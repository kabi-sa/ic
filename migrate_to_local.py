"""Copy everything out of the hosted hub into a local database file.

Run once, when moving the hub off cloud hosting and onto a machine of your own. It reads
the hosted deployment through its own API — accounts, settings, the drop-down lists, every
event with its options, vendors, approval chain, history, pictures and announcement files,
including the file contents — and writes a fresh `data/ic_hub.db` alongside the picture
files in `uploads/`.

Nothing is deleted. Any existing local database is renamed with a timestamp first, so a
mistake here costs nothing but disk space.

    python migrate_to_local.py https://ic-beta.vercel.app

Passwords are deliberately not readable through the API. The administrator account is
recreated with the standard IC password; everyone else signs in with name and e-mail, so
they need no password at all.
"""

import base64
import datetime
import http.cookiejar
import json
import os
import secrets
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request

import db

ADMIN_EMAIL = "ic@kabi.ai"
ADMIN_NAME = "Internal Communication"
ADMIN_PASSWORD = os.environ.get("IC_ADMIN_PASSWORD", db.ADMIN_PASSWORD)


class Remote:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-Requested-With", "ic-hub")
        req.add_header("X-IC-Path", path)          # survives platform path rewriting
        if data:
            req.add_header("Content-Type", "application/json")
        return self.op.open(req, timeout=180)

    def get(self, path):
        try:
            return json.loads(self._open(path).read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise SystemExit("%s -> %s %s" % (path, exc.code, exc.read()[:200]))

    def blob(self, path):
        try:
            return self._open(path).read()
        except urllib.error.HTTPError:
            return None

    def sign_in(self):
        try:
            self._open("/api/auth/login", "POST",
                       {"name": ADMIN_NAME, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        except urllib.error.HTTPError as exc:
            raise SystemExit("Could not sign in to %s: %s" % (self.base, exc.read()[:200]))


def columns(conn, table):
    return [r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def insert(conn, table, row, cols=None):
    """Insert whatever of `row` this table actually has a column for."""
    cols = cols or columns(conn, table)
    use = [c for c in cols if c in row and row[c] is not None]
    if not use:
        return None
    marks = ",".join("?" * len(use))
    cur = conn.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ",".join(use), marks),
                       tuple(row[c] for c in use))
    return cur.lastrowid


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python migrate_to_local.py https://your-hosted-hub")
    remote = Remote(sys.argv[1])
    remote.sign_in()
    print("reading %s\n" % remote.base)

    users = remote.get("/api/users").get("users", [])
    settings = remote.get("/api/admin/settings").get("settings", {})
    lookups = remote.get("/api/lookups")
    index = remote.get("/api/events").get("events", [])
    print("found %d accounts, %d events, %d settings"
          % (len(users), len(index), len(settings)))

    details = []
    for i, item in enumerate(index, 1):
        details.append(remote.get("/api/events/%d" % item["id"]).get("event", {}))
        print("\r  reading event %d/%d" % (i, len(index)), end="", flush=True)
    print()

    # ---- a fresh database beside the old one, which is kept
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if os.path.exists(db.DB_PATH):
        keep = "%s.before-migration-%s" % (db.DB_PATH, stamp)
        shutil.copy2(db.DB_PATH, keep)
        print("\nexisting database kept as %s" % os.path.basename(keep))
        os.remove(db.DB_PATH)

    db.init_db(seed_demo=False)
    conn = db.connect()
    ts = db.now_iso()

    # ---- settings and the drop-downs
    for key, value in settings.items():
        # the API masks the SMTP password; storing the mask would be worse than blank
        if key == "smtp_password" and set(str(value or "")) <= {"*"}:
            continue
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                     (key, "" if value is None else str(value)))
    conn.execute("DELETE FROM lookups")
    for i, v in enumerate(lookups.get("event_types") or []):
        conn.execute("INSERT INTO lookups(kind,value,sort,active) VALUES('event_type',?,?,1)", (v, i))
    for i, v in enumerate(lookups.get("vendor_categories") or []):
        conn.execute("INSERT INTO lookups(kind,value,sort,active) VALUES('vendor_category',?,?,1)", (v, i))

    # ---- accounts. Password hashes are not exposed by the API, by design: the
    # administrator is recreated with the standard password, everyone else is
    # passwordless and signs in with their name and e-mail.
    user_id = {}
    ucols = columns(conn, "users")
    for u in users:
        is_admin = u.get("role") == db.ROLE_ADMIN
        row = dict(u)
        row["password_hash"] = db.hash_password(
            ADMIN_PASSWORD if is_admin else secrets.token_urlsafe(32))
        row["passwordless"] = 0 if is_admin else 1
        row["active"] = 1 if u.get("active", True) else 0
        row["created_at"] = u.get("created_at") or ts
        for flag in ("can_view_all", "can_view_archive", "invited", "is_final_approver"):
            row[flag] = 1 if u.get(flag) else 0
        row.pop("id", None)
        user_id[u["email"].lower()] = insert(conn, "users", row, ucols)
    print("accounts written: %d" % len(user_id))

    def uid(email):
        return user_id.get((email or "").lower())

    # the history and approval records name a person rather than giving an address
    by_name = {u.get("name"): uid(u.get("email")) for u in users}

    def who(name):
        return by_name.get(name)

    # ---- events and everything hanging off them
    photos = files = 0
    os.makedirs(db.UPLOAD_DIR, exist_ok=True)
    for e in details:
        row = dict(e)
        row["created_by"] = uid(e.get("creator_email")) or uid(ADMIN_EMAIL)
        row.pop("id", None)
        row.pop("approver_id", None)
        row["created_at"] = e.get("created_at") or ts
        row["updated_at"] = e.get("updated_at") or ts
        eid = insert(conn, "events", row)

        # options, then the vendors that belong to each
        option_id = {}
        for o in e.get("options") or []:
            orow = dict(o)
            orow["event_id"] = eid
            orow.pop("id", None)
            option_id[o["id"]] = insert(conn, "event_options", orow)
            for v in o.get("vendors") or []:
                vrow = dict(v)
                vrow["event_id"] = eid
                vrow["option_id"] = option_id[o["id"]]
                vrow.pop("id", None)
                vid = insert(conn, "vendors", vrow)
                for f in v.get("files") or []:
                    raw = remote.blob("/api/vendor-files/%d/content" % f["id"])
                    if not raw:
                        continue
                    stored = "%s%s" % (secrets.token_hex(16),
                                       os.path.splitext(f.get("file_name") or "")[1].lower())
                    with open(os.path.join(db.UPLOAD_DIR, stored), "wb") as fh:
                        fh.write(raw)
                    frow = dict(f)
                    frow.update({"vendor_id": vid, "event_id": eid, "file_path": stored})
                    frow.pop("id", None)
                    insert(conn, "vendor_files", frow)
                    files += 1
                for lk in v.get("links") or []:
                    insert(conn, "vendor_links", {
                        "vendor_id": vid, "event_id": eid, "label": lk.get("label"),
                        "url": lk.get("url"), "created_at": ts})

        if e.get("selected_option_id") in option_id:
            conn.execute("UPDATE events SET selected_option_id=? WHERE id=?",
                         (option_id[e["selected_option_id"]], eid))

        # the approval chain, exactly as it stands
        for i, a in enumerate(e.get("approvers") or []):
            who = uid(a.get("email"))
            if not who:
                continue
            insert(conn, "event_approvers", {
                "event_id": eid, "user_id": who, "level": a.get("level") or 1,
                "status": a.get("status") or "pending",
                "is_final": 1 if a.get("is_final") else 0,
                "decided_at": a.get("decided_at"), "sort": a.get("sort", i),
                "created_at": ts})

        # pictures and the announcement that went to employees
        for p in (e.get("photos") or []) + (e.get("announcements") or []):
            raw = remote.blob("/api/event-photos/%d/content" % p["id"])
            if not raw:
                continue
            stored = "%s%s" % (secrets.token_hex(16),
                               os.path.splitext(p.get("file_name") or "")[1].lower())
            with open(os.path.join(db.UPLOAD_DIR, stored), "wb") as fh:
                fh.write(raw)
            prow = dict(p)
            prow.update({"event_id": eid, "file_path": stored,
                         "uploaded_by": row["created_by"], "size": len(raw)})
            prow.pop("id", None)
            insert(conn, "event_photos", prow)
            photos += 1

        # the paper trail
        for h in e.get("history") or []:
            hrow = dict(h)
            hrow["event_id"] = eid
            hrow["performed_by"] = who(h.get("performer_name")) or row["created_by"]
            hrow.pop("id", None)
            insert(conn, "approval_history", hrow)
        for a in e.get("approvals") or []:
            arow = dict(a)
            arow["event_id"] = eid
            arow["approver_id"] = who(a.get("approver_name"))
            arow.pop("id", None)
            insert(conn, "approvals", arow)

        print("  %-11s %-46s %s" % (e.get("event_number"),
                                    (e.get("event_name") or "")[:46], e.get("status")))

    conn.commit()

    got = lambda q: conn.execute(q).fetchone()[0]
    print("\nlocal database written: %s" % db.DB_PATH)
    print("  accounts %d | events %d | options %d | vendors %d"
          % (got("SELECT COUNT(*) FROM users"), got("SELECT COUNT(*) FROM events"),
             got("SELECT COUNT(*) FROM event_options"), got("SELECT COUNT(*) FROM vendors")))
    print("  pictures and announcements %d | vendor files %d | chain rows %d"
          % (photos, files, got("SELECT COUNT(*) FROM event_approvers")))
    conn.close()


if __name__ == "__main__":
    main()
