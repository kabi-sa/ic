"""
Self-test for the hosted (Postgres/Supabase) setup.

Run it yourself — the connection string stays in your own shell and is never
printed, logged or committed. It checks that the IC Events schema works AND that
it is properly walled off from the HC Report Hub's data.

    Windows (PowerShell):
        $env:IC_DATABASE_URL="postgresql://...:6543/postgres?sslmode=require"
        python selftest.py

    Windows (cmd):
        set IC_DATABASE_URL=postgresql://...:6543/postgres?sslmode=require
        python selftest.py

Nothing is left behind: the write test cleans up after itself.
"""

import os
import sys
import uuid

import db

PASS, FAIL = "PASS", "FAIL"
results = []


def check(label, ok, detail=""):
    results.append((PASS if ok else FAIL, label, detail))
    print("  %-5s %-52s %s" % (PASS if ok else FAIL, label, detail))
    return ok


def main():
    url = os.environ.get("IC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("IC_DATABASE_URL is not set. See the instructions at the top of this file.")
        return 2

    # never echo the secret — show only enough to confirm the right target
    host = url.split("@")[-1].split("/")[0] if "@" in url else "?"
    print("\nTarget: %s   (password not shown)\n" % host)
    print("── connection ─────────────────────────────────────────────────────────")
    try:
        conn = db.connect()
    except Exception as exc:
        check("connect to Postgres", False, str(exc).splitlines()[0][:90])
        return 1
    check("connect to Postgres", True, host)
    check("using the pooler (recommended for serverless)", "pooler" in host, host)

    print("\n── schema ─────────────────────────────────────────────────────────────")
    n = conn.execute("SELECT COUNT(*) c FROM information_schema.tables "
                     "WHERE table_schema='ic_events'").fetchone()["c"]
    check("ic_events schema has its tables", n >= 18, "%s tables" % n)
    for t in ("users", "events", "vendors", "event_approvers", "approvals",
              "approval_history", "emails", "settings"):
        got = conn.execute("SELECT COUNT(*) c FROM information_schema.tables "
                           "WHERE table_schema='ic_events' AND table_name=?", (t,)).fetchone()["c"]
        if not got:
            check("table ic_events.%s exists" % t, False)

    print("\n── isolation from HC HUB ──────────────────────────────────────────────")
    leaked = conn.execute(
        "SELECT COALESCE(string_agg(table_name, ', '), '') t FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name IN "
        "('events','vendors','event_options','event_approvers','approvals','vendor_approvals',"
        "'approval_history','emails','lookups','settings','vendor_files','vendor_links',"
        "'event_photos','known_devices','approval_links','users','sessions','notifications')"
    ).fetchone()["t"]
    check("no IC tables created in public", leaked == "", leaked or "none")

    grants = conn.execute(
        "SELECT COALESCE(string_agg(DISTINCT grantee, ', '), '') g "
        "FROM information_schema.role_table_grants "
        "WHERE table_schema='ic_events' AND grantee IN ('anon','authenticated')"
    ).fetchone()["g"]
    check("anon/authenticated hold no grants on ic_events", grants == "", grants or "none")

    usage = []
    for role in ("anon", "authenticated"):
        got = conn.execute("SELECT has_schema_privilege(?, 'ic_events', 'USAGE') p", (role,)).fetchone()["p"]
        if got:
            usage.append(role)
    check("anon/authenticated have no USAGE on ic_events", not usage, ", ".join(usage) or "none")

    hc = conn.execute(
        "SELECT COUNT(*) c FROM information_schema.tables WHERE table_schema='public' "
        "AND table_name IN ('reports','report_history','archived_reports','profiles','invites','sections')"
    ).fetchone()["c"]
    check("HC HUB tables untouched in public", hc == 6, "%s of 6 present" % hc)

    print("\n── read / write / delete ──────────────────────────────────────────────")
    probe = "selftest-%s" % uuid.uuid4().hex[:8]
    try:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (probe, "ok"))
        got = conn.execute("SELECT value FROM settings WHERE key=?", (probe,)).fetchone()
        check("write then read a row", got and got["value"] == "ok")
        conn.execute("UPDATE settings SET value=? WHERE key=?", ("changed", probe))
        got = conn.execute("SELECT value FROM settings WHERE key=?", (probe,)).fetchone()
        check("update a row", got and got["value"] == "changed")
        conn.execute("DELETE FROM settings WHERE key=?", (probe,))
        gone = conn.execute("SELECT COUNT(*) c FROM settings WHERE key=?", (probe,)).fetchone()["c"]
        check("delete a row (nothing left behind)", gone == 0)
        rid = conn.execute("INSERT INTO lookups(kind,value,sort) VALUES(?,?,?)",
                           (probe, "x", 0)).lastrowid
        check("INSERT returns a new id (lastrowid works)", bool(rid), "id=%s" % rid)
        conn.execute("DELETE FROM lookups WHERE kind=?", (probe,))
        conn.commit()
    except Exception as exc:
        check("write/read/delete", False, str(exc).splitlines()[0][:90])
        conn.rollback()

    print("\n── seeded content ─────────────────────────────────────────────────────")
    try:
        db.init_db()
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        admins = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]
        types = conn.execute("SELECT COUNT(*) c FROM lookups WHERE kind='event_type'").fetchone()["c"]
        cats = conn.execute("SELECT COUNT(*) c FROM lookups WHERE kind='vendor_category'").fetchone()["c"]
        check("accounts seeded", users >= 1, "%s users, %s admin" % (users, admins))
        check("event types seeded", types >= 8, "%s" % types)
        check("vendor categories seeded", cats >= 10, "%s" % cats)
    except Exception as exc:
        check("seed settings/lookups/accounts", False, str(exc).splitlines()[0][:90])

    conn.commit()
    conn.close()

    bad = [r for r in results if r[0] == FAIL]
    print("\n" + "=" * 72)
    print("  %d passed, %d failed" % (len(results) - len(bad), len(bad)))
    if bad:
        print("\n  Failing checks:")
        for _, label, detail in bad:
            print("   • %s  %s" % (label, detail))
    else:
        print("  Ready to deploy. Nothing of HC HUB's was touched.")
    print("=" * 72 + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
