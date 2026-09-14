"""
Vercel entrypoint for the IC Events Approval Hub.

Vercel's Python runtime looks for a `handler` derived from BaseHTTPRequestHandler,
which is what the app already exposes — so the same request handler serves both
`python server.py` locally and the deployed functions.

Two things this file owns:

* First-run setup. It is retried on every request until it succeeds, so a single
  transient failure can never leave a deployment permanently without accounts.
* A self-contained `/api/_setup` report, answered here rather than by the router,
  so it stays reachable even when path rewriting misbehaves.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db                      # noqa: E402
import server                  # noqa: E402

_state = {"ready": False, "error": None, "attempts": 0}


def _bootstrap():
    if _state["ready"]:
        return _state
    _state["attempts"] += 1
    if not db.using_postgres():
        _state["error"] = ("IC_DATABASE_URL is not set on this deployment. Add it in Vercel → "
                           "Settings → Environment Variables, then redeploy.")
        server.BOOTSTRAP_ERROR = _state["error"]
        return _state
    try:
        db.init_db()
        _state["error"] = None
        _state["ready"] = True
        server.BOOTSTRAP_ERROR = None
    except Exception as exc:                     # noqa: BLE001
        _state["error"] = "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:300])
        server.BOOTSTRAP_ERROR = _state["error"]
        print("bootstrap failed:", _state["error"], file=sys.stderr)
    return _state


def _wants_setup_report(h):
    stated = h.headers.get("X-IC-Path") or ""
    return "_setup" in stated or "__path=/api/_setup" in (h.path or "") or "_setup" in (h.path or "")


class handler(server.Handler):
    def _handle(self, method):
        state = _bootstrap()

        if _wants_setup_report(self):
            out = {"ok": state["ready"], "attempts": state["attempts"],
                   "setup_error": state["error"],
                   "store": "postgres" if db.using_postgres() else "sqlite"}
            try:
                conn = db.connect()
                try:
                    out["users"] = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
                    out["admins"] = conn.execute(
                        "SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]
                    out["accounts"] = [r["email"] for r in
                                       conn.execute("SELECT email FROM users ORDER BY email")]
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:             # noqa: BLE001
                out["read_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            body = json.dumps(out, indent=1).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        return server.Handler._handle(self, method)
