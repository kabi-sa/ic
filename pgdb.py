"""
Postgres adapter for the IC Events Approval Hub.

The application was written against sqlite3. Rather than rewrite every query,
this presents the same tiny surface the app already uses — `conn.execute(sql,
params)` returning rows you can index by column name, plus `.lastrowid` — and
translates on the way through:

    ?                -> %s
    INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
    lastrowid        -> RETURNING id
    unqualified name -> ic_events.<table>   (via a search_path startup option)

Everything lives in the `ic_events` schema, which is not exposed to the Supabase
API — so the HC Report Hub's anon key cannot reach it. See supabase/0001_ic_events.sql.
"""

import os
import re
import threading

import psycopg
from psycopg.rows import dict_row

SCHEMA = os.environ.get("IC_DB_SCHEMA", "ic_events")

# Every table the application owns. Names are rewritten to schema-qualified form on
# the way through, so a query never depends on the connection's search_path -- which a
# transaction-mode pooler is free to discard between transactions. The failure this
# avoids is 'relation "sessions" does not exist' on an otherwise healthy database.
_TABLES = (
    "approval_history", "approval_links", "approvals", "emails", "event_approvers",
    "event_options", "event_photos", "events", "known_devices", "lookups",
    "login_codes", "notifications", "password_setups", "sessions", "settings", "users",
    "vendor_approvals",
    "vendor_files", "vendor_links", "vendors",
)

# Only after one of these keywords is a bare word a table reference, and only a name
# on the list above is rewritten -- so information_schema.columns, column names that
# happen to match, and subqueries are all left alone.
_QUALIFY = re.compile(
    r"\b(from|join|into|update|table)\s+((?:if\s+not\s+exists\s+)?)(%s)\b"
    % "|".join(_TABLES), re.I)


def _qualify(sql):
    return _QUALIFY.sub(
        lambda m: "%s %s%s.%s" % (m.group(1), m.group(2), SCHEMA, m.group(3)), sql)


# statements that need an id back, so `.lastrowid` keeps working
_INSERT = re.compile(r"^\s*insert\s+(?:or\s+ignore\s+)?into\s+([a-z_\.\"]+)", re.I)
_RETURNING = re.compile(r"\breturning\b", re.I)
_OR_IGNORE = re.compile(r"^\s*insert\s+or\s+ignore\s+into", re.I)
_TABLES_WITHOUT_ID = {"settings", "sessions", "known_devices", "approval_links"}

_pool_lock = threading.Lock()
_pool = []
POOL_MAX = int(os.environ.get("IC_DB_POOL", "4"))


POOLER_HELP = (
    "Use the Supabase TRANSACTION POOLER string, not the direct connection. "
    "Supabase → Connect → Transaction pooler. It looks like "
    "postgresql://postgres.<project-ref>:<password>"
    "@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require  "
    "The direct string (db.<ref>.supabase.co:5432) resolves to IPv6, which "
    "serverless functions cannot reach."
)


def dsn():
    url = os.environ.get("IC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "IC_DATABASE_URL is not set. " + POOLER_HELP)

    # Catch the two mistakes that produce baffling driver errors on Vercel.
    tail = url.split("@")[-1]
    if "pooler." not in tail and (".supabase.co" in tail or ":5432" in tail):
        raise RuntimeError("This is the direct Supabase connection string. " + POOLER_HELP)
    if "[YOUR-PASSWORD]" in url or "YOUR-PASSWORD" in url:
        raise RuntimeError(
            "The connection string still contains the [YOUR-PASSWORD] placeholder. "
            "Replace it with the real database password (Supabase → Settings → "
            "Database → Reset database password if you don't have it).")
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _translate(sql):
    """sqlite dialect -> postgres."""
    out = _OR_IGNORE.sub("INSERT INTO", sql) if _OR_IGNORE.match(sql) else sql
    ignore = out is not sql
    # ? placeholders -> %s, leaving ?? and quoted text alone
    out = re.sub(r"\?", "%s", out)
    if ignore and not re.search(r"on\s+conflict", out, re.I):
        out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return _qualify(out)


def _wants_id(sql):
    m = _INSERT.match(sql)
    if not m or _RETURNING.search(sql):
        return False
    table = m.group(1).split(".")[-1].strip('"').lower()
    return table not in _TABLES_WITHOUT_ID


class Result:
    """Mimics the slice of sqlite3.Cursor the application relies on."""

    def __init__(self, rows, lastrowid=None, rowcount=0):
        self._rows = rows or []
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)


class Row(dict):
    """dict that also answers row["x"] and row.keys() like sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


class Connection:
    """One pooled connection, configured for a transaction-mode pooler.

    Supabase's pooler hands a different server connection to each transaction, which
    rules out two things psycopg and the app would otherwise rely on:

    * Prepared statements. psycopg starts naming and reusing them after a few
      executions, and the names collide across server connections -- the symptom is
      `DuplicatePreparedStatement: prepared statement "_pg3_3" already exists`.
      `prepare_threshold = None` turns the automation off.
    * `SET search_path`, which is session state and does not survive the switch. It
      travels as a libpq startup option instead, so the pooler applies it to every
      server connection it opens. That also saves a round trip per connection.
    """

    def __init__(self):
        try:
            self._conn = psycopg.connect(
                dsn(), autocommit=False, row_factory=dict_row,
                options="-c search_path=%s,public" % SCHEMA,
            )
        except psycopg.OperationalError:
            # Some poolers reject the `options` startup packet. Queries carry their own
            # schema qualification, so this is a convenience rather than a requirement.
            self._conn = psycopg.connect(dsn(), autocommit=False, row_factory=dict_row)
        self._conn.prepare_threshold = None

    # -- the sqlite-shaped API the app uses -------------------------------
    def execute(self, sql, params=()):
        stmt = _translate(sql)
        want_id = _wants_id(sql)
        if want_id:
            stmt = stmt.rstrip().rstrip(";") + " RETURNING id"
        with self._conn.cursor() as cur:
            cur.execute(stmt, tuple(params) if params else None)
            rows, last = [], None
            if cur.description:
                fetched = cur.fetchall()
                rows = [Row(r) for r in fetched]
                if want_id and rows:
                    last = rows[0].get("id")
                    rows = []          # an INSERT returns no result set to the app
            return Result(rows, last, cur.rowcount)

    def executescript(self, script):
        """Only used for the sqlite bootstrap; Postgres uses the .sql migration."""
        with self._conn.cursor() as cur:
            cur.execute(script)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        # hand the connection back for reuse rather than dropping it: serverless
        # invocations are short and Supabase's pooler charges for churn
        try:
            self._conn.rollback()
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass
            return
        with _pool_lock:
            if len(_pool) < POOL_MAX and not self._conn.closed:
                _pool.append(self)
                return
        try:
            self._conn.close()
        except Exception:
            pass


def connect():
    with _pool_lock:
        while _pool:
            c = _pool.pop()
            if not c._conn.closed:
                return c
    return Connection()


def is_configured():
    return bool(os.environ.get("IC_DATABASE_URL") or os.environ.get("DATABASE_URL"))
