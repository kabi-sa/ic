"""
Standalone health check.

Vercel maps `api/<name>.py` to `/api/<name>` without any rewrite, so this stays
reachable even if the catch-all routing in vercel.json is missing or misbehaving.
It also runs the first-time setup, so hitting this URL repairs a deployment whose
accounts were never created.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        out = {}
        try:
            import db
            out["store"] = "postgres" if db.using_postgres() else "sqlite"
            out["db_configured"] = bool(os.environ.get("IC_DATABASE_URL")
                                        or os.environ.get("DATABASE_URL"))
            if not out["db_configured"]:
                out["ok"] = False
                out["setup_error"] = ("IC_DATABASE_URL is not set on this deployment. Add it in "
                                      "Vercel → Settings → Environment Variables, then redeploy.")
            else:
                try:
                    db.init_db()                      # safe to repeat; seeds if empty
                    out["setup_ran"] = True
                except Exception as exc:              # noqa: BLE001
                    out["ok"] = False
                    out["setup_error"] = "%s: %s" % (type(exc).__name__,
                                                     str(exc).splitlines()[0][:240])
                conn = db.connect()
                try:
                    out["users"] = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
                    out["admins"] = conn.execute(
                        "SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]
                    out["events"] = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
                    out["seeded"] = out["users"] > 0
                    schema = os.environ.get("IC_DB_SCHEMA", "ic_events")
                    if db.using_postgres():
                        out["schema"] = schema
                        out["tables"] = conn.execute(
                            "SELECT COUNT(*) c FROM information_schema.tables "
                            "WHERE table_schema=?", (schema,)).fetchone()["c"]
                        leaked = conn.execute(
                            "SELECT COUNT(*) c FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_name IN "
                            "('events','vendors','approvals','users','settings')").fetchone()["c"]
                        out["isolated"] = leaked == 0
                    out["base_url"] = db.get_setting(conn, "app_base_url", "")
                    conn.commit()
                finally:
                    conn.close()
            out.setdefault("ok", True)
        except Exception as exc:                      # noqa: BLE001
            out = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:240])}

        body = json.dumps(out, indent=1).encode()
        self.send_response(200 if out.get("ok") else 500)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
