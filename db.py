"""
IC Events Approval Hub — database layer.

Pure Python standard library (sqlite3). No external dependencies.
"""

import os
import sqlite3
import hashlib
import secrets
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "ic_hub.db")

ROLE_IC = "ic_user"
ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"
ROLES = (ROLE_IC, ROLE_MANAGER, ROLE_ADMIN)

# ---------------------------------------------------------------- statuses
ST_DRAFT = "draft"
ST_PENDING = "pending_approval"
ST_PENDING_VENDORS = "pending_vendor_approval"
ST_PARTIAL = "partially_approved"
ST_FULL = "fully_approved"
ST_REJECTED = "rejected"
ST_CANCELLED = "cancelled"
ST_COMPLETED = "completed"

EVENT_STATUSES = (
    ST_DRAFT, ST_PENDING, ST_PENDING_VENDORS, ST_PARTIAL,
    ST_FULL, ST_REJECTED, ST_CANCELLED, ST_COMPLETED,
)

VS_PENDING = "pending"
VS_APPROVED = "approved"
VS_REJECTED = "rejected"
VS_NOT_SELECTED = "not_selected"     # belongs to an option the approver did not pick

OPT_PENDING = "pending"
OPT_SELECTED = "selected"
OPT_NOT_SELECTED = "not_selected"


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")


# ---------------------------------------------------------------- schema
SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL,
    department    TEXT,
    job_title     TEXT,
    phone         TEXT,
    invited       INTEGER NOT NULL DEFAULT 0,   -- added ad-hoc as an approver; has no password
    can_view_archive INTEGER NOT NULL DEFAULT 0, -- read-only access to the completed-events history
    passwordless  INTEGER NOT NULL DEFAULT 1,   -- 1 = signs in with name + e-mail only
    approver_level INTEGER,                      -- 1 = 1st approver, 2 = 2nd, 3 = 3rd ...
    is_final_approver INTEGER NOT NULL DEFAULT 0, -- exactly one person holds the final say
    manager_id    INTEGER REFERENCES users(id),
    can_view_all  INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token          TEXT PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope_event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,  -- set for review links
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);

-- Once an approver has confirmed their identity on a review link, this remembers
-- their browser so later links open straight away without asking again.
CREATE TABLE IF NOT EXISTS login_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT    NOT NULL,
    code_hash   TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    used        INTEGER NOT NULL DEFAULT 0,
    requested_ip TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_codes_email ON login_codes(email);

CREATE TABLE IF NOT EXISTS known_devices (
    token        TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name_used    TEXT,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    expires_at   TEXT NOT NULL
);

-- One-time review links: the manager opens the approval page straight from the
-- e-mail without a password. The token is the secret; the manager confirms their
-- name and e-mail on arrival, and the resulting session can reach ONLY that event.
CREATE TABLE IF NOT EXISTS approval_links (
    token          TEXT PRIMARY KEY,
    event_id       INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    approver_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at     TEXT NOT NULL,
    revoked        INTEGER NOT NULL DEFAULT 0,
    opened_at      TEXT,
    opened_name    TEXT,
    opened_email   TEXT,
    open_count     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_number        TEXT    NOT NULL UNIQUE,
    event_name          TEXT    NOT NULL,
    event_type          TEXT,
    event_date          TEXT,
    start_time          TEXT,
    end_time            TEXT,
    location            TEXT,
    expected_attendees  INTEGER,
    description         TEXT,
    miscellaneous_cost  REAL    NOT NULL DEFAULT 0,
    total_budget        REAL    NOT NULL DEFAULT 0,
    approved_budget     REAL    NOT NULL DEFAULT 0,
    rejected_budget     REAL    NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL DEFAULT 'draft',
    created_by          INTEGER NOT NULL REFERENCES users(id),
    approver_id         INTEGER REFERENCES users(id),
    submitted_at        TEXT,
    decided_at          TEXT,
    executed            INTEGER NOT NULL DEFAULT 0,
    execution_date      TEXT,
    execution_notes     TEXT,
    selected_option_id  INTEGER,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

-- An event can be sent to several approvers at once. All of them receive the
-- approval e-mail; whichever one submits a decision first records it.
-- events.approver_id stays as the primary approver (the first in the list).
-- Approval runs as a chain: level 1 decides first, then level 2, then level 3 ...
-- `level` is copied from the user when they are assigned, so re-ranking a person
-- later never rewrites the history of a request already in flight.
CREATE TABLE IF NOT EXISTS event_approvers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    level      INTEGER NOT NULL DEFAULT 1,
    status     TEXT    NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|skipped
    is_final   INTEGER NOT NULL DEFAULT 0,          -- this person's approval closes the request
    decided_at TEXT,
    sort       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    UNIQUE(event_id, user_id)
);

-- A single event can carry several alternative vendor packages ("Option A" with
-- vendors A+B+C, "Option B" with C+D+E ...). Each option has its own total and
-- the approver picks exactly one. Every event always has at least one option.
CREATE TABLE IF NOT EXISTS event_options (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT,
    sort        INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'pending',   -- pending | selected | not_selected
    vendor_cost REAL    NOT NULL DEFAULT 0,
    total       REAL    NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS vendors (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    option_id         INTEGER REFERENCES event_options(id) ON DELETE CASCADE,
    vendor_name       TEXT    NOT NULL,
    category          TEXT,
    contact_name      TEXT,
    contact_email     TEXT,
    contact_phone     TEXT,
    description       TEXT,
    quotation_amount  REAL    NOT NULL DEFAULT 0,
    vat_rate          REAL    NOT NULL DEFAULT 15,
    vat               REAL    NOT NULL DEFAULT 0,
    total_amount      REAL    NOT NULL DEFAULT 0,
    approval_status   TEXT    NOT NULL DEFAULT 'pending',
    rejection_reason  TEXT,
    quotation_file    TEXT,
    quotation_name    TEXT,
    quotation_mime    TEXT,
    quotation_size    INTEGER,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

-- Extra files that belong to a vendor: the quotation itself plus any number of
-- sample images, portfolio documents, profiles ...
CREATE TABLE IF NOT EXISTS vendor_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id  INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    event_id   INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL DEFAULT 'attachment',  -- 'quotation' | 'image' | 'attachment'
    file_path  TEXT    NOT NULL,
    file_name  TEXT    NOT NULL,
    mime       TEXT,
    size       INTEGER,
    caption    TEXT,
    created_at TEXT    NOT NULL
);

-- Pictures of the event itself. Deliberately NOT tied to a vendor and NOT locked
-- when the event is approved: photos are taken after the event has happened.
CREATE TABLE IF NOT EXISTS event_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL DEFAULT 'photo',   -- 'photo' | 'announcement'
    file_path   TEXT    NOT NULL,
    file_name   TEXT    NOT NULL,
    mime        TEXT,
    size        INTEGER,
    caption     TEXT,
    uploaded_by INTEGER REFERENCES users(id),
    created_at  TEXT    NOT NULL
);

-- Reference links for a vendor (website, portfolio, Instagram, drive folder ...)
CREATE TABLE IF NOT EXISTS vendor_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id  INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    event_id   INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    label      TEXT,
    url        TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id               INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    approver_id            INTEGER NOT NULL REFERENCES users(id),
    event_decision         TEXT,
    event_rejection_reason TEXT,
    decision_date          TEXT,
    created_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vendor_approvals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id        INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    event_id         INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    approver_id      INTEGER NOT NULL REFERENCES users(id),
    decision         TEXT    NOT NULL,
    rejection_reason TEXT,
    decision_date    TEXT,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id   INTEGER REFERENCES events(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,
    title      TEXT NOT NULL,
    message    TEXT,
    read       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    action          TEXT NOT NULL,
    performed_by    INTEGER REFERENCES users(id),
    role            TEXT,
    comments        TEXT,
    previous_status TEXT,
    new_status      TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emails (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER REFERENCES events(id) ON DELETE CASCADE,
    to_email   TEXT NOT NULL,
    to_name    TEXT,
    cc         TEXT,
    subject    TEXT NOT NULL,
    body_html  TEXT NOT NULL,
    body_text  TEXT,
    type       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'queued',
    error      TEXT,
    sent_at    TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lookups (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL,          -- 'event_type' | 'vendor_category'
    value    TEXT NOT NULL,
    sort     INTEGER NOT NULL DEFAULT 0,
    active   INTEGER NOT NULL DEFAULT 1,
    UNIQUE(kind, value)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_creator  ON events(created_by);
CREATE INDEX IF NOT EXISTS idx_events_approver ON events(approver_id);
CREATE INDEX IF NOT EXISTS idx_vendors_event   ON vendors(event_id);
CREATE INDEX IF NOT EXISTS idx_options_event   ON event_options(event_id);
CREATE INDEX IF NOT EXISTS idx_vendors_option  ON vendors(option_id);
CREATE INDEX IF NOT EXISTS idx_vfiles_vendor   ON vendor_files(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vlinks_vendor   ON vendor_links(vendor_id);
CREATE INDEX IF NOT EXISTS idx_ephotos_event   ON event_photos(event_id);
CREATE INDEX IF NOT EXISTS idx_notif_user      ON notifications(user_id, read);
CREATE INDEX IF NOT EXISTS idx_hist_event      ON approval_history(event_id);
"""

DEFAULT_EVENT_TYPES = [
    "Internal Event", "Employee Engagement", "Celebration", "Awareness",
    "Workshop", "Team Activity", "Campaign", "Other",
]
DEFAULT_VENDOR_CATEGORIES = [
    "Catering", "Event Management", "Decoration", "Entertainment",
    "Photography", "Gifts", "Printing", "Transportation", "Venue", "Other",
]

# Who signs off a monthly recap or a quarterly update. Held as data rather than written
# into the code, because the answer has already changed once and will change again: the
# people are named on the form, and this only decides which boxes start ticked.
RECORD_APPROVER_SETTING = "record_approver_ids"

DEFAULT_SETTINGS = {
    RECORD_APPROVER_SETTING: "",
    "org_name": "KABi",
    "app_name": "IC Events Approval Hub",
    "currency": "SAR",
    "default_vat_rate": "15",
    "app_base_url": "http://localhost:8080",
    # E-mail is DELIVERY-DISABLED by default: every message is composed and
    # stored in the outbox, nothing leaves the building until an admin
    # explicitly turns SMTP on and fills in the credentials.
    "smtp_enabled": "0",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_tls": "1",
    "mail_from": "ic-hub@kabi.ai",
    "mail_from_name": "KABi IC Events Approval Hub",
}


# ---------------------------------------------------------------- helpers
def load_env_file(path=None):
    """Read a local .env so nobody has to set shell variables by hand.

    Values already present in the real environment win, so Vercel's encrypted
    variables are never overridden by a stray file.
    """
    path = path or os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and value and key not in os.environ:
                    os.environ[key] = value
        return True
    except OSError:
        return False


load_env_file()


def using_postgres():
    """Hosted (Supabase) when a connection string is configured, else local SQLite."""
    return bool(os.environ.get("IC_DATABASE_URL") or os.environ.get("DATABASE_URL"))


def connect():
    if using_postgres():
        import pgdb
        return pgdb.connect()
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return "pbkdf2$%s$%s" % (salt, dk.hex())


def verify_password(password, stored):
    try:
        _, salt, _hex = stored.split("$", 2)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else (DEFAULT_SETTINGS.get(key, default))


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


# Columns added after the first release. Both stores are brought up to this list on
# start-up, so an existing database keeps working without anyone running SQL by hand.
# The declarations are deliberately valid in both SQLite and Postgres.
ADDITIVE_TABLES = {
    # Sign-in codes arrived after the first release; an existing store needs the table.
    "login_codes": """CREATE TABLE IF NOT EXISTS login_codes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        email       TEXT    NOT NULL,
        code_hash   TEXT    NOT NULL,
        expires_at  TEXT    NOT NULL,
        attempts    INTEGER NOT NULL DEFAULT 0,
        used        INTEGER NOT NULL DEFAULT 0,
        requested_ip TEXT,
        created_at  TEXT    NOT NULL
    )""",
    # Set-up links, from the point where IC stopped issuing passwords. A person is sent
    # one of these and chooses their own, so nobody else ever knows it -- not the
    # administrator, not the hub. Only the digest of the link is kept.
    "password_setups": """CREATE TABLE IF NOT EXISTS password_setups (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        token_digest TEXT    NOT NULL,
        user_id      INTEGER NOT NULL,
        expires_at   TEXT    NOT NULL,
        used         INTEGER NOT NULL DEFAULT 0,
        used_at      TEXT,
        used_ip      TEXT,
        created_by   INTEGER,
        created_at   TEXT    NOT NULL
    )""",
}

ADDITIVE_COLUMNS = {
        "events": [
            # What kind of record this is. Activities are the events the hub runs;
            # recaps and quarterly updates are things IC published, kept in the same
            # history so the calendar shows the whole communication year in one place.
            ("record_kind", "TEXT NOT NULL DEFAULT 'activity'"),
            # Who a recap or update went out to. Activities have a location instead.
            ("audience", "TEXT"),
            ("executed", "INTEGER NOT NULL DEFAULT 0"),
            ("execution_date", "TEXT"),
            ("execution_notes", "TEXT"),
            ("selected_option_id", "INTEGER"),
            ("current_level", "INTEGER"),
            ("rejected_budget", "REAL NOT NULL DEFAULT 0"),
            ("decided_at", "TEXT"),
        ],
        "users": [("phone", "TEXT"), ("job_title", "TEXT"), ("can_view_all", "INTEGER NOT NULL DEFAULT 0"),
                  ("invited", "INTEGER NOT NULL DEFAULT 0"),
                  ("can_view_archive", "INTEGER NOT NULL DEFAULT 0"),
                  ("passwordless", "INTEGER NOT NULL DEFAULT 1"),
                  ("approver_level", "INTEGER"),
                  ("is_final_approver", "INTEGER NOT NULL DEFAULT 0")],
        "sessions": [("scope_event_id", "INTEGER")],
        "emails": [("body_text", "TEXT"), ("cc", "TEXT")],
        "event_approvers": [("level", "INTEGER NOT NULL DEFAULT 1"),
                            ("status", "TEXT NOT NULL DEFAULT 'pending'"),
                            ("is_final", "INTEGER NOT NULL DEFAULT 0"),
                            ("decided_at", "TEXT"),
                            # Which package this particular approver chose. The event
                            # carries the standing choice; this is the per-person record,
                            # so a later approver can see what the earlier one picked and
                            # the history says who changed it.
                            ("selected_option_id", "INTEGER"),
                            # What this approver approved, as a list. The single column
                            # above keeps the first of them so everything that reads one
                            # id still works; this is the answer when they took two.
                            ("selected_option_ids", "TEXT")],
        "event_photos": [("kind", "TEXT NOT NULL DEFAULT 'photo'")],
        # What a quotation is actually made of. Recording only the sum loses the two
        # numbers people negotiate and compare -- "40 gifts at 8.60" says something
        # "344.00" does not. quotation_amount stays as the line's net total.
        # Delivery belongs to the package, not to the event: one option may be collected
        # and another delivered, and a single event-level "miscellaneous cost" could not
        # express that -- it was added to every option identically.
        "event_options": [("delivery_cost", "REAL NOT NULL DEFAULT 0")],
        "vendors": [("unit_price", "REAL"),
                    ("quantity", "REAL NOT NULL DEFAULT 1"),
                    ("vat_rate", "REAL NOT NULL DEFAULT 15"), ("quotation_name", "TEXT"),
                    ("quotation_mime", "TEXT"), ("quotation_size", "INTEGER"),
                    ("option_id", "INTEGER")],
}


def _migrate_pg(conn):
    """Bring the hosted schema up to ADDITIVE_COLUMNS.

    The Postgres tables are created once from supabase/0001_ic_events.sql, so without
    this a column added later would be missing on the hosted store and every query
    touching it would fail. information_schema stands in for SQLite's PRAGMA.
    """
    import pgdb
    for name, ddl in ADDITIVE_TABLES.items():
        # the sqlite DDL is close enough to translate by hand for these
        try:
            conn.execute(ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                                     "integer generated always as identity primary key"))
            conn.commit()
        except Exception as exc:            # noqa: BLE001
            # One table this role may not create must not stop the others from being
            # tried. login_codes went missing on the hosted store exactly this way, and
            # because the failure was silent it surfaced much later as an unexplained
            # error on a sign-in page. The health check reports missing tables now.
            conn.rollback()
            print("  migration: could not create %s: %s"
                  % (name, str(exc).splitlines()[0][:160]))
    for table, cols in ADDITIVE_COLUMNS.items():
        have = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=? AND table_name=?", (pgdb.SCHEMA, table))}
        if not have:
            continue                      # table absent: the schema script has not run
        for name, decl in cols:
            if name not in have:
                conn.execute("ALTER TABLE %s ADD COLUMN IF NOT EXISTS %s %s" % (table, name, decl))
                # each one stands on its own, so a later failure cannot undo this one
                conn.commit()


def _migrate(conn):
    """Additive migrations so an existing database keeps working."""
    for ddl in ADDITIVE_TABLES.values():
        conn.execute(ddl)
    for table, cols in ADDITIVE_COLUMNS.items():
        try:
            have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        except sqlite3.Error:
            continue
        if not have:
            continue
        for name, decl in cols:
            if name not in have:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl))

    # Accounts that already have a real password keep needing it (this protects the
    # admin account); the column default of 1 must not silently open them up.
    have_users = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "passwordless" in have_users:
        # An administrator always has a password. Everyone else is left alone: whether
        # an account holds one is a decision the administrator made, and a migration
        # that runs on every start must not keep overruling it. This line used to force
        # approvers back to code-only, so an approver given a password could sign in
        # once and then silently could not after the next restart. Their request link
        # and the one-time code are unaffected either way.
        conn.execute("UPDATE users SET passwordless=0 WHERE role='admin'")
        # existing approvers default to 1st-level until the admin says otherwise
        conn.execute("UPDATE users SET approver_level=1 "
                     "WHERE role='manager' AND approver_level IS NULL")

    # Seed the recap sign-off list once, from the people who do it today. Matching on
    # the name is only good enough for a seed -- after this it is an id list in the
    # settings table, which is why it is set only when it has never been set.
    if not get_setting(conn, RECORD_APPROVER_SETTING, ""):
        seed = [str(r["id"]) for r in conn.execute(
            "SELECT id FROM users WHERE active=1 AND role='manager' AND ("
            "  name LIKE 'Mashael%' OR name LIKE 'Sahabah%') ORDER BY name")]
        if seed:
            set_setting(conn, RECORD_APPROVER_SETTING, ",".join(seed))

    # A quotation recorded before there were units is one of something at its own price.
    try:
        conn.execute("UPDATE vendors SET unit_price=quotation_amount, quantity=1 "
                     "WHERE unit_price IS NULL")
    except Exception:                       # noqa: BLE001 - the column may not exist yet
        pass

    # Miscellaneous cost was added to every option identically, so copying it into each
    # option's delivery leaves every total exactly where it was. Done once: the guard is
    # delivery_cost being untouched.
    try:
        conn.execute(
            "UPDATE event_options SET delivery_cost=("
            "  SELECT COALESCE(e.miscellaneous_cost,0) FROM events e WHERE e.id=event_id) "
            "WHERE COALESCE(delivery_cost,0)=0 AND ("
            "  SELECT COALESCE(e.miscellaneous_cost,0) FROM events e WHERE e.id=event_id) > 0")
    except Exception:                       # noqa: BLE001
        pass

    # Back-fill the approver list from the single approver_id column.
    ts = now_iso()
    for row in conn.execute("SELECT id, approver_id FROM events WHERE approver_id IS NOT NULL").fetchall():
        conn.execute("INSERT OR IGNORE INTO event_approvers(event_id,user_id,sort,created_at) "
                     "VALUES(?,?,0,?)", (row["id"], row["approver_id"], ts))

    # keep each assignment's level in step with the person's rank, and start any
    # in-flight request at its lowest level
    try:
        conn.execute("UPDATE event_approvers SET level=COALESCE("
                     "(SELECT COALESCE(approver_level,1) FROM users WHERE users.id=event_approvers.user_id),1) "
                     "WHERE level IS NULL OR level=0")
        conn.execute("UPDATE events SET current_level="
                     "(SELECT MIN(level) FROM event_approvers WHERE event_id=events.id) "
                     "WHERE current_level IS NULL AND status IN ('pending_approval','pending_vendor_approval')")
        conn.execute("UPDATE users SET is_final_approver=0 WHERE role<>'manager'")
        if not conn.execute("SELECT 1 FROM users WHERE is_final_approver=1").fetchone():
            top = conn.execute("SELECT id FROM users WHERE role='manager' AND active=1 "
                               "ORDER BY COALESCE(approver_level,1) DESC, id LIMIT 1").fetchone()
            if top:
                conn.execute("UPDATE users SET is_final_approver=1 WHERE id=?", (top["id"],))
        conn.execute("UPDATE event_approvers SET is_final=COALESCE("
                     "(SELECT is_final_approver FROM users WHERE users.id=event_approvers.user_id),0) "
                     "WHERE is_final=0")
    except sqlite3.Error:
        pass

    # Every event needs at least one option; older events get "Option A" holding
    # all of their existing vendors.
    for row in conn.execute("SELECT id FROM events").fetchall():
        has = conn.execute("SELECT id FROM event_options WHERE event_id=? ORDER BY sort,id LIMIT 1",
                           (row["id"],)).fetchone()
        if has:
            opt_id = has["id"]
        else:
            opt_id = conn.execute(
                "INSERT INTO event_options(event_id,name,sort,status,created_at,updated_at) "
                "VALUES(?,?,0,'pending',?,?)", (row["id"], "Option A", ts, ts)).lastrowid
        conn.execute("UPDATE vendors SET option_id=? WHERE event_id=? AND option_id IS NULL",
                     (opt_id, row["id"]))


def next_event_number(conn, year=None):
    year = year or datetime.date.today().year
    prefix = "IC-%d-" % year
    row = conn.execute(
        "SELECT event_number FROM events WHERE event_number LIKE ? "
        "ORDER BY event_number DESC LIMIT 1", (prefix + "%",)
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row["event_number"].rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return "%s%03d" % (prefix, seq)


# ---------------------------------------------------------------- bootstrap
def expected_tables():
    """Every table this application needs, whichever store it is running on."""
    import re
    return sorted(set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))
                  | set(ADDITIVE_TABLES))


def unqualified_tables():
    """Tables this application defines that would NOT be rewritten to our own schema.

    On Postgres every table name in a query is rewritten to `ic_events.<name>` before it
    is sent. The connection's search_path still ends in `public`, so a table that the
    rewriter does not know about would silently resolve there instead -- reading, and
    worse writing, inside the HC Report Hub's schema. Nothing is wrong today; this exists
    so that adding a table and forgetting to register it is visible in the health check
    rather than discovered as mixed data months later.
    """
    import re
    import pgdb
    defined = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)) | set(ADDITIVE_TABLES)
    return sorted(defined - set(pgdb._TABLES))


def init_db(seed_demo=True):
    """Create/upgrade the store and seed the first accounts.

    On Postgres the tables come from supabase/0001_ic_events.sql (run once in the
    SQL editor), so we skip the SQLite DDL and only seed + settings here.
    """
    if using_postgres():
        conn = connect()
        # Postgres keeps DDL inside the transaction, so a failure anywhere below used to
        # roll the schema change back with it -- the column would be added, something
        # later would fail, and the store would come back none the wiser. Commit the
        # migration on its own: it must not depend on what follows.
        _migrate_pg(conn)
        conn.commit()
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        for i, v in enumerate(DEFAULT_EVENT_TYPES):
            conn.execute("INSERT OR IGNORE INTO lookups(kind,value,sort) VALUES('event_type',?,?)", (v, i))
        for i, v in enumerate(DEFAULT_VENDOR_CATEGORIES):
            conn.execute("INSERT OR IGNORE INTO lookups(kind,value,sort) VALUES('vendor_category',?,?)", (v, i))
        if not conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] and seed_demo:
            _seed_users(conn)
        conn.commit()
        conn.close()
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)

    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))

    for i, v in enumerate(DEFAULT_EVENT_TYPES):
        conn.execute("INSERT OR IGNORE INTO lookups(kind,value,sort) VALUES('event_type',?,?)", (v, i))
    for i, v in enumerate(DEFAULT_VENDOR_CATEGORIES):
        conn.execute("INSERT OR IGNORE INTO lookups(kind,value,sort) VALUES('vendor_category',?,?)", (v, i))

    has_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    if not has_users and seed_demo:
        _seed_users(conn)

    conn.commit()
    conn.close()


# Only the IC administrator signs in with a password; everyone else uses name + e-mail.
# The password a brand-new installation starts with. Set IC_ADMIN_PASSWORD before the
# first run and nothing weak ever reaches the database; leave it unset and this is used,
# which is fine on an office network and not fine on a public address -- so the hub says
# so, loudly, until it is changed.
WEAK_DEFAULT_PASSWORD = "IC@kabi2026"
ADMIN_PASSWORD = os.environ.get("IC_ADMIN_PASSWORD") or WEAK_DEFAULT_PASSWORD


def using_weak_default(stored_hash):
    """True while the administrator still has the documented starter password."""
    return bool(stored_hash) and verify_password(WEAK_DEFAULT_PASSWORD, stored_hash)
DEMO_PASSWORD = ADMIN_PASSWORD      # kept for older call sites

SEED_USERS = [
    # name, email, role, department, job title
    ("Internal Communication", "ic@kabi.ai",     ROLE_ADMIN,   "Internal Communication", "IC Administrator"),
    ("Njood Aloraij",        "naloraij@kabi.ai", ROLE_IC,      "Internal Communication", "Internal Communication Specialist"),
    ("Sara Al-Harbi",        "sara.ic@kabi.ai",  ROLE_IC,      "Internal Communication", "Internal Communication Officer"),
    ("Mohammed Al-Otaibi",   "m.manager@kabi.ai", ROLE_MANAGER, "Human Capital",         "Human Capital Director"),
    ("Layla Al-Qahtani",     "l.manager@kabi.ai", ROLE_MANAGER, "Corporate Services",    "Corporate Communication Manager"),
]


def _seed_users(conn):
    ts = now_iso()
    ids = {}
    for name, email, role, dept, title in SEED_USERS:
        is_admin = role == ROLE_ADMIN
        cur = conn.execute(
            "INSERT INTO users(name,email,password_hash,role,department,job_title,"
            "passwordless,approver_level,active,created_at) VALUES(?,?,?,?,?,?,?,?,1,?)",
            (name, email,
             hash_password(ADMIN_PASSWORD if is_admin else secrets.token_urlsafe(32)),
             role, dept, title,
             0 if is_admin else 1,
             1 if role == ROLE_MANAGER else None, ts),
        )
        ids[email] = cur.lastrowid
    # IC users report to the HC director by default
    conn.execute("UPDATE users SET manager_id=? WHERE email IN ('naloraij@kabi.ai','sara.ic@kabi.ai')",
                 (ids["m.manager@kabi.ai"],))


if __name__ == "__main__":
    init_db()
    print("Database ready at", DB_PATH)
