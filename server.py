"""
IC Events Approval Hub — application server.

Standard-library only (http.server + sqlite3). Start with:  python server.py
Then open http://localhost:8080
"""

import base64
import calendar
import csv
import datetime
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db
import mailer
import xlsx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
COOKIE_NAME = "ic_session"
KNOWN_COOKIE = "ic_known"
SESSION_DAYS = 7
REVIEW_SESSION_DAYS = 30      # a review-link session stays signed in
KNOWN_DEVICE_DAYS = 180       # ...and the browser is remembered for longer still
MAX_UPLOAD = 15 * 1024 * 1024          # 15 MB per file, when we host ourselves

# A file travels as base64 inside a JSON body, and base64 inflates it by four thirds.
# So the body limit has to clear base64(MAX_UPLOAD) -- 20 MB exactly -- with room for
# the JSON around it. It used to be 20 MB, which meant the advertised 15 MB could never
# actually arrive: the request was refused for being too big before the per-file check
# could say so in plainer words.
MAX_BODY = 28 * 1024 * 1024

# Vercel caps a serverless request body at 4.5 MB and rejects a larger one itself, with
# its own error page instead of our JSON -- which is exactly why an oversized upload
# there surfaced as a bare "Request failed (413)" with no reason attached. Nothing in
# this process can raise that ceiling, so the hub reports the real one and the browser
# shrinks pictures to fit it.
PLATFORM_BODY_LIMIT = int(4.5 * 1024 * 1024) if os.environ.get("VERCEL") else 0


def upload_limit():
    """The largest file that can actually reach this deployment, in bytes."""
    if PLATFORM_BODY_LIMIT:
        # leave room for the JSON keys, the file name and the base64 padding
        return min(MAX_UPLOAD, (PLATFORM_BODY_LIMIT - 16 * 1024) // 4 * 3)
    return MAX_UPLOAD

ALLOWED_EXT = {
    ".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx", ".xls", ".xlsx",
    ".gif", ".webp", ".ppt", ".pptx", ".csv", ".txt",
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

_login_attempts = {}
_lock = threading.Lock()
BOOTSTRAP_ERROR = None


# ====================================================================== util
class ApiError(Exception):
    def __init__(self, status, message, extra=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


def s(v, maxlen=4000):
    if v is None:
        return None
    v = str(v).strip()
    return v[:maxlen] if v else None


def req(v, field, maxlen=4000):
    v = s(v, maxlen)
    if not v:
        raise ApiError(400, "%s is required." % field)
    return v


def num(v, default=0.0):
    try:
        n = float(str(v).replace(",", "").strip())
        if n != n or n in (float("inf"), float("-inf")):
            return default
        return round(n, 2)
    except (TypeError, ValueError):
        return default


def intv(v, default=None):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def public_user(row):
    if not row:
        return None
    return {
        "id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"],
        "department": row["department"], "job_title": row["job_title"],
        "phone": row["phone"] if "phone" in row.keys() else None,
        "manager_id": row["manager_id"], "active": bool(row["active"]),
        "can_view_all": bool(row["can_view_all"]), "created_at": row["created_at"],
        "invited": bool(row["invited"]) if "invited" in row.keys() else False,
        "can_view_archive": bool(row["can_view_archive"]) if "can_view_archive" in row.keys() else False,
        "passwordless": bool(row["passwordless"]) if "passwordless" in row.keys() else True,
        "approver_level": row["approver_level"] if "approver_level" in row.keys() else None,
        "is_final_approver": bool(row["is_final_approver"]) if "is_final_approver" in row.keys() else False,
    }


def valid_email(v):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", v or ""))


def safe_url(v):
    v = s(v, 1000)
    if not v:
        return None
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v
    if not re.match(r"^https?://[^\s<>\"']+$", v, re.I):
        raise ApiError(400, "That link does not look like a valid URL.")
    return v


# ================================================================== business
def ensure_option(conn, event_id):
    """Every event always has at least one option — create "Option A" on demand."""
    row = conn.execute("SELECT * FROM event_options WHERE event_id=? ORDER BY sort,id LIMIT 1",
                       (event_id,)).fetchone()
    if row:
        return row["id"]
    ts = db.now_iso()
    return conn.execute(
        "INSERT INTO event_options(event_id,name,sort,status,created_at,updated_at) "
        "VALUES(?,?,0,'pending',?,?)", (event_id, "Option A", ts, ts)).lastrowid


def option_letter(n):
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return letters[n] if n < len(letters) else str(n + 1)


def recalc_budget(conn, event_id):
    """Recompute every option's total and the event's headline / approved / rejected budgets.

    Each option is a standalone package: its total is its own vendors plus the shared
    miscellaneous cost. The event's estimated budget follows the *selected* option once
    the approver has chosen one, and the primary (first) option before that.
    """
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        return None
    ensure_option(conn, event_id)
    ts = db.now_iso()

    options = conn.execute("SELECT * FROM event_options WHERE event_id=? ORDER BY sort,id",
                           (event_id,)).fetchall()
    for opt in options:
        vs = conn.execute("SELECT * FROM vendors WHERE option_id=?", (opt["id"],)).fetchall()
        vendor_cost = round(sum(float(v["total_amount"] or 0) for v in vs), 2)
        # Delivery is the option's own: one package may be collected and another
        # delivered, which a single figure on the event could not say.
        delivery = round(float(opt["delivery_cost"] or 0), 2)
        conn.execute("UPDATE event_options SET vendor_cost=?, total=?, updated_at=? WHERE id=?",
                     (vendor_cost, round(vendor_cost + delivery, 2), ts, opt["id"]))

    # Whatever was approved -- which may be more than one package, because an event can
    # run two of them. Before a decision there is nothing approved, and the estimate
    # falls back to the first option so the figure on screen means something.
    live = [o for o in options if (o["status"] or "") == "selected"]
    if not live and options:
        live = [options[0]]
    total = round(sum(float(o["total"] or 0) for o in live), 2)
    delivery = round(sum(float(o["delivery_cost"] or 0) for o in live), 2)

    # approved / rejected count the vendors of every option that was approved
    live_ids = [o["id"] for o in live]
    marks = ",".join(["?"] * len(live_ids)) or "NULL"
    live_vendors = conn.execute(
        "SELECT * FROM vendors WHERE option_id IN (%s)" % marks, tuple(live_ids)).fetchall() \
        if live_ids else []
    approved_vendors = round(
        sum(float(v["total_amount"] or 0) for v in live_vendors if v["approval_status"] == db.VS_APPROVED), 2)
    rejected_vendors = round(
        sum(float(v["total_amount"] or 0) for v in live_vendors
            if v["approval_status"] in (db.VS_REJECTED, db.VS_NOT_SELECTED)), 2)

    if ev["status"] in (db.ST_FULL, db.ST_PARTIAL, db.ST_COMPLETED, db.ST_PENDING_VENDORS):
        approved = round(approved_vendors + delivery, 2)
    else:
        approved = 0.0

    conn.execute(
        "UPDATE events SET total_budget=?, approved_budget=?, rejected_budget=?, updated_at=? WHERE id=?",
        (total, approved, rejected_vendors, ts, event_id))
    return total


def log(conn, event_id, action, user, prev=None, new=None, comments=None):
    conn.execute(
        "INSERT INTO approval_history(event_id,action,performed_by,role,comments,previous_status,new_status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (event_id, action, user["id"] if user else None, user["role"] if user else "system",
         comments, prev, new, db.now_iso()))


def notify(conn, user_id, event_id, ntype, title, message):
    if not user_id:
        return
    conn.execute(
        "INSERT INTO notifications(user_id,event_id,type,title,message,read,created_at) "
        "VALUES(?,?,?,?,?,0,?)", (user_id, event_id, ntype, title, message, db.now_iso()))


def approver_ids(conn, event_id):
    rows_ = conn.execute("SELECT user_id FROM event_approvers WHERE event_id=? ORDER BY sort,id",
                         (event_id,)).fetchall()
    return [r["user_id"] for r in rows_]


def load_approvers(conn, event_id):
    return rows(conn.execute(
        "SELECT u.id, u.name, u.email, u.job_title, u.department, a.level, a.status, a.decided_at, "
        "       a.is_final, a.sort, a.selected_option_id, o.name selected_option_name "
        "FROM event_approvers a JOIN users u ON u.id=a.user_id "
        "LEFT JOIN event_options o ON o.id=a.selected_option_id WHERE a.event_id=? "
        "ORDER BY a.level, a.sort, a.id", (event_id,)))


def chain_levels(conn, event_id):
    """The distinct approval levels this event must pass, in order."""
    return [r["level"] for r in conn.execute(
        "SELECT DISTINCT level FROM event_approvers WHERE event_id=? ORDER BY level", (event_id,))]


def level_approvers(conn, event_id, level):
    return rows(conn.execute(
        "SELECT u.*, a.is_final FROM event_approvers a JOIN users u ON u.id=a.user_id "
        "WHERE a.event_id=? AND a.level=? ORDER BY a.sort, a.id", (event_id, level)))


def approval_trail(conn, event_id, before_level=None):
    """The approvers who have already signed this event off, oldest decision first.

    This is what turns a bare "please approve" into a message that names whose
    approval the reader is building on.
    """
    q = ("SELECT u.name, u.email, u.job_title, a.level, a.decided_at, "
         "       a.selected_option_id, a.selected_option_ids, o.name option_name "
         "FROM event_approvers a JOIN users u ON u.id=a.user_id "
         "LEFT JOIN event_options o ON o.id=a.selected_option_id "
         "WHERE a.event_id=? AND a.status='approved' ")
    args = [event_id]
    if before_level is not None:
        q += "AND a.level<? "
        args.append(before_level)
    out = rows(conn.execute(q + "ORDER BY a.level, a.id", tuple(args)))
    # "Option A + Option C" where they took two, so the message names what was approved
    # rather than only the first of it.
    names = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM event_options WHERE event_id=?", (event_id,))}
    for row in out:
        ids = [intv(x) for x in (row.get("selected_option_ids") or "").split(",") if x]
        if len(ids) > 1:
            row["option_name"] = " + ".join(names.get(i, "?") for i in ids)
    return out


def next_level(conn, event_id, after):
    row = conn.execute("SELECT MIN(level) next_level FROM event_approvers "
                       "WHERE event_id=? AND level>?", (event_id, after)).fetchone()
    return row["next_level"] if row and row["next_level"] is not None else None


def supersede_pending(conn, event_id):
    """Retire request drafts that a withdrawal or a re-submission has replaced.

    Each submission queues a fresh message and issues a fresh review link, and the
    old links are revoked. Leaving the old drafts in the outbox is not just clutter:
    they look identical to the live one and carry a link that no longer works, so
    sending the wrong one silently dead-ends the approver.

    Messages already marked as sent are left alone -- they are a record of what went out.
    """
    conn.execute("UPDATE emails SET status='superseded' "
                 "WHERE event_id=? AND status='queued' AND type='approval_request'",
                 (event_id,))


def send_level_request(conn, ev, level, note=None, reuse_links=False):
    """E-mail every approver at this level, each with their own review link.

    `reuse_links` rebuilds the message around the link the person already holds,
    instead of minting a new one and quietly breaking the link they were sent.
    """
    people = level_approvers(conn, ev["id"], level)
    if not people:
        return []
    options = load_options(conn, ev["id"])
    creator = conn.execute("SELECT * FROM users WHERE id=?", (ev["created_by"],)).fetchone()
    currency = db.get_setting(conn, "currency", "SAR")
    base = db.get_setting(conn, "app_base_url", "http://localhost:8080")

    # everyone already through the chain is named in the message and copied on it
    trail = approval_trail(conn, ev["id"], level)
    cc = ", ".join(t["email"] for t in trail if t["email"]) or None
    chosen = next((o["name"] for o in options if o["id"] == ev["selected_option_id"]), None)
    last_level = next_level(conn, ev["id"], level) is None

    for person in people:
        token = issue_review_link(conn, ev["id"], person["id"],
                                  revoke_previous=False, reuse=reuse_links)
        url = "%s/#/review/%s" % (base.rstrip("/"), token)
        closes = bool(person["is_final"]) or last_level
        subject, html_body, text_body = mailer.build_approval_request(
            ev, options, creator, person, base, currency, url, people, level=level, note=note,
            trail=trail, is_final=closes, chosen_name=chosen)
        mailer.queue(conn, person["email"], person["name"], subject, html_body,
                     "approval_request", ev["id"], text_body, cc)
        notify(conn, person["id"], ev["id"], "approval_request", "New approval request",
               "%s — %s needs your approval as the %s approver (%s)."
               % (ev["event_number"], ev["event_name"], ordinal(level),
                  mailer.money(ev["total_budget"], currency)))
    return people


def ordinal(n):
    n = int(n or 1)
    return "%d%s" % (n, {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th"))


PASSWORD_ROLES = (db.ROLE_ADMIN, db.ROLE_IC)


def uses_password(role):
    """Who is given a password the moment their account is made.

    The IC team creates, edits, deletes and exports; they sign in every day and cannot
    wait on an e-mail that may not arrive, so they get one straight away. An approver
    does not need one to do their job -- the personal link in their request already
    proves who they are -- so none is minted for them by default. Fewer passwords in
    circulation is the point.
    """
    return role in PASSWORD_ROLES


def may_hold_password(role):
    """Who *may* be given one when the administrator decides they need it.

    Anyone with an account. An approver who wants to browse the hub, rather than only
    answer the request in front of them, would otherwise depend on a one-time code --
    and until automatic e-mail is switched on, that code cannot actually be delivered.
    Refusing to issue a password locked those people out of their own hub. Issuing one
    is deliberate and per-account: it is not minted unless someone asks for it.
    """
    return role in db.ROLES


def set_final_approver(conn, uid, wanted, role):
    """Exactly one approver carries the final say."""
    if role != db.ROLE_MANAGER:
        conn.execute("UPDATE users SET is_final_approver=0 WHERE id=?", (uid,))
        return False
    if wanted:
        conn.execute("UPDATE users SET is_final_approver=0 WHERE id<>?", (uid,))
        conn.execute("UPDATE users SET is_final_approver=1 WHERE id=?", (uid,))
        return True
    conn.execute("UPDATE users SET is_final_approver=0 WHERE id=?", (uid,))
    return False


def record_approver_defaults(conn):
    """Who signs off a recap or a quarterly update, unless told otherwise.

    Read from the settings table and filtered to people who are still active, so a name
    that leaves the team stops being offered without anything having to be edited.
    """
    raw = db.get_setting(conn, db.RECORD_APPROVER_SETTING, "") or ""
    out = []
    for part in raw.split(","):
        uid = intv(part.strip())
        if not uid or uid in out:
            continue
        if conn.execute("SELECT 1 FROM users WHERE id=? AND active=1", (uid,)).fetchone():
            out.append(uid)
    return out


def approve_record(conn, event_id, ids, on_date):
    """Attach the people who signed a published record off, already approved.

    A recap is not waiting on anyone by the time it is filed -- it went out. So the
    approvals are written as decided, on the day it was shared, rather than left pending
    and cluttering somebody's queue with a decision about last month.
    """
    if not ids:
        return []
    clean = set_approvers(conn, event_id, ids)
    conn.execute("UPDATE event_approvers SET status='approved', decided_at=? WHERE event_id=?",
                 (on_date, event_id))
    return clean


def set_approvers(conn, event_id, ids):
    """Replace the approver list. The first entry becomes the primary approver."""
    owner = conn.execute("SELECT created_by FROM events WHERE id=?", (event_id,)).fetchone()
    owner_id = owner["created_by"] if owner else None
    clean = []
    for uid in ids or []:
        uid = intv(uid)
        if not uid or uid in clean:
            continue
        row = conn.execute("SELECT name FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if not row:
            raise ApiError(400, "One of the selected approvers is not an active user.")
        if uid == owner_id:
            raise ApiError(400, "You cannot assign yourself as the approver of your own event.")
        clean.append(uid)
    if len(clean) > 8:
        raise ApiError(400, "You can assign at most 8 approvers to one event.")
    conn.execute("DELETE FROM event_approvers WHERE event_id=?", (event_id,))
    ts = db.now_iso()
    for i, uid in enumerate(clean):
        u = conn.execute("SELECT COALESCE(approver_level,1) lvl, is_final_approver is_final "
                         "FROM users WHERE id=?", (uid,)).fetchone()
        conn.execute("INSERT INTO event_approvers(event_id,user_id,level,status,is_final,sort,created_at) "
                     "VALUES(?,?,?,'pending',?,?,?)", (event_id, uid, u["lvl"], u["is_final"], i, ts))
    conn.execute("UPDATE events SET approver_id=? WHERE id=?", (clean[0] if clean else None, event_id))
    return clean


def is_approver(conn, user, ev):
    return user["id"] in approver_ids(conn, ev["id"])


def approved_option_ids(conn, event_id):
    """Every option that survived the decision, in order.

    An event can genuinely run two packages -- gifts and catering, say -- so approval is
    not a choice between them but a statement of which ones are happening. Where nothing
    has been decided yet this is empty, and the caller falls back to the first option for
    an estimate.
    """
    return [r["id"] for r in conn.execute(
        "SELECT id FROM event_options WHERE event_id=? AND status='selected' ORDER BY sort,id",
        (event_id,))]


def chosen_option_id(conn, ev):
    if ev["selected_option_id"]:
        return ev["selected_option_id"]
    row = conn.execute("SELECT id FROM event_options WHERE event_id=? ORDER BY sort,id LIMIT 1",
                       (ev["id"],)).fetchone()
    return row["id"] if row else None


def vendor_status_label(conn, ev):
    """Vendor progress across every option approved, or the first before then."""
    live = approved_option_ids(conn, ev["id"]) or [chosen_option_id(conn, ev)]
    live = [x for x in live if x]
    marks = ",".join(["?"] * len(live)) or "NULL"
    vs = conn.execute("SELECT approval_status FROM vendors WHERE option_id IN (%s)" % marks,
                      tuple(live)).fetchall() if live else []
    return vendor_summary_from([dict(v) for v in vs], ev["status"])


def load_options(conn, event_id):
    opts = rows(conn.execute("SELECT * FROM event_options WHERE event_id=? ORDER BY sort,id", (event_id,)))
    for o in opts:
        o["vendors"] = load_vendors(conn, event_id, o["id"])
        o["vendor_count"] = len(o["vendors"])
        o["approved_total"] = round(sum(float(v["total_amount"] or 0) for v in o["vendors"]
                                        if v["approval_status"] == db.VS_APPROVED), 2)
        o["rejected_total"] = round(sum(float(v["total_amount"] or 0) for v in o["vendors"]
                                        if v["approval_status"] == db.VS_REJECTED), 2)
    return opts


def load_vendors(conn, event_id, option_id=None):
    """The vendors of one option, of several, or of the whole event.

    Several, because an event can run two approved packages and its vendor list is then
    both of them -- passing only the first would quietly hide half of what was approved.
    """
    if isinstance(option_id, (list, tuple, set)):
        ids = [intv(x) for x in option_id if intv(x)]
        if not ids:
            ids = [0]
        marks = ",".join(["?"] * len(ids))
        vs = rows(conn.execute(
            "SELECT * FROM vendors WHERE option_id IN (%s) ORDER BY option_id, id" % marks,
            tuple(ids)))
    elif option_id:
        vs = rows(conn.execute("SELECT * FROM vendors WHERE option_id=? ORDER BY id", (option_id,)))
    else:
        vs = rows(conn.execute("SELECT * FROM vendors WHERE event_id=? ORDER BY option_id, id", (event_id,)))
    for v in vs:
        # who actually decided this vendor, and when
        d = conn.execute(
            "SELECT va.decision, va.decision_date, u.name FROM vendor_approvals va "
            "LEFT JOIN users u ON u.id=va.approver_id WHERE va.vendor_id=? ORDER BY va.id DESC LIMIT 1",
            (v["id"],)).fetchone()
        v["decided_by"] = d["name"] if d else None
        v["decided_at"] = d["decision_date"] if d else None
        v["files"] = rows(conn.execute(
            "SELECT id,kind,file_name,mime,size,caption,created_at FROM vendor_files "
            "WHERE vendor_id=? ORDER BY id", (v["id"],)))
        for f in v["files"]:
            f["is_image"] = os.path.splitext(f["file_name"])[1].lower() in IMAGE_EXT
        v["links"] = rows(conn.execute(
            "SELECT id,label,url FROM vendor_links WHERE vendor_id=? ORDER BY id", (v["id"],)))
        v["is_image"] = bool(v.get("quotation_name") and
                             os.path.splitext(v["quotation_name"])[1].lower() in IMAGE_EXT)
    return vs


class Prefetch:
    """Everything the event list needs, fetched per relation instead of per event.

    Built once for a list of events, so seven queries per event becomes six in total.
    This matters most on the hosted store, where every round trip crosses a region
    boundary: a page of thirty events cost two hundred-odd queries before.
    """

    def __init__(self, conn, evs):
        self.people, self.approvers, self.gallery = {}, {}, {}
        self.first_option, self.option_count = {}, {}
        self.vendors_by_option = {}
        ids = [e["id"] for e in evs]
        if not ids:
            return
        marks = ",".join(["?"] * len(ids))

        for r in conn.execute("SELECT id, name, email FROM users"):
            self.people[r["id"]] = {"name": r["name"], "email": r["email"]}

        for r in conn.execute(
                "SELECT u.id, u.name, u.email, u.job_title, u.department, a.event_id, a.level, "
                "a.status, a.decided_at, a.is_final, a.sort, a.selected_option_id, "
                "o.name selected_option_name "
                "FROM event_approvers a JOIN users u ON u.id=a.user_id "
                "LEFT JOIN event_options o ON o.id=a.selected_option_id "
                "WHERE a.event_id IN (%s) ORDER BY a.level, a.sort, a.id" % marks, ids):
            row = dict(r)
            self.approvers.setdefault(row.pop("event_id"), []).append(row)

        for r in conn.execute(
                "SELECT p.id, p.event_id, p.kind, p.file_name, p.mime, p.size, p.caption, "
                "p.created_at, u.name uploaded_by_name FROM event_photos p "
                "LEFT JOIN users u ON u.id=p.uploaded_by "
                "WHERE p.event_id IN (%s) ORDER BY p.id" % marks, ids):
            row = dict(r)
            row["is_image"] = os.path.splitext(row["file_name"])[1].lower() in IMAGE_EXT
            self.gallery.setdefault(row.pop("event_id"), []).append(row)

        self.approved_options = {}
        self.option_totals = {}
        for r in conn.execute("SELECT id, event_id, status, total FROM event_options "
                              "WHERE event_id IN (%s) ORDER BY sort, id" % marks, ids):
            self.option_count[r["event_id"]] = self.option_count.get(r["event_id"], 0) + 1
            self.first_option.setdefault(r["event_id"], r["id"])
            # the list shows a range while nothing is settled, so it needs the figures
            self.option_totals.setdefault(r["event_id"], []).append(
                round(float(r["total"] or 0), 2))
            if (r["status"] or "") == "selected":
                self.approved_options.setdefault(r["event_id"], []).append(r["id"])

        opt_ids = list(set(self.first_option.values())
                       | {e["selected_option_id"] for e in evs if e["selected_option_id"]}
                       | {oid for v in self.approved_options.values() for oid in v})
        if opt_ids:
            om = ",".join(["?"] * len(opt_ids))
            for r in conn.execute("SELECT option_id, approval_status, total_amount FROM vendors "
                                  "WHERE option_id IN (%s)" % om, opt_ids):
                self.vendors_by_option.setdefault(r["option_id"], []).append(dict(r))

    def person(self, uid):
        return self.people.get(uid)

    def option_for(self, ev):
        return ev["selected_option_id"] or self.first_option.get(ev["id"])

    def vendors_for(self, ev):
        """The vendors of every approved option, or of the first while none is approved.

        One option is not the whole picture once two can be approved together -- listing
        only the first would count half of what was agreed.
        """
        live = self.approved_options.get(ev["id"]) or [self.option_for(ev)]
        out = []
        for oid in live:
            out += self.vendors_by_option.get(oid, [])
        return out


def vendor_summary_from(vs, status=None):
    """Vendor progress counted from rows already in hand.

    While the request is still going through the chain, an earlier approver's ticks are
    not the event's answer: a later approver can choose a different package entirely, and
    those vendors then become not-selected. Reporting "1/1 Approved" next to a status of
    Pending Approval claimed a decision the event had not reached -- so until the chain
    finishes this says what is actually true, which is that it is still being decided.
    """
    total = len(vs)
    if not total:
        return {"total": 0, "approved": 0, "rejected": 0, "pending": 0, "label": "No vendors"}
    approved = sum(1 for v in vs if v["approval_status"] == db.VS_APPROVED)
    rejected = sum(1 for v in vs if v["approval_status"] == db.VS_REJECTED)
    pending = total - approved - rejected
    undecided = status in (db.ST_DRAFT, db.ST_PENDING)
    if undecided or pending == total:
        label = "Pending"
    else:
        label = "%d/%d Approved" % (approved, total)
    return {"total": total, "approved": approved, "rejected": rejected,
            "pending": pending, "label": label, "undecided": bool(undecided)}


def content_disposition(disposition, filename):
    """A Content-Disposition header that survives an Arabic file name.

    HTTP header values are latin-1, and Python refuses anything else outright -- so a
    file called الافطار.png took the whole response down with a UnicodeEncodeError, and
    every Arabic-named picture and announcement simply failed to load. The fix is the
    one HTTP defines for this: a plain ASCII name for anything old, and the real name
    percent-encoded in filename*, which every current browser prefers.
    """
    stem, dot, ext = filename.rpartition(".")
    ascii_name = re.sub(r"[^\w\-. ]", "_", (stem or filename).encode("ascii", "ignore")
                        .decode("ascii")).strip("_ ")
    if not ascii_name:
        ascii_name = "file"                       # nothing of the name was ASCII
    if dot and ext:
        safe_ext = re.sub(r"[^\w]", "", ext.encode("ascii", "ignore").decode("ascii"))
        ascii_name = "%s.%s" % (ascii_name, safe_ext or "bin")
    encoded = urllib.parse.quote(filename, safe="")
    return '%s; filename="%s"; filename*=UTF-8\'\'%s' % (disposition, ascii_name, encoded)


KIND_ACTIVITY = "activity"
KIND_RECAP = "monthly_recap"
KIND_QUARTER = "quarterly_update"
RECORD_KINDS = (KIND_ACTIVITY, KIND_RECAP, KIND_QUARTER)

DEFAULT_AUDIENCE = "KABi MENA"

# A recap has no money and nothing to buy: it is a thing IC wrote and sent. Keeping
# these out of the record is what stops a published sheet appearing in a budget total.
NO_BUDGET_KINDS = (KIND_RECAP, KIND_QUARTER)

KIND_LABELS = {
    KIND_ACTIVITY: "Event or activity",
    KIND_RECAP: "Monthly recap",
    KIND_QUARTER: "Quarterly update",
}


def last_thursday(year, month):
    """The last Thursday of a month, which is when IC publishes.

    Recaps and quarterly updates are not events with a date of their own -- they go out
    at the end of a period. Placing them on that Thursday is what lets the calendar read
    as the communication year rather than a list of parties.
    """
    days = calendar.monthrange(year, month)[1]
    for day in range(days, 0, -1):
        if datetime.date(year, month, day).weekday() == 3:      # Monday is 0
            return datetime.date(year, month, day)
    raise ValueError("no Thursday in %d-%02d" % (year, month))   # cannot happen


def quarter_close(year, quarter):
    """The last Thursday of the quarter's final month."""
    if quarter not in (1, 2, 3, 4):
        raise ApiError(400, "A quarter is 1, 2, 3 or 4.")
    return last_thursday(year, quarter * 3)


def iso_date(value, label="Date", required=False):
    """A calendar date, or nothing at all.

    An unchecked date reaches the store as whatever was typed, and then quietly breaks
    everything that treats it as a date: the calendar places it nowhere, the month and
    year filters miss it, sorting puts it in the wrong place and the export carries the
    nonsense onward. Rejecting it at the door is the only place this stays cheap.
    """
    text = s(value, 20) or ""
    if not text:
        if required:
            raise ApiError(400, "%s is required." % label)
        return None
    # fromisoformat also accepts the compact 20261120, which every LIKE '2026-%' filter
    # in this application would then miss, so the hyphenated form is the only one taken.
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        raise ApiError(400, "%s must be a real date, as YYYY-MM-DD." % label)
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        raise ApiError(400, "%s must be a real date, as YYYY-MM-DD." % label)
    return text


def iso_time(value, label="Time"):
    """A wall-clock time, or nothing."""
    text = s(value, 10) or ""
    if not text:
        return None
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", text):
        raise ApiError(400, "%s must be a time, as HH:MM." % label)
    return text


def num_or_zero(value):
    """Numbers belong in an export as numbers, so Excel can total them."""
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def status_label(status):
    return (status or "").replace("_", " ").title()


def vendor_status_text(status):
    return {db.VS_APPROVED: "Approved", db.VS_REJECTED: "Rejected",
            db.VS_NOT_SELECTED: "Not selected"}.get(status, "Pending")


def event_dict(conn, ev, with_children=False, pre=None):
    """One event as the UI wants it. With `pre`, the shared relations come from a
    Prefetch instead of a query apiece; without it the behaviour is unchanged."""
    d = dict(ev)
    if pre is not None:
        creator = pre.person(ev["created_by"])
        approver = pre.person(ev["approver_id"]) if ev["approver_id"] else None
    else:
        creator = conn.execute("SELECT name,email FROM users WHERE id=?", (ev["created_by"],)).fetchone()
        approver = conn.execute("SELECT name,email FROM users WHERE id=?", (ev["approver_id"],)).fetchone() \
            if ev["approver_id"] else None
    d["creator_name"] = creator["name"] if creator else "—"
    d["creator_email"] = creator["email"] if creator else None
    d["approver_name"] = approver["name"] if approver else None
    d["approver_email"] = approver["email"] if approver else None
    if pre is not None:
        d["approvers"] = pre.approvers.get(ev["id"], [])
        vs = pre.vendors_for(ev)
        d["vendor_summary"] = vendor_summary_from(vs, ev["status"])
        d["chosen_option_id"] = pre.option_for(ev)
        d["approved_option_ids"] = [d["chosen_option_id"]] if d["chosen_option_id"] else []
        d["vendor_cost"] = round(sum(float(v["total_amount"] or 0) for v in vs), 2)
        d["option_count"] = pre.option_count.get(ev["id"], 0)
        d["option_totals"] = pre.option_totals.get(ev["id"], [])
        gallery = [dict(g) for g in pre.gallery.get(ev["id"], [])]
    else:
        d["approvers"] = load_approvers(conn, ev["id"])
        d["vendor_summary"] = vendor_status_label(conn, ev)
        d["chosen_option_id"] = chosen_option_id(conn, ev)
        live = approved_option_ids(conn, ev["id"]) or [d["chosen_option_id"]]
        d["approved_option_ids"] = [x for x in live if x]
        marks = ",".join(["?"] * len(d["approved_option_ids"])) or "NULL"
        vt = conn.execute("SELECT COALESCE(SUM(total_amount),0) t FROM vendors "
                          "WHERE option_id IN (%s)" % marks,
                          tuple(d["approved_option_ids"])).fetchone()["t"]             if d["approved_option_ids"] else 0
        d["vendor_cost"] = round(float(vt or 0), 2)
        d["option_count"] = conn.execute("SELECT COUNT(*) c FROM event_options WHERE event_id=?",
                                         (ev["id"],)).fetchone()["c"]
        d["option_totals"] = [round(float(r["total"] or 0), 2) for r in conn.execute(
            "SELECT total FROM event_options WHERE event_id=? ORDER BY sort,id", (ev["id"],))]
        gallery = rows(conn.execute(
            "SELECT p.id, p.kind, p.file_name, p.mime, p.size, p.caption, p.created_at, "
            "u.name uploaded_by_name FROM event_photos p "
            "LEFT JOIN users u ON u.id=p.uploaded_by WHERE p.event_id=? ORDER BY p.id", (ev["id"],)))
        for g in gallery:
            g["is_image"] = os.path.splitext(g["file_name"])[1].lower() in IMAGE_EXT
    # Approvers arrive ordered by approval level, which is what an event in flight needs:
    # 1st, then 2nd, then final. A published recap has no chain to walk -- everyone signed
    # it before it went out -- so there the configured order is the one that carries
    # meaning, and it decides which name the events list leads with.
    if (ev["record_kind"] or KIND_ACTIVITY) != KIND_ACTIVITY:
        d["approvers"] = sorted(d["approvers"], key=lambda a: (a["sort"], a["id"]))
    d["approver_names"] = ", ".join(a["name"] for a in d["approvers"]) or None
    d["photos"] = [g for g in gallery if g["kind"] != "announcement"]
    d["announcements"] = [g for g in gallery if g["kind"] == "announcement"]
    if with_children:
        d["options"] = load_options(conn, ev["id"])
        d["vendors"] = load_vendors(conn, ev["id"],
                                    d.get("approved_option_ids") or d["chosen_option_id"])
        d["history"] = rows(conn.execute(
            "SELECT h.*, u.name performer_name FROM approval_history h "
            "LEFT JOIN users u ON u.id=h.performed_by WHERE h.event_id=? ORDER BY h.id", (ev["id"],)))
        d["approvals"] = rows(conn.execute(
            "SELECT a.*, u.name approver_name FROM approvals a LEFT JOIN users u ON u.id=a.approver_id "
            "WHERE a.event_id=? ORDER BY a.id DESC", (ev["id"],)))
    return d


# --------------------------------------------------------------- permissions
ARCHIVE_STATUSES = (db.ST_COMPLETED, db.ST_FULL, db.ST_PARTIAL, db.ST_REJECTED, db.ST_CANCELLED)


def is_archive_viewer(user):
    return bool(user and user.get("can_view_archive") and not scoped(user))


def can_view_event(conn, user, ev):
    if scoped(user):
        return ev["id"] == user["scope_event_id"] and is_approver(conn, user, ev)
    if user["role"] == db.ROLE_ADMIN:
        return True
    if ev["created_by"] == user["id"]:
        return True
    if ev["status"] != db.ST_DRAFT and is_approver(conn, user, ev):
        return True
    if user["role"] == db.ROLE_IC and user["can_view_all"]:
        return True
    # read-only history access, granted per person: completed events only
    if is_archive_viewer(user) and ev["status"] in ARCHIVE_STATUSES:
        return True
    return False


def can_edit_event(user, ev):
    if ev["status"] != db.ST_DRAFT or scoped(user):
        return False
    return user["role"] == db.ROLE_ADMIN or ev["created_by"] == user["id"]


def my_assignment(conn, user, ev):
    return conn.execute("SELECT * FROM event_approvers WHERE event_id=? AND user_id=?",
                        (ev["id"], user["id"])).fetchone()


def can_decide(conn, user, ev):
    """Only the approvers at the level the chain has reached may decide."""
    if scoped(user) and ev["id"] != user["scope_event_id"]:
        return False
    if ev["status"] not in (db.ST_PENDING, db.ST_PENDING_VENDORS):
        return False
    mine = my_assignment(conn, user, ev)
    if not mine or mine["status"] != "pending":
        return False
    return mine["level"] == (ev["current_level"] or mine["level"])


def guard_view(conn, user, event_id):
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if not can_view_event(conn, user, ev):
        raise ApiError(403, "You do not have permission to access this event.")
    return ev


def guard_edit(conn, user, event_id):
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "You do not have permission to modify this event.")
    if ev["status"] != db.ST_DRAFT:
        raise ApiError(409, "This event is %s and can no longer be edited. Use \"Withdraw & Edit\" first."
                       % ev["status"].replace("_", " "))
    return ev


def missing_for_submission(conn, ev):
    missing = []
    if not (ev["event_name"] or "").strip():
        missing.append("Event Name")
    if not ev["event_date"]:
        missing.append("Event Date")
    if not (ev["location"] or "").strip():
        missing.append("Location")
    if not (ev["description"] or "").strip():
        missing.append("Event Description")
    if not approver_ids(conn, ev["id"]):
        missing.append("At least one approver")
    options = conn.execute("SELECT * FROM event_options WHERE event_id=? ORDER BY sort,id",
                           (ev["id"],)).fetchall()
    multi = len(options) > 1
    for opt in options:
        vs = conn.execute("SELECT * FROM vendors WHERE option_id=? ORDER BY id", (opt["id"],)).fetchall()
        prefix = ("%s: " % opt["name"]) if multi else ""
        if not vs:
            missing.append(prefix + "At least one vendor")
            continue
        for i, v in enumerate(vs, 1):
            if not (v["vendor_name"] or "").strip():
                missing.append("%sVendor %d: Vendor Name" % (prefix, i))
            if float(v["quotation_amount"] or 0) <= 0:
                missing.append("%sVendor %d (%s): Quotation Amount"
                               % (prefix, i, v["vendor_name"] or "unnamed"))
    return missing


# ==================================================================== routes
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


# ------------------------------------------------------------------- session
def current_user(conn, handler):
    token = handler.cookies.get(COOKIE_NAME)
    if not token:
        return None
    row = conn.execute(
        "SELECT u.*, s.scope_event_id FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token=? AND s.expires_at > ? AND u.active=1", (token, db.now_iso())).fetchone()
    if not row:
        return None
    user = dict(row)
    # A review-link session is scoped: it can only ever touch the one event it was issued for.
    user["scope_event_id"] = row["scope_event_id"]
    return user


def scoped(user):
    return bool(user and user.get("scope_event_id"))


def deny_scoped(user, what="this action"):
    if scoped(user):
        raise ApiError(403, "Your review link only covers the event it was sent for. "
                            "Sign in with your account for %s." % what)


@route("POST", r"/api/auth/login")
def api_login(conn, ctx):
    """Sign in with name + e-mail.

    Only people the IC team has added can get in — an unknown address is refused.
    Accounts that carry a real password (the administrator) must still supply it;
    everyone the admin adds signs in with their name and e-mail alone.
    """
    body = ctx["body"]
    email = (s(body.get("email")) or "").lower()
    name = s(body.get("name"), 120)
    password = body.get("password") or ""
    key = ctx["handler"].client_address[0] + "|" + email
    with _lock:
        attempts, until = _login_attempts.get(key, (0, None))
        if until and datetime.datetime.now() < until:
            raise ApiError(429, "Too many failed attempts. Please try again in a minute.")

    def strike(msg, status=401, extra=None):
        with _lock:
            att, _ = _login_attempts.get(key, (0, None))
            att += 1
            _login_attempts[key] = (att, datetime.datetime.now() + datetime.timedelta(minutes=1)
                                    if att >= 8 else None)
        raise ApiError(status, msg, extra)

    if not email:
        raise ApiError(400, "Please enter your work e-mail address.")
    user = conn.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
    if not user or not user["active"]:
        strike("That e-mail is not on the users list. Ask the Internal Communication team to add you.", 403)

    if user["passwordless"]:
        # A name is not a secret. On a public address, anyone who knew a colleague's
        # e-mail could sign in as them, so these accounts now prove it with a code
        # sent to that e-mail. Approvers are unaffected: their link does the same job.
        raise ApiError(401, "This account signs in with a code sent to your e-mail.",
                       {"needs_code": True, "email": email})
    else:
        if not password:
            raise ApiError(401, "This account needs a password.", {"needs_password": True})
        if not db.verify_password(password, user["password_hash"]):
            strike("Incorrect password.", 401, {"needs_password": True})
    with _lock:
        _login_attempts.pop(key, None)
    token = secrets.token_urlsafe(32)
    exp = (datetime.datetime.now() + datetime.timedelta(days=SESSION_DAYS)).replace(microsecond=0).isoformat(sep=" ")
    conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                 (token, user["id"], db.now_iso(), exp))
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now_iso(),))
    ctx["set_cookie"] = token
    return {"user": public_user(user)}


CODE_MINUTES = 10          # how long a code stays valid
CODE_TRIES = 5             # wrong guesses before the code is burned
CODE_PER_HOUR = 6          # codes one address may ask for in an hour


def _issue_login_code(conn, user, handler):
    """Make a fresh code, retire any earlier one, and post it to the account's e-mail."""
    now = datetime.datetime.now()
    since = (now - datetime.timedelta(hours=1)).replace(microsecond=0).isoformat(sep=" ")
    asked = conn.execute("SELECT COUNT(*) c FROM login_codes WHERE email=? AND created_at > ?",
                         (user["email"].lower(), since)).fetchone()["c"]
    if asked >= CODE_PER_HOUR:
        raise ApiError(429, "Too many codes requested for this address. Please try again later.")

    conn.execute("UPDATE login_codes SET used=1 WHERE email=? AND used=0",
                 (user["email"].lower(),))
    code = "%06d" % secrets.randbelow(1000000)
    expires = (now + datetime.timedelta(minutes=CODE_MINUTES)).replace(microsecond=0).isoformat(sep=" ")
    conn.execute(
        "INSERT INTO login_codes(email,code_hash,expires_at,requested_ip,created_at) "
        "VALUES(?,?,?,?,?)",
        (user["email"].lower(), db.hash_password(code), expires,
         handler.client_address[0], db.now_iso()))

    subject, html, text = mailer.build_login_code(user, code, CODE_MINUTES)
    mailer.queue(conn, user["email"], user["name"], subject, html, "login_code", None, text)
    return code, expires


@route("POST", r"/api/auth/request-code")
def api_request_code(conn, ctx):
    """Send a one-time sign-in code to a known address."""
    email = (s(ctx["body"].get("email")) or "").lower()
    if not email:
        raise ApiError(400, "Please enter your work e-mail address.")
    user = conn.execute("SELECT * FROM users WHERE lower(email)=? AND active=1",
                        (email,)).fetchone()
    if not user:
        raise ApiError(403, "That e-mail is not on the users list. Ask the Internal "
                            "Communication team to add you.")
    if not user["passwordless"]:
        raise ApiError(400, "This account signs in with its password.",
                       {"needs_password": True})

    code, expires = _issue_login_code(conn, user, ctx["handler"])
    delivered = db.get_setting(conn, "smtp_enabled", "0") == "1"
    out = {"sent": delivered, "email": email, "expires_at": expires,
           "minutes": CODE_MINUTES}
    if not delivered:
        # Automatic delivery is off, so nothing was actually sent. Saying "check your
        # e-mail" here would strand people; the code is waiting in Administration,
        # where IC already forwards the approval messages by hand.
        out["undelivered"] = ("Automatic e-mail is not switched on, so the code could not "
                              "be sent. Ask the Internal Communication team for it.")
    return out


@route("POST", r"/api/auth/verify-code")
def api_verify_code(conn, ctx):
    """Exchange a valid code for a session."""
    body = ctx["body"]
    email = (s(body.get("email")) or "").lower()
    entered = re.sub(r"\D", "", s(body.get("code"), 12) or "")
    if not email or not entered:
        raise ApiError(400, "Enter the six-digit code from your e-mail.")

    user = conn.execute("SELECT * FROM users WHERE lower(email)=? AND active=1",
                        (email,)).fetchone()
    if not user:
        raise ApiError(403, "That e-mail is not on the users list.")

    row = conn.execute("SELECT * FROM login_codes WHERE email=? AND used=0 "
                       "ORDER BY id DESC LIMIT 1", (email,)).fetchone()
    if not row:
        raise ApiError(401, "No code is waiting for that address. Ask for a new one.",
                       {"needs_code": True, "email": email})
    if row["expires_at"] < db.now_iso():
        conn.execute("UPDATE login_codes SET used=1 WHERE id=?", (row["id"],))
        raise ApiError(401, "That code has expired. Ask for a new one.",
                       {"needs_code": True, "email": email})
    if row["attempts"] >= CODE_TRIES:
        conn.execute("UPDATE login_codes SET used=1 WHERE id=?", (row["id"],))
        raise ApiError(429, "Too many wrong attempts. Ask for a new code.",
                       {"needs_code": True, "email": email})
    if not db.verify_password(entered, row["code_hash"]):
        conn.execute("UPDATE login_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
        left = CODE_TRIES - (row["attempts"] + 1)
        raise ApiError(401, "That code is not right. %d attempt%s left."
                       % (max(left, 0), "" if left == 1 else "s"))

    # spent, whatever happens next
    conn.execute("UPDATE login_codes SET used=1 WHERE id=?", (row["id"],))
    token = secrets.token_urlsafe(32)
    exp = (datetime.datetime.now() + datetime.timedelta(days=SESSION_DAYS)) \
        .replace(microsecond=0).isoformat(sep=" ")
    conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                 (token, user["id"], db.now_iso(), exp))
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now_iso(),))
    conn.execute("DELETE FROM login_codes WHERE expires_at < ?", (db.now_iso(),))
    ctx["set_cookie"] = token
    return {"user": public_user(user)}


@route("POST", r"/api/auth/logout")
def api_logout(conn, ctx):
    token = ctx["handler"].cookies.get(COOKIE_NAME)
    if token:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    ctx["clear_cookie"] = True
    return {"ok": True}


@route("POST", r"/api/review/forget")
def api_review_forget(conn, ctx):
    """Stop remembering this browser — for shared or public computers."""
    known = ctx["handler"].cookies.get(KNOWN_COOKIE)
    if known:
        conn.execute("DELETE FROM known_devices WHERE token=?", (known,))
    sess = ctx["handler"].cookies.get(COOKIE_NAME)
    if sess:
        conn.execute("DELETE FROM sessions WHERE token=?", (sess,))
    ctx["cookies"].append((KNOWN_COOKIE, "", 0))
    ctx["clear_cookie"] = True
    return {"ok": True}


@route("GET", r"/api/health")
def api_health(conn, ctx):
    """Open health check — deliberately carries no secrets and no business data.

    Lets you confirm a deployment is wired to the right database and that the
    IC schema is still walled off from the HC Report Hub, without signing in.
    """
    out = {"ok": True, "store": "postgres" if db.using_postgres() else "sqlite"}
    if BOOTSTRAP_ERROR:
        out["ok"] = False
        out["setup_error"] = BOOTSTRAP_ERROR
    try:
        out["users"] = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        out["events"] = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        if db.using_postgres():
            out["tables"] = conn.execute(
                "SELECT COUNT(*) c FROM information_schema.tables "
                "WHERE table_schema=?", (pg_schema(),)).fetchone()["c"]
            leaked = conn.execute(
                "SELECT COUNT(*) c FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name IN ('events','vendors','approvals','approval_history','emails',"
                "'users','settings','lookups','event_options','event_approvers')").fetchone()["c"]
            exposed = conn.execute("SELECT has_schema_privilege('anon', ?, 'USAGE') p",
                                   (pg_schema(),)).fetchone()["p"]
            # A foreign key crossing between the two schemas would tie the hubs
            # together structurally even with every table in its own place -- so it is
            # asked about directly rather than inferred from where the tables live.
            try:
                crossing = conn.execute(
                    "SELECT COUNT(*) c FROM information_schema.table_constraints tc "
                    "JOIN information_schema.constraint_column_usage ccu "
                    "  ON ccu.constraint_name=tc.constraint_name "
                    " AND ccu.constraint_schema=tc.constraint_schema "
                    "WHERE tc.constraint_type='FOREIGN KEY' "
                    "  AND ((tc.table_schema=? AND ccu.table_schema='public') "
                    "    OR (tc.table_schema='public' AND ccu.table_schema=?))",
                    (pg_schema(), pg_schema())).fetchone()["c"]
            except Exception:               # noqa: BLE001
                # A diagnostic that cannot run must not make a healthy hub report as
                # broken. Say the answer is unknown and leave the verdict to the audit.
                crossing = None
            stray = db.unqualified_tables()
            out["schema"] = pg_schema()
            out["isolated"] = (leaked == 0 and not exposed
                               and crossing in (0, None) and not stray)
            out["tables_in_public"] = leaked
            out["cross_schema_links"] = "unknown" if crossing is None else crossing
            if stray:
                # Loud, because the consequence is writing into the other hub's schema.
                out["unqualified_tables"] = stray
        out["admins"] = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]
        out["seeded"] = out["users"] > 0
        out["base_url"] = db.get_setting(conn, "app_base_url", "")
        out["smtp_enabled"] = db.get_setting(conn, "smtp_enabled", "0") == "1"

        # Whether the store has caught up with the code. A column the application
        # expects and the database has not got is the one failure this check could not
        # see before, and it presents as an unexplained 500 on an ordinary page.
        missing = []
        # A whole table that is absent, not only a column. This is how login_codes was
        # missing from the hosted store without anything saying so.
        try:
            if db.using_postgres():
                have = {r["table_name"] for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema=?",
                    (pg_schema(),))}
            else:
                have = {r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            absent = [t for t in db.expected_tables() if t not in have]
        except Exception:                   # noqa: BLE001 - a health check never throws
            absent = []
        if absent:
            out["ok"] = False
            out["missing_tables"] = absent
        for table, columns in db.ADDITIVE_COLUMNS.items():
            try:
                if db.using_postgres():
                    have = {r["column_name"] for r in conn.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=? AND table_name=?", (pg_schema(), table))}
                else:
                    have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
            except Exception:                       # noqa: BLE001 - a health check never throws
                continue
            if not have:
                continue
            missing += ["%s.%s" % (table, name) for name, _decl in columns if name not in have]
        out["schema_ready"] = not missing and not absent
        if missing:
            out["ok"] = False
            out["missing_columns"] = missing[:20]
            out["hint"] = ("The database is behind the code. A cold start applies these "
                           "automatically; if this persists, the migration is failing.")
    except Exception as exc:                       # noqa: BLE001
        out["ok"] = False
        out["error"] = str(exc).splitlines()[0][:160]
    return out


def pg_schema():
    return os.environ.get("IC_DB_SCHEMA", "ic_events")


@route("GET", r"/api/auth/me")
def api_me(conn, ctx):
    user = ctx["user"]
    if not user:
        return {"user": None}
    unread = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND read=0",
                          (user["id"],)).fetchone()["c"]
    # An administrator still holding the documented starter password is told so on every
    # screen. A warning that only appears in a setup guide is a warning nobody reads.
    weak = (user["role"] == db.ROLE_ADMIN
            and db.using_weak_default(user.get("password_hash")))
    return {"user": public_user(user), "unread": unread,
            "scoped": scoped(user), "scope_event_id": user.get("scope_event_id"),
            "archive_viewer": is_archive_viewer(user), "weak_password": weak,
            "secure_connection": ctx["handler"].is_https(),
            # The browser needs this to shrink a picture to fit instead of watching the
            # upload be refused -- the ceiling differs between the office hub and the
            # hosted one, so it cannot be a constant in the page.
            "upload_limit_bytes": upload_limit(),
            "settings": {"currency": db.get_setting(conn, "currency", "SAR"),
                         "org_name": db.get_setting(conn, "org_name", "KABi"),
                         "app_name": db.get_setting(conn, "app_name", "IC Events Approval Hub"),
                         "default_vat_rate": num(db.get_setting(conn, "default_vat_rate", "15"), 15)}}


# ------------------------------------------------------- choosing a password
SETUP_DAYS = 7             # how long a set-up link stays usable
MIN_PASSWORD = 10


def token_digest(token):
    """A lookup key for a bearer token.

    The token is 288 bits of randomness, so a plain SHA-256 is the right tool: there is
    nothing to brute-force and nothing a salt would add. Keeping the digest rather than
    the token means a copy of this table cannot be turned into a set of account
    takeovers -- which matters more here than anywhere else, because this particular
    token sets a password.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_problem(password, user=None):
    """Why this password will not do, or None if it will.

    Deliberately short. Length is what actually helps; rules demanding a symbol and a
    capital mostly produce Passw0rd!. So the checks are aimed at the passwords people
    genuinely reach for: the one printed in the setup guide, their own name, their own
    e-mail address.
    """
    password = password or ""
    if len(password) < MIN_PASSWORD:
        return "Please use at least %d characters." % MIN_PASSWORD
    if len(password) > 200:
        return "That is longer than the hub can take. Please keep it under 200 characters."
    low = password.lower()
    if low == db.WEAK_DEFAULT_PASSWORD.lower():
        return "That is the password from the setup guide. Please choose your own."
    for word in ("password", "kabi2026", "12345678", "qwerty", "letmein"):
        if word in low:
            return ("That contains %s, which is one of the first things anyone would try."
                    % json.dumps(word))
    if user:
        local = ((user["email"] or "").split("@")[0] or "").lower()
        if len(local) > 2 and local in low:
            return "Please leave your e-mail address out of your password."
        for part in (user["name"] or "").lower().split():
            if len(part) > 3 and part in low:
                return "Please leave your own name out of your password."
    return None


def issue_setup_link(conn, user, by_id=None):
    """A one-time link that lets someone choose their own password.

    Internal Communication does not set passwords: it hands over this link, and the
    person on the other end picks something only they know. Any earlier link for the
    account is retired first, so exactly one is ever live -- a link that went to the
    wrong place stops working the moment a new one is made.
    """
    conn.execute("UPDATE password_setups SET used=1 WHERE user_id=? AND used=0",
                 (user["id"],))
    token = secrets.token_urlsafe(36)
    exp = (datetime.datetime.now() + datetime.timedelta(days=SETUP_DAYS)) \
        .replace(microsecond=0).isoformat(sep=" ")
    conn.execute("INSERT INTO password_setups(token_digest,user_id,expires_at,created_by,"
                 "created_at) VALUES(?,?,?,?,?)",
                 (token_digest(token), user["id"], exp, by_id, db.now_iso()))
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=120)) \
        .replace(microsecond=0).isoformat(sep=" ")
    conn.execute("DELETE FROM password_setups WHERE used=1 AND created_at < ?", (cutoff,))
    base = db.get_setting(conn, "app_base_url", "http://localhost:8080")
    url = "%s/#/set-password/%s" % (base.rstrip("/"), token)
    subject, html_body, text_body = mailer.build_setup_link(user, url, SETUP_DAYS)
    mailer.queue(conn, user["email"], user["name"], subject, html_body,
                 "password_setup", None, text_body)
    return url, exp


def _load_setup(conn, token):
    """The live set-up link behind this token, or a reason it cannot be used.

    Each refusal says what to do next. A dead link is the one moment someone is locked
    out with no way back in, so "ask for a new one" has to be in the message itself.
    """
    row = conn.execute(
        "SELECT p.*, u.name uname, u.email uemail, u.active uactive FROM password_setups p "
        "JOIN users u ON u.id=p.user_id WHERE p.token_digest=?",
        (token_digest(token),)).fetchone()
    if not row:
        raise ApiError(404, "This link is not valid. Ask the Internal Communication team "
                            "for a new one.")
    if row["used"]:
        raise ApiError(410, "This link has already been used. Ask the Internal "
                            "Communication team for a new one.")
    if row["expires_at"] < db.now_iso():
        raise ApiError(410, "This link has expired. Ask the Internal Communication team "
                            "for a new one.")
    if not row["uactive"]:
        raise ApiError(403, "That account is not active. Ask the Internal Communication team.")
    return row


@route("POST", r"/api/users/(\d+)/setup-link")
def api_setup_link(conn, ctx, uid):
    """Make a one-time link so this person can choose their own password.

    This replaced issuing a password on someone's behalf. An administrator can give a
    person the means to set one, and cannot learn what they set -- which is what makes
    "nobody else knows my password" true here rather than merely polite.
    """
    admin = need_role(ctx, db.ROLE_ADMIN)
    uid = int(uid)
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        raise ApiError(404, "User not found.")
    if not u["active"]:
        raise ApiError(400, "%s is not active. Reactivate the account first." % u["name"])
    url, exp = issue_setup_link(conn, u, admin["id"])
    print("  Set-up link issued for %s by %s" % (u["email"], admin["email"]))
    return {"ok": True, "name": u["name"], "email": u["email"], "url": url,
            "expires_at": exp, "days": SETUP_DAYS,
            "sent": db.get_setting(conn, "smtp_enabled", "0") == "1"}


@route("GET", r"/api/setup/([A-Za-z0-9_\-]{20,120})")
def api_setup_open(conn, ctx, token):
    """What the set-up page needs to greet the right person. Creates no session."""
    row = _load_setup(conn, token)
    return {"name": row["uname"], "email": row["uemail"],
            "expires_at": row["expires_at"], "min_length": MIN_PASSWORD}


@route("POST", r"/api/setup/([A-Za-z0-9_\-]{20,120})")
def api_setup_save(conn, ctx, token):
    """Store the password this person chose, and sign them in.

    Every existing session for the account goes first. Setting a password is what you do
    when you are not certain who else has been in, so it should end anyone who was.
    """
    key = ctx["handler"].client_address[0] + "|setup|" + token[:12]
    with _lock:
        attempts, until = _login_attempts.get(key, (0, None))
        if until and datetime.datetime.now() < until:
            raise ApiError(429, "Too many attempts. Please wait a minute and try again.")

    row = _load_setup(conn, token)
    b = ctx["body"]
    password = b.get("password") or ""
    confirm = b.get("confirm")
    if confirm is not None and password != confirm:
        raise ApiError(400, "The two passwords do not match.")

    user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
    problem = password_problem(password, user)
    if problem:
        with _lock:
            att, _ = _login_attempts.get(key, (0, None))
            _login_attempts[key] = (att + 1,
                                    datetime.datetime.now() + datetime.timedelta(minutes=1)
                                    if att + 1 >= 12 else None)
        raise ApiError(400, problem)
    with _lock:
        _login_attempts.pop(key, None)

    conn.execute("UPDATE users SET password_hash=?, passwordless=0, invited=0 WHERE id=?",
                 (db.hash_password(password), user["id"]))
    conn.execute("UPDATE password_setups SET used=1, used_at=?, used_ip=? WHERE id=?",
                 (db.now_iso(), ctx["handler"].client_address[0], row["id"]))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))

    sess = secrets.token_urlsafe(32)
    exp = (datetime.datetime.now() + datetime.timedelta(days=SESSION_DAYS)) \
        .replace(microsecond=0).isoformat(sep=" ")
    conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                 (sess, user["id"], db.now_iso(), exp))
    ctx["set_cookie"] = sess
    print("  Password set by %s" % user["email"])
    fresh = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    return {"ok": True, "user": public_user(fresh)}


# ------------------------------------------------------- passwordless review
LINK_DAYS = 45


def deny_self_approval(conn, ctx, link, approver):
    """Refuse a review link opened by the person who raised the request.

    The requester holds this link -- it is on their own page, because sending it is their
    job -- and the approver's name and address are printed beside it. So nothing stops
    them opening it themselves and approving their own event, which is the one thing the
    approval chain exists to prevent. The decision endpoint's own guard does not catch
    it: by then the session genuinely is the approver's.

    This does not make the link unforgeable. A private window defeats it, and the real
    answer is for approvers to sign in -- which they can now do, since they may hold a
    password. What it does is stop it happening by accident, and put the attempt in the
    record either way, which an audit that reads "approved by the approver" otherwise
    could not show.
    """
    current = current_user(conn, ctx["handler"])
    if not current or current["id"] == approver["id"]:
        return
    ev = conn.execute("SELECT created_by, event_number FROM events WHERE id=?",
                      (link["event_id"],)).fetchone()
    if not ev or current["id"] != ev["created_by"]:
        return
    log(conn, link["event_id"], "Self-approval refused", current, None, None,
        "%s tried to open %s's review link while signed in as the requester"
        % (current["name"], approver["name"]))
    conn.commit()
    raise ApiError(403, "This link belongs to %s. You are signed in as the person who "
                        "raised this request, so you cannot approve it yourself."
                   % approver["name"])


def issue_review_link(conn, event_id, approver_id, revoke_previous=True, reuse=False):
    """A personal link per approver. Resubmitting revokes every previous link.

    `reuse` returns the link this person already holds rather than minting another.
    That matters when the requester asks to see the link again, or rebuilds a draft:
    every extra token is another way into the event, and a link already pasted into a
    chat has to keep working. A re-submission still revokes and re-issues, because
    there the old link genuinely should stop opening.
    """
    if revoke_previous:
        conn.execute("UPDATE approval_links SET revoked=1 WHERE event_id=? AND revoked=0", (event_id,))
    if reuse:
        live = conn.execute(
            "SELECT token FROM approval_links WHERE event_id=? AND approver_id=? "
            "AND revoked=0 AND expires_at > ? ORDER BY created_at DESC",
            (event_id, approver_id, db.now_iso())).fetchone()
        if live:
            return live["token"]
    token = secrets.token_urlsafe(36)
    exp = (datetime.datetime.now() + datetime.timedelta(days=LINK_DAYS)) \
        .replace(microsecond=0).isoformat(sep=" ")
    conn.execute("INSERT INTO approval_links(token,event_id,approver_id,expires_at,created_at) "
                 "VALUES(?,?,?,?,?)", (token, event_id, approver_id, exp, db.now_iso()))
    return token


def known_device_user(conn, handler):
    """The approver this browser was remembered as, if any."""
    token = handler.cookies.get(KNOWN_COOKIE)
    if not token:
        return None
    row = conn.execute(
        "SELECT u.*, k.token ktoken, k.name_used FROM known_devices k JOIN users u ON u.id=k.user_id "
        "WHERE k.token=? AND k.expires_at > ? AND u.active=1", (token, db.now_iso())).fetchone()
    return row


def remember_device(conn, ctx, user_id, name_used):
    token = ctx["handler"].cookies.get(KNOWN_COOKIE)
    exp = (datetime.datetime.now() + datetime.timedelta(days=KNOWN_DEVICE_DAYS)) \
        .replace(microsecond=0).isoformat(sep=" ")
    existing = conn.execute("SELECT * FROM known_devices WHERE token=?", (token,)).fetchone() if token else None
    if existing and existing["user_id"] == user_id:
        conn.execute("UPDATE known_devices SET expires_at=?, last_used_at=?, name_used=? WHERE token=?",
                     (exp, db.now_iso(), name_used, token))
    else:
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO known_devices(token,user_id,name_used,created_at,last_used_at,expires_at) "
                     "VALUES(?,?,?,?,?,?)", (token, user_id, name_used, db.now_iso(), db.now_iso(), exp))
    ctx["cookies"].append((KNOWN_COOKIE, token, KNOWN_DEVICE_DAYS * 24))
    conn.execute("DELETE FROM known_devices WHERE expires_at < ?", (db.now_iso(),))
    return token


def open_review_session(conn, ctx, approver, link, name_used):
    """Create the event-scoped session and remember this browser."""
    sess = secrets.token_urlsafe(32)
    exp = (datetime.datetime.now() + datetime.timedelta(days=REVIEW_SESSION_DAYS)) \
        .replace(microsecond=0).isoformat(sep=" ")
    conn.execute("INSERT INTO sessions(token,user_id,scope_event_id,created_at,expires_at) VALUES(?,?,?,?,?)",
                 (sess, approver["id"], link["event_id"], db.now_iso(), exp))
    conn.execute("UPDATE approval_links SET opened_at=?, opened_name=?, opened_email=?, "
                 "open_count=open_count+1 WHERE token=?",
                 (db.now_iso(), name_used, approver["email"], link["token"]))
    ctx["cookies"].append((COOKIE_NAME, sess, REVIEW_SESSION_DAYS * 24))
    remember_device(conn, ctx, approver["id"], name_used)


def mask_email(email):
    try:
        name, domain = (email or "").split("@", 1)
    except ValueError:
        return "•••"
    head = name[:2] if len(name) > 3 else name[:1]
    return "%s%s@%s" % (head, "•" * max(3, len(name) - len(head)), domain)


def _load_link(conn, token, for_entry=True):
    row = conn.execute("SELECT * FROM approval_links WHERE token=?", (s(token, 120),)).fetchone()
    if not row or row["revoked"]:
        raise ApiError(404, "This review link is no longer valid. Please ask for a new approval request.")
    if row["expires_at"] < db.now_iso():
        raise ApiError(410, "This review link has expired. Please ask the requester to resend it.")
    return row


@route("GET", r"/api/review/([A-Za-z0-9_\-]{20,120})")
def api_review_preview(conn, ctx, token):
    """Minimal public preview — enough to reassure the manager, nothing confidential."""
    link = _load_link(conn, token)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (link["event_id"],)).fetchone()
    if not ev:
        raise ApiError(404, "This event no longer exists.")
    approver = conn.execute("SELECT * FROM users WHERE id=?", (link["approver_id"],)).fetchone()
    creator = conn.execute("SELECT name FROM users WHERE id=?", (ev["created_by"],)).fetchone()
    known = known_device_user(conn, ctx["handler"])
    return {"review": {
        "event_number": ev["event_number"],
        "event_name": ev["event_name"],
        "event_date": ev["event_date"],
        "status": ev["status"],
        "requested_by": creator["name"] if creator else "—",
        "email_hint": mask_email(approver["email"] if approver else ""),
        "awaiting": ev["status"] in (db.ST_PENDING, db.ST_PENDING_VENDORS),
        # this browser already confirmed this approver — no need to ask again
        "remembered": ({"name": known["name_used"] or known["name"], "email": known["email"]}
                       if known and approver and known["id"] == approver["id"] else None),
    }}


@route("POST", r"/api/review/([A-Za-z0-9_\-]{20,120})/continue")
def api_review_continue(conn, ctx, token):
    """Open the approval page for a browser that was already confirmed once."""
    link = _load_link(conn, token)
    known = known_device_user(conn, ctx["handler"])
    if not known or known["id"] != link["approver_id"]:
        raise ApiError(403, "This browser has not been confirmed for this approver yet.")
    ev = conn.execute("SELECT * FROM events WHERE id=?", (link["event_id"],)).fetchone()
    if not ev:
        raise ApiError(404, "This event no longer exists.")
    deny_self_approval(conn, ctx, link, known)
    name_used = known["name_used"] or known["name"]
    first = not link["opened_at"]
    open_review_session(conn, ctx, known, link, name_used)
    if first:
        log(conn, link["event_id"], "Approver opened the review link", dict(known), ev["status"],
            ev["status"], "Opened by %s (remembered browser)" % name_used)
    return {"user": public_user(known), "event_id": link["event_id"], "scoped": True,
            "remembered": True}


@route("POST", r"/api/review/([A-Za-z0-9_\-]{20,120})/enter")
def api_review_enter(conn, ctx, token):
    """Confirm identity (name + e-mail) and open a session scoped to this one event."""
    link = _load_link(conn, token)
    b = ctx["body"]
    name = req(b.get("name"), "Your name", 120)
    email = (req(b.get("email"), "Your e-mail", 160)).lower()

    key = ctx["handler"].client_address[0] + "|link|" + token[:12]
    with _lock:
        attempts, until = _login_attempts.get(key, (0, None))
        if until and datetime.datetime.now() < until:
            raise ApiError(429, "Too many attempts. Please wait a minute and try again.")

    approver = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (link["approver_id"],)).fetchone()
    if not approver or email != (approver["email"] or "").lower():
        with _lock:
            attempts, _ = _login_attempts.get(key, (0, None))
            attempts += 1
            _login_attempts[key] = (attempts, datetime.datetime.now() + datetime.timedelta(minutes=2)
                                    if attempts >= 5 else None)
        raise ApiError(403, "That e-mail does not match the address this request was sent to.")
    with _lock:
        _login_attempts.pop(key, None)

    deny_self_approval(conn, ctx, link, approver)

    first = not link["opened_at"]
    open_review_session(conn, ctx, approver, link, name)
    if first:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (link["event_id"],)).fetchone()
        log(conn, link["event_id"], "Approver opened the review link", dict(approver), ev["status"],
            ev["status"], "Opened by %s (%s) without signing in" % (name, email))
    return {"user": public_user(approver), "event_id": link["event_id"], "scoped": True}


@route("PUT", r"/api/profile")
def api_profile(conn, ctx):
    user = need_user(ctx)
    deny_scoped(user, "profile changes")
    b = ctx["body"]
    name = req(b.get("name"), "Name", 120)
    dept = s(b.get("department"), 120)
    phone = s(b.get("phone"), 40)
    conn.execute("UPDATE users SET name=?, department=?, phone=? WHERE id=?",
                 (name, dept, phone, user["id"]))
    if b.get("new_password"):
        # The current one is required even though the session already proves who this is:
        # it is what stops a borrowed, unlocked browser from being turned into a
        # permanent hold on the account.
        if not db.verify_password(b.get("current_password") or "", user["password_hash"]):
            raise ApiError(400, "Your current password is incorrect.")
        problem = password_problem(b["new_password"], user)
        if problem:
            raise ApiError(400, problem)
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (db.hash_password(b["new_password"]), user["id"]))
        # Every other session for this account ends. Changing a password is what you do
        # when you think somebody else may have had it.
        conn.execute("DELETE FROM sessions WHERE user_id=? AND token<>?",
                     (user["id"], ctx["handler"].cookies.get(COOKIE_NAME) or ""))
    row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    return {"user": public_user(row)}


def need_user(ctx):
    if not ctx["user"]:
        raise ApiError(401, "Please sign in to continue.")
    return ctx["user"]


def need_role(ctx, *roles):
    user = need_user(ctx)
    if user["role"] not in roles:
        raise ApiError(403, "Your role does not allow this action.")
    return user


# ------------------------------------------------------------------ lookups
@route("POST", r"/api/approvers")
def api_add_approver(conn, ctx):
    """Name an approver who isn't in the list yet.

    If the address already belongs to an account we reuse it. Otherwise an
    'invited approver' record is created with NO usable password — that person can
    only ever act through the passwordless review link sent to their e-mail.
    """
    user = need_role(ctx, db.ROLE_IC, db.ROLE_ADMIN)
    deny_scoped(user, "adding approvers")
    b = ctx["body"]
    name = req(b.get("name"), "Name", 120)
    email = (req(b.get("email"), "E-mail", 160)).lower()
    if not valid_email(email):
        raise ApiError(400, "Please enter a valid e-mail address.")

    existing = conn.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
    if existing:
        if not existing["active"]:
            raise ApiError(400, "That account is deactivated. Ask an administrator to re-enable it.")
        if existing["id"] == user["id"]:
            raise ApiError(400, "You cannot add yourself as an approver.")
        return {"user": public_user(existing), "created": False}

    cur = conn.execute(
        "INSERT INTO users(name,email,password_hash,role,department,job_title,invited,active,created_at) "
        "VALUES(?,?,?,?,?,?,1,1,?)",
        (name, email, db.hash_password(secrets.token_urlsafe(32)), db.ROLE_MANAGER,
         s(b.get("department"), 120), s(b.get("job_title"), 120) or "Invited approver", db.now_iso()))
    conn.execute("UPDATE users SET approver_level=COALESCE(?,1), passwordless=1 WHERE id=?",
                 (intv(b.get("approver_level")), cur.lastrowid))
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"user": public_user(row), "created": True}


@route("GET", r"/api/lookups")
def api_lookups(conn, ctx):
    need_user(ctx)
    ev_types = [r["value"] for r in conn.execute(
        "SELECT value FROM lookups WHERE kind='event_type' AND active=1 ORDER BY sort,id")]
    cats = [r["value"] for r in conn.execute(
        "SELECT value FROM lookups WHERE kind='vendor_category' AND active=1 ORDER BY sort,id")]
    # managers/admins, invited approvers, and anyone already used as an approver
    approvers = rows(conn.execute(
        "SELECT id,name,email,department,job_title,invited,approver_level,is_final_approver FROM users "
        "WHERE active=1 AND (role IN ('manager','admin') OR invited=1 "
        "      OR id IN (SELECT user_id FROM event_approvers)) ORDER BY invited, name"))
    return {"event_types": ev_types, "vendor_categories": cats, "approvers": approvers,
            "record_approvers": record_approver_defaults(conn),
            "currency": db.get_setting(conn, "currency", "SAR"),
            "default_vat_rate": num(db.get_setting(conn, "default_vat_rate", "15"), 15)}


# ------------------------------------------------------------------- events
def scope_sql(user):
    if scoped(user):
        return "e.id=?", [user["scope_event_id"]]
    if user["role"] == db.ROLE_ADMIN:
        return "1=1", []
    if user["role"] == db.ROLE_MANAGER:
        return ("(e.id IN (SELECT event_id FROM event_approvers WHERE user_id=?) AND e.status<>'draft') "
                "OR e.created_by=?"), [user["id"], user["id"]]
    if user["can_view_all"]:
        return "1=1", []
    return "e.created_by=?", [user["id"]]


@route("GET", r"/api/events")
def api_events(conn, ctx):
    user = need_user(ctx)
    q = ctx["query"]
    where, params = scope_sql(user)
    sql = ["SELECT e.* FROM events e WHERE (%s)" % where]

    search = s(q.get("search", [None])[0], 120)
    if search:
        like = "%" + search.lower() + "%"
        sql.append("AND (lower(e.event_name) LIKE ? OR lower(e.event_number) LIKE ? "
                   "OR lower(e.location) LIKE ? OR e.id IN "
                   "(SELECT event_id FROM vendors WHERE lower(vendor_name) LIKE ?))")
        params += [like, like, like, like]

    status = s(q.get("status", [None])[0], 40)
    if status and status != "all":
        sql.append("AND e.status=?")
        params.append(status)

    kind = s(q.get("kind", [None])[0], 24)
    if kind and kind != "all":
        if kind not in RECORD_KINDS:
            raise ApiError(400, "Unknown kind of record.")
        sql.append("AND COALESCE(e.record_kind,?)=?")
        params += [KIND_ACTIVITY, kind]

    # The dashboard asks by year and month rather than by a range, which is the same
    # question in a friendlier shape.
    year = s(q.get("year", [None])[0], 8)
    month = s(q.get("month", [None])[0], 4)
    if year and year != "all" and re.match(r"^\d{4}$", year):
        if month and month != "all" and re.match(r"^\d{1,2}$", month):
            sql.append("AND e.event_date LIKE ?")
            params.append("%s-%02d-%%" % (year, int(month)))
        else:
            sql.append("AND e.event_date LIKE ?")
            params.append("%s-%%" % year)

    etype = s(q.get("event_type", [None])[0], 60)
    if etype and etype != "all":
        sql.append("AND e.event_type=?")
        params.append(etype)

    approver = intv(q.get("approver", [None])[0])
    if approver:
        sql.append("AND e.id IN (SELECT event_id FROM event_approvers WHERE user_id=?)")
        params.append(approver)

    dfrom = s(q.get("date_from", [None])[0], 20)
    if dfrom:
        sql.append("AND e.event_date >= ?")
        params.append(dfrom)
    dto = s(q.get("date_to", [None])[0], 20)
    if dto:
        sql.append("AND e.event_date <= ?")
        params.append(dto)

    mine = q.get("assigned_to_me", [None])[0]
    if mine == "1":
        sql.append("AND e.id IN (SELECT event_id FROM event_approvers WHERE user_id=?) "
                   "AND e.status IN ('pending_approval','pending_vendor_approval')")
        params.append(user["id"])

    sql.append("ORDER BY e.created_at DESC, e.id DESC")
    found = conn.execute(" ".join(sql), params).fetchall()
    pre = Prefetch(conn, found)
    result = [event_dict(conn, r, pre=pre) for r in found]

    vstatus = s(q.get("vendor_status", [None])[0], 30)
    if vstatus and vstatus != "all":
        def keep(e):
            vsum = e["vendor_summary"]
            if vstatus == "pending":
                return vsum["pending"] > 0
            if vstatus == "approved":
                return vsum["total"] > 0 and vsum["approved"] == vsum["total"]
            if vstatus == "rejected":
                return vsum["rejected"] > 0
            if vstatus == "mixed":
                return vsum["approved"] > 0 and vsum["rejected"] > 0
            return True
        result = [e for e in result if keep(e)]

    return {"events": result}


@route("GET", r"/api/archive")
def api_archive(conn, ctx):
    """Completed-events history for the calendar view, with a photo for each card.

    Anyone granted read-only history access sees every completed event; everyone
    else sees the ones already visible to them.
    """
    user = need_user(ctx)
    if is_archive_viewer(user):
        where, params = "1=1", []
    else:
        where, params = scope_sql(user)
    # The calendar plots what actually happened: events confirmed as executed,
    # plus the ones that were cancelled.
    kind = s(ctx["query"].get("kind", [None])[0], 24)
    extra, args = "", []
    if kind and kind != "all":
        if kind not in RECORD_KINDS:
            raise ApiError(400, "Unknown kind of record.")
        extra, args = " AND COALESCE(e.record_kind,?)=?", [KIND_ACTIVITY, kind]
    sql = ("SELECT e.* FROM events e WHERE (%s) AND (e.executed=1 OR e.status=?)%s ORDER BY "
           "COALESCE(e.execution_date, e.event_date) DESC" % (where, extra))
    evs = conn.execute(sql, params + [db.ST_CANCELLED] + args).fetchall()

    out = []
    pre = Prefetch(conn, evs)
    for ev in evs:
        d = event_dict(conn, ev, pre=pre)
        opt = d["chosen_option_id"]
        # the shared announcement leads, then event pictures, then vendor images
        gallery = [dict(p, src="/api/event-photos/%d/content" % p["id"],
                        source="Announcement" if p["kind"] == "announcement" else "Event photo")
                   for p in (d["announcements"] + d["photos"]) if p["is_image"]]
        d["announcement_files"] = [dict(p, src="/api/event-photos/%d/content" % p["id"])
                                   for p in d["announcements"] if not p["is_image"]]
        for f in conn.execute("SELECT f.id, f.file_name, v.vendor_name FROM vendor_files f "
                              "JOIN vendors v ON v.id=f.vendor_id WHERE f.event_id=? ORDER BY f.id",
                              (ev["id"],)):
            if os.path.splitext(f["file_name"])[1].lower() in IMAGE_EXT:
                gallery.append({"id": f["id"], "file_name": f["file_name"], "caption": None,
                                "src": "/api/vendor-files/%d/content" % f["id"],
                                "source": f["vendor_name"]})
        d["photos"] = gallery
        d["vendor_names"] = [r["vendor_name"] for r in conn.execute(
            "SELECT vendor_name FROM vendors WHERE option_id=? ORDER BY id", (opt,))] if opt else []
        d["calendar_date"] = ev["execution_date"] or ev["event_date"]
        out.append(d)
    return {"events": out, "read_only": is_archive_viewer(user) and user["role"] != db.ROLE_ADMIN,
            "currency": db.get_setting(conn, "currency", "SAR")}


@route("GET", r"/api/stats")
def api_stats(conn, ctx):
    user = need_user(ctx)
    where, params = scope_sql(user)

    # The figures answer "how did this month go", so they take a period. Filtering on
    # the event's own date rather than when it was entered: a recap for October belongs
    # to October however late it was recorded.
    q = ctx["query"]
    year = s(q.get("year", [None])[0], 8)
    month = s(q.get("month", [None])[0], 4)
    extra, args = "", []
    if year and year != "all":
        if not re.match(r"^\d{4}$", year):
            raise ApiError(400, "Year should be four digits.")
        if month and month != "all":
            if not re.match(r"^\d{1,2}$", month) or not 1 <= int(month) <= 12:
                raise ApiError(400, "Month should be 1 to 12.")
            extra, args = " AND e.event_date LIKE ?", ["%s-%02d-%%" % (year, int(month))]
        else:
            extra, args = " AND e.event_date LIKE ?", ["%s-%%" % year]

    kind = s(q.get("kind", [None])[0], 24)
    if kind and kind != "all":
        if kind not in RECORD_KINDS:
            raise ApiError(400, "Unknown kind of record.")
        extra += " AND COALESCE(e.record_kind,?)=?"
        args += [KIND_ACTIVITY, kind]

    evs = conn.execute("SELECT e.* FROM events e WHERE (%s)%s" % (where, extra),
                       params + args).fetchall()
    total_budget = round(sum(float(e["total_budget"] or 0) for e in evs), 2)
    approved_budget = round(sum(float(e["approved_budget"] or 0) for e in evs), 2)
    def count(*st):
        return sum(1 for e in evs if e["status"] in st)
    stats = {
        "total": len(evs),
        "draft": count(db.ST_DRAFT),
        "pending": count(db.ST_PENDING, db.ST_PENDING_VENDORS),
        "approved": count(db.ST_FULL),
        "partially": count(db.ST_PARTIAL),
        "rejected": count(db.ST_REJECTED),
        "cancelled": count(db.ST_CANCELLED),
        "completed": count(db.ST_COMPLETED),
        "total_budget": total_budget,
        "approved_budget": approved_budget,
    }
    if user["role"] in (db.ROLE_MANAGER, db.ROLE_ADMIN):
        stats["awaiting_my_decision"] = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE id IN "
            "(SELECT event_id FROM event_approvers WHERE user_id=?) AND status IN (?,?)",
            (user["id"], db.ST_PENDING, db.ST_PENDING_VENDORS)).fetchone()["c"]
    stats["years"] = [r["y"] for r in conn.execute(
        "SELECT DISTINCT substr(e.event_date,1,4) y FROM events e WHERE (%s) "
        "AND e.event_date IS NOT NULL ORDER BY y DESC" % where, params) if r["y"]]
    stats["period"] = {"year": year or "all", "month": month or "all"}
    return {"stats": stats}


@route("POST", r"/api/events")
def api_create_event(conn, ctx):
    user = need_role(ctx, db.ROLE_IC, db.ROLE_ADMIN)
    b = ctx["body"]
    name = req(b.get("event_name"), "Event Name", 200)
    ts = db.now_iso()
    year = datetime.date.today().year
    if b.get("event_date"):
        try:
            year = int(str(b["event_date"])[:4])
        except ValueError:
            pass
    number = db.next_event_number(conn, year)
    cur = conn.execute(
        "INSERT INTO events(event_number,event_name,event_type,event_date,start_time,end_time,location,"
        "expected_attendees,description,miscellaneous_cost,total_budget,approved_budget,rejected_budget,"
        "status,created_by,approver_id,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,0,0,0,?,?,?,?,?)",
        (number, name, s(b.get("event_type"), 60), iso_date(b.get("event_date"), "Event date"),
         iso_time(b.get("start_time"), "Start time"),
         s(b.get("end_time"), 10), s(b.get("location"), 200), intv(b.get("expected_attendees")),
         s(b.get("description"), 6000), num(b.get("miscellaneous_cost")), db.ST_DRAFT, user["id"],
         intv(b.get("approver_id")), ts, ts))
    eid = cur.lastrowid
    set_approvers(conn, eid, b.get("approver_ids") or ([b.get("approver_id")] if b.get("approver_id") else []))
    ensure_option(conn, eid)
    recalc_budget(conn, eid)
    log(conn, eid, "Event created", user, None, db.ST_DRAFT,
        "Event %s created as a draft" % number)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    return {"event": event_dict(conn, ev, True)}


@route("GET", r"/api/events/(\d+)")
def api_event(conn, ctx, eid):
    user = need_user(ctx)
    ev = guard_view(conn, user, int(eid))
    d = event_dict(conn, ev, True)
    d["can_edit"] = can_edit_event(user, ev)
    d["can_decide"] = can_decide(conn, user, ev)
    d["can_submit"] = (ev["status"] == db.ST_DRAFT and
                       (user["id"] == ev["created_by"] or user["role"] == db.ROLE_ADMIN))
    owns = (user["id"] == ev["created_by"] or user["role"] == db.ROLE_ADMIN) and not scoped(user)
    d["can_complete"] = owns and ev["status"] in (db.ST_FULL, db.ST_PARTIAL)
    d["can_upload"] = owns
    # a finished record stays correctable: dates and details for a past activity are
    # often only settled after the fact
    d["can_edit_record"] = owns and (ev["status"] == db.ST_COMPLETED or bool(ev["executed"]))
    d["missing"] = missing_for_submission(conn, ev) if d["can_submit"] else []
    return {"event": d}


@route("PUT", r"/api/events/(\d+)")
def api_update_event(conn, ctx, eid):
    user = need_user(ctx)
    ev = guard_edit(conn, user, int(eid))
    b = ctx["body"]
    fields = {
        "event_name": req(b.get("event_name"), "Event Name", 200),
        "event_type": s(b.get("event_type"), 60),
        "event_date": iso_date(b.get("event_date"), "Event date"),
        "start_time": iso_time(b.get("start_time"), "Start time"),
        "end_time": iso_time(b.get("end_time"), "End time"),
        "location": s(b.get("location"), 200),
        "expected_attendees": intv(b.get("expected_attendees")),
        "description": s(b.get("description"), 6000),
        "miscellaneous_cost": num(b.get("miscellaneous_cost")),
    }
    conn.execute(
        "UPDATE events SET event_name=?,event_type=?,event_date=?,start_time=?,end_time=?,location=?,"
        "expected_attendees=?,description=?,miscellaneous_cost=?,updated_at=? WHERE id=?",
        tuple(fields.values()) + (db.now_iso(), ev["id"]))
    incoming = b.get("approver_ids")
    if incoming is None and b.get("approver_id"):
        incoming = [b.get("approver_id")]
    if incoming is not None:
        set_approvers(conn, ev["id"], incoming)
    recalc_budget(conn, ev["id"])
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("DELETE", r"/api/events/(\d+)")
def api_delete_event(conn, ctx, eid):
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and not (ev["created_by"] == user["id"] and ev["status"] == db.ST_DRAFT):
        raise ApiError(403, "Only draft events you own can be deleted.")
    for f in conn.execute("SELECT file_path FROM vendor_files WHERE event_id=?", (ev["id"],)):
        _remove_file(f["file_path"])
    conn.execute("DELETE FROM events WHERE id=?", (ev["id"],))
    return {"ok": True}


@route("POST", r"/api/events/(\d+)/submit")
def api_submit(conn, ctx, eid):
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the event owner can submit this event.")
    if ev["status"] != db.ST_DRAFT:
        raise ApiError(409, "Only draft events can be submitted.")
    missing = missing_for_submission(conn, ev)
    if missing:
        raise ApiError(422, "This event cannot be submitted yet — required information is missing.",
                       {"missing": missing})

    ts = db.now_iso()
    supersede_pending(conn, ev["id"])       # a fresh request replaces any earlier draft
    conn.execute("UPDATE events SET status=?, submitted_at=?, selected_option_id=NULL, updated_at=? "
                 "WHERE id=?", (db.ST_PENDING, ts, ts, ev["id"]))
    conn.execute("UPDATE vendors SET approval_status='pending', rejection_reason=NULL, updated_at=? "
                 "WHERE event_id=?", (ts, ev["id"]))
    conn.execute("UPDATE event_options SET status='pending', updated_at=? WHERE event_id=?", (ts, ev["id"]))
    approvers = load_approvers(conn, ev["id"])
    conn.execute("UPDATE event_approvers SET status='pending', decided_at=NULL WHERE event_id=?", (ev["id"],))
    levels = chain_levels(conn, ev["id"])
    first = levels[0]
    conn.execute("UPDATE events SET current_level=? WHERE id=?", (first, ev["id"]))
    conn.execute("INSERT INTO approvals(event_id,approver_id,created_at) VALUES(?,?,?)",
                 (ev["id"], approvers[0]["id"], ts))
    chain_text = " → ".join("%s: %s" % (ordinal(l), ", ".join(
        a["name"] for a in approvers if a["level"] == l)) for l in levels)
    log(conn, ev["id"], "Event submitted for approval", user, db.ST_DRAFT, db.ST_PENDING,
        "Approval chain — %s" % chain_text)
    recalc_budget(conn, ev["id"])

    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    creator = conn.execute("SELECT * FROM users WHERE id=?", (row["created_by"],)).fetchone()

    # only the FIRST level is asked now; later levels are e-mailed as the chain advances
    conn.execute("UPDATE approval_links SET revoked=1 WHERE event_id=? AND revoked=0", (ev["id"],))
    supersede_pending(conn, ev["id"])       # those drafts point at links just revoked
    sent = send_level_request(conn, row, first)
    notify(conn, creator["id"], row["id"], "submitted", "Event submitted",
           "%s was sent to %s (%s approver)%s."
           % (row["event_number"], ", ".join(p["name"] for p in sent), ordinal(first),
              " — %d more level(s) to follow" % (len(levels) - 1) if len(levels) > 1 else ""))
    return {"event": event_dict(conn, row, True)}


@route("GET", r"/api/events/(\d+)/review-links")
def api_review_links(conn, ctx, eid):
    """The personal link for each approver who still has to decide.

    Automatic e-mail is off, so requests are passed on by hand -- and what someone
    actually needs to paste into a chat is the link, not a mail draft. It was only ever
    reachable by opening the draft and reading it out of the message body, which is why
    it could not be found. This exposes nothing new: it is the same link that message
    carries, to the same person who is already the one sending it.
    """
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the requester can share this event's review links.")
    deny_scoped(user, "sharing review links")
    if ev["status"] not in (db.ST_PENDING, db.ST_PENDING_VENDORS):
        return {"links": [], "level": None}

    level = ev["current_level"] or chain_levels(conn, ev["id"])[:1]
    level = level if isinstance(level, int) else (level[0] if level else None)
    if not level:
        return {"links": [], "level": None}

    base = db.get_setting(conn, "app_base_url", "http://localhost:8080").rstrip("/")
    out = []
    for person in level_approvers(conn, ev["id"], level):
        decided = conn.execute(
            "SELECT status FROM event_approvers WHERE event_id=? AND user_id=?",
            (ev["id"], person["id"])).fetchone()
        if decided and decided["status"] != "pending":
            continue
        token = issue_review_link(conn, ev["id"], person["id"],
                                  revoke_previous=False, reuse=True)
        # The message that carries this link, whatever state it is in. A draft already
        # marked sent is still the one to re-download or copy -- the requester may be
        # sending it again, to a different address, or to someone who mislaid it.
        msg = conn.execute(
            "SELECT id, status, subject, sent_at FROM emails WHERE event_id=? AND lower(to_email)=? "
            "AND type='approval_request' AND status<>'superseded' "
            "ORDER BY created_at DESC, id DESC",
            (ev["id"], (person["email"] or "").lower())).fetchone()
        out.append({"name": person["name"], "email": person["email"],
                    "level": level, "is_final": bool(person["is_final"]),
                    "url": "%s/#/review/%s" % (base, token),
                    "message_id": msg["id"] if msg else None,
                    "message_status": msg["status"] if msg else None,
                    "shared": bool(msg and msg["status"] == "sent_manually"),
                    "shared_at": msg["sent_at"] if msg else None,
                    "subject": msg["subject"] if msg else None})
    return {"links": out, "level": level, "ordinal": ordinal(level)}


@route("POST", r"/api/events/(\d+)/messages/rebuild")
def api_rebuild_messages(conn, ctx, eid):
    """Rebuild the waiting draft from the event as it stands now.

    A draft is written when the request moves to a level and then sits in the outbox
    unchanged. Anything that happened afterwards -- a vendor edited, an earlier
    approver's choice recorded, the template itself improved -- is not in it. Rebuilding
    keeps the same review link, so a link already shared still opens; only the message
    around it is refreshed.
    """
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the requester can rebuild this event's messages.")
    deny_scoped(user, "rebuilding messages")
    if ev["status"] not in (db.ST_PENDING, db.ST_PENDING_VENDORS):
        raise ApiError(409, "This event is not waiting on an approver.")

    level = ev["current_level"] or (chain_levels(conn, ev["id"]) or [None])[0]
    if not level:
        raise ApiError(409, "This event has no approval level to send to.")
    supersede_pending(conn, ev["id"])
    sent = send_level_request(conn, ev, level, reuse_links=True)
    log(conn, ev["id"], "Request draft rebuilt", user, ev["status"], ev["status"],
        "Refreshed for %s" % (", ".join(p["name"] for p in sent) or "nobody"))
    return {"ok": True, "rebuilt": len(sent),
            "names": [p["name"] for p in sent]}


@route("GET", r"/api/events/(\d+)/messages")
def api_event_messages(conn, ctx, eid):
    """The messages this event generated, ready to hand to Outlook via mailto:.

    Only the requester and administrators can see them — they are the people who
    need to pass the request on while automatic delivery is off.
    """
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the requester can send this event's messages.")
    deny_scoped(user, "sending messages")
    items = rows(conn.execute(
        "SELECT id,to_email,to_name,cc,subject,body_text,body_html,type,status,created_at,sent_at "
        "FROM emails WHERE event_id=? AND status<>'superseded' ORDER BY id DESC", (ev["id"],)))
    # One live draft per recipient per kind: a re-issued request replaces its predecessor.
    # Sent messages are always kept, being a record of what actually went out.
    live, seen = [], set()
    for m in items:
        if m["status"] == "queued":
            key = (m["to_email"], m["type"])
            if key in seen:
                continue
            seen.add(key)
        live.append(m)
    items = live
    for i in items:
        i["body_html"] = mailer.inline_logo(i["body_html"])
    return {"messages": items,
            "smtp_enabled": db.get_setting(conn, "smtp_enabled", "0") == "1"}


@route("GET", r"/api/events/(\d+)/messages/(\d+)/eml")
def api_message_eml(conn, ctx, eid, mid):
    """Download the branded message as an Outlook draft (.eml, X-Unsent)."""
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the requester can send this event's messages.")
    deny_scoped(user, "sending messages")
    row = conn.execute("SELECT * FROM emails WHERE id=? AND event_id=?", (int(mid), ev["id"])).fetchone()
    if not row:
        raise ApiError(404, "Message not found.")
    data = mailer.build_eml(row["subject"], row["to_email"], row["to_name"],
                            row["body_html"], row["body_text"], cc=row["cc"])
    name = re.sub(r"[^\w\- ]", "", "%s - %s" % (ev["event_number"], row["to_name"] or row["to_email"]))
    ctx["raw"] = (data, "message/rfc822", name + ".eml", True)
    return None


@route("GET", r"/api/admin/emails/(\d+)/eml")
def api_admin_message_eml(conn, ctx, mid):
    need_role(ctx, db.ROLE_ADMIN)
    row = conn.execute("SELECT * FROM emails WHERE id=?", (int(mid),)).fetchone()
    if not row:
        raise ApiError(404, "Message not found.")
    data = mailer.build_eml(row["subject"], row["to_email"], row["to_name"],
                            row["body_html"], row["body_text"], cc=row["cc"])
    ctx["raw"] = (data, "message/rfc822",
                  re.sub(r"[^\w\- ]", "", row["subject"])[:60] + ".eml", True)
    return None


@route("POST", r"/api/events/(\d+)/messages/(\d+)/mark-sent")
def api_mark_sent(conn, ctx, eid, mid):
    """Record that the request actually reached the approver, and how.

    Downloading the draft sets this by itself, because downloading it is what someone
    does on the way to sending it. Sharing the link does not: it is copied and pasted
    into a chat, and nothing here can see that happen. So it can also be said outright,
    and taken back -- a tick made in error should not be permanent.

    Without it, "waiting for approval" and "not sent yet" looked identical from this
    page, and the only way to tell them apart was to remember.
    """
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the requester can do this.")
    deny_scoped(user, "marking a request as sent")
    row = conn.execute("SELECT * FROM emails WHERE id=? AND event_id=?", (int(mid), ev["id"])).fetchone()
    if not row:
        raise ApiError(404, "Message not found.")

    b = ctx["body"] or {}
    shared = b.get("shared", True)
    how = s(b.get("how"), 20) or "e-mail"
    if shared:
        when = db.now_iso()
        conn.execute("UPDATE emails SET status='sent_manually', sent_at=? WHERE id=?",
                     (when, row["id"]))
        log(conn, ev["id"], "Approval request shared", user, ev["status"], ev["status"],
            "Sent to %s by %s, as a %s" % (row["to_email"], user["name"],
                                           "link" if how == "link" else "designed e-mail"))
        return {"ok": True, "shared": True, "sent_at": when}

    conn.execute("UPDATE emails SET status='queued', sent_at=NULL WHERE id=?", (row["id"],))
    log(conn, ev["id"], "Sharing un-marked", user, ev["status"], ev["status"],
        "%s is waiting to be sent to %s again" % (user["name"], row["to_email"]))
    return {"ok": True, "shared": False, "sent_at": None}


@route("POST", r"/api/events/(\d+)/withdraw")
def api_withdraw(conn, ctx, eid):
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the event owner can withdraw this event.")
    if ev["status"] not in (db.ST_PENDING, db.ST_PENDING_VENDORS):
        raise ApiError(409, "Only events awaiting approval can be withdrawn.")
    ts = db.now_iso()
    conn.execute("UPDATE approvals SET event_decision='withdrawn', decision_date=? "
                 "WHERE event_id=? AND event_decision IS NULL", (ts, ev["id"]))
    conn.execute("UPDATE vendors SET approval_status='pending', rejection_reason=NULL, updated_at=? "
                 "WHERE event_id=?", (ts, ev["id"]))
    conn.execute("UPDATE event_options SET status='pending', updated_at=? WHERE event_id=?", (ts, ev["id"]))
    conn.execute("UPDATE events SET status=?, submitted_at=NULL, approved_budget=0, rejected_budget=0, "
                 "selected_option_id=NULL, updated_at=? WHERE id=?", (db.ST_DRAFT, ts, ev["id"]))
    conn.execute("UPDATE approval_links SET revoked=1 WHERE event_id=? AND revoked=0", (ev["id"],))
    log(conn, ev["id"], "Approval request withdrawn", user, ev["status"], db.ST_DRAFT,
        s(ctx["body"].get("comment"), 500) or "Returned to draft for editing")
    for uid in approver_ids(conn, ev["id"]):
        notify(conn, uid, ev["id"], "withdrawn", "Approval request withdrawn",
               "%s — %s was withdrawn by the requester." % (ev["event_number"], ev["event_name"]))
    recalc_budget(conn, ev["id"])
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("POST", r"/api/events/(\d+)/cancel")
def api_cancel(conn, ctx, eid):
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "You cannot cancel this event.")
    if ev["status"] in (db.ST_CANCELLED, db.ST_COMPLETED):
        raise ApiError(409, "This event is already closed.")
    reason = req(ctx["body"].get("reason"), "Cancellation reason", 800)
    ts = db.now_iso()
    conn.execute("UPDATE events SET status=?, updated_at=? WHERE id=?", (db.ST_CANCELLED, ts, ev["id"]))
    conn.execute("UPDATE approvals SET event_decision='cancelled', decision_date=? "
                 "WHERE event_id=? AND event_decision IS NULL", (ts, ev["id"]))
    conn.execute("UPDATE approval_links SET revoked=1 WHERE event_id=? AND revoked=0", (ev["id"],))
    log(conn, ev["id"], "Event cancelled", user, ev["status"], db.ST_CANCELLED, reason)
    for uid in set(approver_ids(conn, ev["id"]) + [ev["created_by"]]):
        if uid != user["id"]:
            notify(conn, uid, ev["id"], "cancelled", "Event cancelled",
                   "%s — %s was cancelled." % (ev["event_number"], ev["event_name"]))
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("POST", r"/api/events/(\d+)/complete")
def api_complete(conn, ctx, eid):
    """Mark an approved event as executed: records whether it happened and when."""
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    deny_scoped(user, "closing an event")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the event owner can mark this event as executed.")
    if ev["status"] not in (db.ST_FULL, db.ST_PARTIAL):
        raise ApiError(409, "Only approved events can be marked as executed.")

    b = ctx["body"]
    exec_date = s(b.get("execution_date"), 20) or (ev["event_date"] or db.now_iso()[:10])
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", exec_date):
        raise ApiError(400, "Please provide a valid execution date (YYYY-MM-DD).")
    notes = s(b.get("execution_notes"), 2000)
    ts = db.now_iso()
    conn.execute("UPDATE events SET status=?, executed=1, execution_date=?, execution_notes=?, updated_at=? "
                 "WHERE id=?", (db.ST_COMPLETED, exec_date, notes, ts, ev["id"]))
    log(conn, ev["id"], "Event executed", user, ev["status"], db.ST_COMPLETED,
        "Executed on %s%s" % (exec_date, (" — " + notes) if notes else ""))

    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    vendors = conn.execute("SELECT * FROM vendors WHERE option_id=? ORDER BY id",
                           (chosen_option_id(conn, row),)).fetchall()
    currency = db.get_setting(conn, "currency", "SAR")
    base = db.get_setting(conn, "app_base_url", "http://localhost:8080")
    subject, body, body_text = mailer.build_executed(row, vendors, user, base, currency)

    # close the loop for everyone involved
    for uid in set(approver_ids(conn, row["id"]) + [row["created_by"]]):
        if not uid:
            continue
        person = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if person and person["id"] != user["id"]:
            mailer.queue(conn, person["email"], person["name"], subject, body, "executed",
                         row["id"], body_text, admin_cc(conn, person["email"]))
        notify(conn, uid, row["id"], "executed", "Event executed",
               "%s — %s was executed on %s." % (row["event_number"], row["event_name"],
                                                mailer.fmt_date(exec_date)))
    return {"event": event_dict(conn, row, True)}


# ------------------------------------------------------------------ options
@route("POST", r"/api/events/(\d+)/options")
def api_add_option(conn, ctx, eid):
    user = need_user(ctx)
    ev = guard_edit(conn, user, int(eid))
    count = conn.execute("SELECT COUNT(*) c FROM event_options WHERE event_id=?", (ev["id"],)).fetchone()["c"]
    if count >= 6:
        raise ApiError(400, "An event can hold at most 6 alternative options.")
    name = s(ctx["body"].get("name"), 80) or ("Option %s" % option_letter(count))
    ts = db.now_iso()
    delivery = num(ctx["body"].get("delivery_cost"))
    if delivery < 0:
        raise ApiError(400, "The delivery cost cannot be negative.")
    oid = conn.execute(
        "INSERT INTO event_options(event_id,name,description,delivery_cost,sort,status,"
        "created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)",
        (ev["id"], name, s(ctx["body"].get("description"), 1000), delivery, count, ts, ts)).lastrowid
    recalc_budget(conn, ev["id"])
    log(conn, ev["id"], "Option added", user, ev["status"], ev["status"], name)
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True), "option_id": oid}


@route("PUT", r"/api/options/(\d+)")
def api_update_option(conn, ctx, oid):
    user = need_user(ctx)
    opt = conn.execute("SELECT * FROM event_options WHERE id=?", (int(oid),)).fetchone()
    if not opt:
        raise ApiError(404, "Option not found.")
    ev = guard_edit(conn, user, opt["event_id"])
    b = ctx["body"]
    # Absent means "leave it": the option editor sends a name and a description, and a
    # delivery cost that was never typed must not silently become zero.
    delivery = num(b.get("delivery_cost"), float(opt["delivery_cost"] or 0))         if b.get("delivery_cost") is not None else float(opt["delivery_cost"] or 0)
    if delivery < 0:
        raise ApiError(400, "The delivery cost cannot be negative.")
    conn.execute("UPDATE event_options SET name=?, description=?, delivery_cost=?, "
                 "updated_at=? WHERE id=?",
                 (req(b.get("name"), "Option name", 80),
                  s(b.get("description"), 1000), delivery, db.now_iso(), opt["id"]))
    recalc_budget(conn, ev["id"])
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("DELETE", r"/api/options/(\d+)")
def api_delete_option(conn, ctx, oid):
    user = need_user(ctx)
    opt = conn.execute("SELECT * FROM event_options WHERE id=?", (int(oid),)).fetchone()
    if not opt:
        raise ApiError(404, "Option not found.")
    ev = guard_edit(conn, user, opt["event_id"])
    count = conn.execute("SELECT COUNT(*) c FROM event_options WHERE event_id=?", (ev["id"],)).fetchone()["c"]
    if count <= 1:
        raise ApiError(400, "An event must keep at least one option.")
    for f in conn.execute("SELECT file_path FROM vendor_files WHERE vendor_id IN "
                          "(SELECT id FROM vendors WHERE option_id=?)", (opt["id"],)):
        _remove_file(f["file_path"])
    conn.execute("DELETE FROM vendors WHERE option_id=?", (opt["id"],))
    conn.execute("DELETE FROM event_options WHERE id=?", (opt["id"],))
    recalc_budget(conn, ev["id"])
    log(conn, ev["id"], "Option removed", user, ev["status"], ev["status"], opt["name"])
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


def resolve_option(conn, ev, option_id):
    """Validate that an option belongs to this event; fall back to the first option."""
    if option_id:
        opt = conn.execute("SELECT * FROM event_options WHERE id=? AND event_id=?",
                           (option_id, ev["id"])).fetchone()
        if not opt:
            raise ApiError(400, "That option does not belong to this event.")
        return opt["id"]
    return ensure_option(conn, ev["id"])


# ------------------------------------------------------------------ vendors
def money_given(value, label, existing=None):
    """A figure that was actually given, or a refusal.

    num() falls back to a default, which is right for an optional number and wrong for a
    price. "abc" and "1; DROP TABLE vendors; --" both became 0.00, so a quotation was
    recorded as free and flowed into the option total and the approved budget looking
    exactly like a real zero. Nothing was injected -- every value is bound -- but a
    number the hub could not read should be refused, not invented.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if existing is None:
            raise ApiError(400, "%s is required." % label)
        return float(existing)
    try:
        n = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ApiError(400, "%s must be a number." % label)
    if n != n or n in (float("inf"), float("-inf")):
        raise ApiError(400, "%s must be a number." % label)
    return round(n, 2)


def _vendor_totals(b, default_vat, existing=None):
    """A line's numbers: unit price, quantity, net, VAT, gross.

    A quotation is nearly always "N of something at X each", and storing only the sum
    loses the two figures people actually negotiate and compare across vendors. The net
    total is still kept in quotation_amount, so everything downstream -- option totals,
    approved budgets, the export -- reads exactly as it did.

    A caller that sends only quotation_amount still works: that is one of something at
    that price, which is what the older forms and the import scripts mean.
    """
    existing = existing or {}
    stated_units = b.get("unit_price") is not None or b.get("quantity") is not None
    if stated_units:
        unit = money_given(b.get("unit_price"), "The price per unit",
                           existing.get("unit_price"))
        qty = money_given(b.get("quantity"), "The quantity", existing.get("quantity") or 1)
        if unit < 0:
            raise ApiError(400, "The unit price cannot be negative.")
        if qty <= 0:
            raise ApiError(400, "The quantity must be at least 1.")
        amount = round(unit * qty, 2)
    else:
        amount = money_given(b.get("quotation_amount"), "The quotation amount",
                             existing.get("quotation_amount") or 0)
        unit, qty = amount, 1
    if amount < 0:
        raise ApiError(400, "The quotation amount cannot be negative.")
    vat_rate = num(b.get("vat_rate"), default_vat)
    if vat_rate < 0 or vat_rate > 100:
        raise ApiError(400, "The VAT rate must be between 0 and 100.")
    vat = round(amount * vat_rate / 100.0, 2)
    return amount, vat_rate, vat, round(amount + vat, 2), unit, qty


@route("POST", r"/api/events/(\d+)/vendors")
def api_add_vendor(conn, ctx, eid):
    user = need_user(ctx)
    ev = guard_edit(conn, user, int(eid))
    b = ctx["body"]
    name = req(b.get("vendor_name"), "Vendor Name", 160)
    email = s(b.get("contact_email"), 160)
    if email and not valid_email(email):
        raise ApiError(400, "The vendor contact e-mail is not valid.")
    default_vat = num(db.get_setting(conn, "default_vat_rate", "15"), 15)
    amount, vat_rate, vat, total, unit, qty = _vendor_totals(b, default_vat)
    option_id = resolve_option(conn, ev, intv(b.get("option_id")))
    ts = db.now_iso()
    cur = conn.execute(
        "INSERT INTO vendors(event_id,option_id,vendor_name,category,contact_name,contact_email,contact_phone,"
        "description,unit_price,quantity,quotation_amount,vat_rate,vat,total_amount,approval_status,"
        "created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
        (ev["id"], option_id, name, s(b.get("category"), 60), s(b.get("contact_name"), 120), email,
         s(b.get("contact_phone"), 40), s(b.get("description"), 3000),
         unit, qty, amount, vat_rate, vat, total, ts, ts))
    vid = cur.lastrowid
    for link in (b.get("links") or [])[:20]:
        url = safe_url(link.get("url") if isinstance(link, dict) else link)
        if url:
            conn.execute("INSERT INTO vendor_links(vendor_id,event_id,label,url,created_at) VALUES(?,?,?,?,?)",
                         (vid, ev["id"], s((link or {}).get("label"), 120) if isinstance(link, dict) else None,
                          url, ts))
    recalc_budget(conn, ev["id"])
    log(conn, ev["id"], "Vendor added", user, ev["status"], ev["status"],
        "%s — %s" % (name, mailer.money(total, db.get_setting(conn, "currency", "SAR"))))
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True), "vendor_id": vid}


@route("PUT", r"/api/vendors/(\d+)")
def api_update_vendor(conn, ctx, vid):
    user = need_user(ctx)
    v = conn.execute("SELECT * FROM vendors WHERE id=?", (int(vid),)).fetchone()
    if not v:
        raise ApiError(404, "Vendor not found.")
    ev = guard_edit(conn, user, v["event_id"])
    b = ctx["body"]
    name = req(b.get("vendor_name"), "Vendor Name", 160)
    email = s(b.get("contact_email"), 160)
    if email and not valid_email(email):
        raise ApiError(400, "The vendor contact e-mail is not valid.")
    default_vat = num(db.get_setting(conn, "default_vat_rate", "15"), 15)
    amount, vat_rate, vat, total, unit, qty = _vendor_totals(b, default_vat, dict(v))
    option_id = resolve_option(conn, ev, intv(b.get("option_id")) or v["option_id"])
    conn.execute(
        "UPDATE vendors SET option_id=?,vendor_name=?,category=?,contact_name=?,contact_email=?,contact_phone=?,"
        "description=?,unit_price=?,quantity=?,quotation_amount=?,vat_rate=?,vat=?,total_amount=?,"
        "updated_at=? WHERE id=?",
        (option_id, name, s(b.get("category"), 60), s(b.get("contact_name"), 120), email,
         s(b.get("contact_phone"), 40), s(b.get("description"), 3000), unit, qty, amount,
         vat_rate, vat, total, db.now_iso(), v["id"]))
    recalc_budget(conn, ev["id"])
    log(conn, ev["id"], "Vendor updated", user, ev["status"], ev["status"], name)
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("DELETE", r"/api/vendors/(\d+)")
def api_delete_vendor(conn, ctx, vid):
    user = need_user(ctx)
    v = conn.execute("SELECT * FROM vendors WHERE id=?", (int(vid),)).fetchone()
    if not v:
        raise ApiError(404, "Vendor not found.")
    ev = guard_edit(conn, user, v["event_id"])
    for f in conn.execute("SELECT file_path FROM vendor_files WHERE vendor_id=?", (v["id"],)):
        _remove_file(f["file_path"])
    conn.execute("DELETE FROM vendors WHERE id=?", (v["id"],))
    recalc_budget(conn, ev["id"])
    log(conn, ev["id"], "Vendor removed", user, ev["status"], ev["status"], v["vendor_name"])
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


# ------------------------------------------------------- vendor files/links
def admin_cc(conn, exclude_email=None):
    """The IC administrators, copied on every decision so IC keeps the full picture."""
    rows_ = conn.execute("SELECT email FROM users WHERE role='admin' AND active=1").fetchall()
    out = [r["email"] for r in rows_
           if r["email"] and r["email"].lower() != (exclude_email or "").lower()]
    return ", ".join(out) or None


def ic_recipient(conn):
    """The IC mailbox that owns the process — the addressee of every final decision."""
    r = conn.execute("SELECT name, email FROM users WHERE role='admin' AND active=1 "
                     "ORDER BY id LIMIT 1").fetchone()
    return (r["email"], r["name"]) if r else (None, None)


def decision_audience(conn, event_id, creator, exclude_email=None):
    """Who is copied on a final decision: the event creator, then the whole chain.

    Only the outcome of the chain is circulated this widely — the step-by-step requests
    go to the approver whose turn it is, copying just those already through.
    """
    seen, out = set(), []
    candidates = [creator["email"]] + [r["email"] for r in conn.execute(
        "SELECT u.email FROM event_approvers a JOIN users u ON u.id=a.user_id "
        "WHERE a.event_id=? ORDER BY a.level, a.sort, a.id", (event_id,))]
    for email in candidates:
        key = (email or "").lower()
        if email and key not in seen and key != (exclude_email or "").lower():
            seen.add(key)
            out.append(email)
    return ", ".join(out) or None


def keep_blob(conn, table, row_id, raw):
    """Hosted: the file lives in the row. Local: it is already on disk."""
    if db.using_postgres():
        conn.execute("UPDATE %s SET content=? WHERE id=?" % table, (raw, row_id))


def load_blob(conn, table, row):
    """Return the bytes for a stored file, from the row or from disk."""
    if db.using_postgres():
        got = conn.execute("SELECT content FROM %s WHERE id=?" % table, (row["id"],)).fetchone()
        data = got and got["content"]
        if data is None:
            raise ApiError(404, "The stored file is missing.")
        return bytes(data)
    path = os.path.join(db.UPLOAD_DIR, os.path.basename(row["file_path"]))
    if not os.path.isfile(path):
        raise ApiError(404, "The stored file is missing from the server.")
    with open(path, "rb") as fh:
        return fh.read()


def _remove_file(rel_path):
    if db.using_postgres():
        return          # the blob goes with the row
    try:
        full = os.path.join(db.UPLOAD_DIR, os.path.basename(rel_path or ""))
        if os.path.isfile(full):
            os.remove(full)
    except OSError:
        pass


def clean_filename(name):
    """A display name that is only ever a name.

    The stored file is given a random name of our own, so a path in here cannot make
    the server write anywhere unexpected. It is still echoed back to browsers in
    Content-Disposition and shown in galleries, so it keeps nothing that looks like a
    path or a control character -- untrusted text should not be carried verbatim in a
    field whose whole meaning is "this is a file name".
    """
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip(". ")
    return name[:200] or "file"


def _store_upload(b):
    filename = clean_filename(req(b.get("filename"), "File name", 240))
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ApiError(400, "Unsupported file type. Allowed: PDF, JPG, PNG, DOC, DOCX, XLS, XLSX.")
    data = b.get("data") or ""
    if "," in data[:120] and data[:5].lower() == "data:":
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data, validate=False)
    except Exception:
        raise ApiError(400, "The uploaded file could not be read.")
    if not raw:
        raise ApiError(400, "The uploaded file is empty.")
    if len(raw) > upload_limit():
        # The number differs between the office hub and the hosted one, so it has to be
        # read rather than written: on the hosted deployment "15 MB" was simply untrue.
        raise ApiError(413, "The file exceeds the %.1f MB limit here."
                       % (upload_limit() / (1024.0 * 1024.0)))
    stored = "%s%s" % (secrets.token_hex(16), ext)
    if not db.using_postgres():
        os.makedirs(db.UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(db.UPLOAD_DIR, stored), "wb") as fh:
            fh.write(raw)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return stored, filename, mime, len(raw), raw


@route("POST", r"/api/vendors/(\d+)/files")
def api_vendor_upload(conn, ctx, vid):
    user = need_user(ctx)
    v = conn.execute("SELECT * FROM vendors WHERE id=?", (int(vid),)).fetchone()
    if not v:
        raise ApiError(404, "Vendor not found.")
    ev = guard_edit(conn, user, v["event_id"])
    b = ctx["body"]
    kind = s(b.get("kind"), 20) or "attachment"
    if kind not in ("quotation", "image", "attachment"):
        kind = "attachment"
    stored, filename, mime, size, raw = _store_upload(b)
    ts = db.now_iso()
    if kind == "quotation":
        old = conn.execute("SELECT file_path FROM vendor_files WHERE vendor_id=? AND kind='quotation'",
                           (v["id"],)).fetchall()
        for o in old:
            _remove_file(o["file_path"])
        conn.execute("DELETE FROM vendor_files WHERE vendor_id=? AND kind='quotation'", (v["id"],))
        conn.execute("UPDATE vendors SET quotation_file=?,quotation_name=?,quotation_mime=?,"
                     "quotation_size=?,updated_at=? WHERE id=?",
                     (stored, filename, mime, size, ts, v["id"]))
    fid = conn.execute(
        "INSERT INTO vendor_files(vendor_id,event_id,kind,file_path,file_name,mime,size,caption,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (v["id"], v["event_id"], kind, stored, filename, mime, size, s(b.get("caption"), 200), ts)).lastrowid
    keep_blob(conn, "vendor_files", fid, raw)
    log(conn, ev["id"], "Vendor file uploaded", user, ev["status"], ev["status"],
        "%s — %s (%s)" % (v["vendor_name"], filename, kind))
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("DELETE", r"/api/vendor-files/(\d+)")
def api_delete_vendor_file(conn, ctx, fid):
    user = need_user(ctx)
    f = conn.execute("SELECT * FROM vendor_files WHERE id=?", (int(fid),)).fetchone()
    if not f:
        raise ApiError(404, "File not found.")
    ev = guard_edit(conn, user, f["event_id"])
    _remove_file(f["file_path"])
    conn.execute("DELETE FROM vendor_files WHERE id=?", (f["id"],))
    if f["kind"] == "quotation":
        conn.execute("UPDATE vendors SET quotation_file=NULL,quotation_name=NULL,quotation_mime=NULL,"
                     "quotation_size=NULL WHERE id=?", (f["vendor_id"],))
    log(conn, ev["id"], "Vendor file removed", user, ev["status"], ev["status"], f["file_name"])
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


# --------------------------------------------------------- event pictures
def guard_photos(conn, user, event_id):
    """Photos may be added at ANY stage — including after the event has run."""
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    deny_scoped(user, "adding pictures")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the event owner can manage its pictures.")
    return ev


ANNOUNCEMENT_EXT = IMAGE_EXT | {".pdf", ".ppt", ".pptx", ".doc", ".docx"}


@route("POST", r"/api/events/(\d+)/photos")
def api_add_photo(conn, ctx, eid):
    """Event pictures, or the announcement template that was shared with employees."""
    user = need_user(ctx)
    ev = guard_photos(conn, user, int(eid))
    b = ctx["body"]
    kind = "announcement" if s(b.get("kind"), 20) == "announcement" else "photo"
    stored, filename, mime, size, raw = _store_upload(b)
    ext = os.path.splitext(filename)[1].lower()
    allowed = ANNOUNCEMENT_EXT if kind == "announcement" else IMAGE_EXT
    if ext not in allowed:
        _remove_file(stored)
        raise ApiError(400, "Announcements accept images, PDF, PPT or DOC files."
                       if kind == "announcement" else "Pictures must be JPG, PNG, GIF or WEBP.")
    pid = conn.execute(
        "INSERT INTO event_photos(event_id,kind,file_path,file_name,mime,size,caption,uploaded_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (ev["id"], kind, stored, filename, mime, size, s(b.get("caption"), 200), user["id"],
         db.now_iso())).lastrowid
    keep_blob(conn, "event_photos", pid, raw)
    log(conn, ev["id"], "Announcement template added" if kind == "announcement" else "Event picture added",
        user, ev["status"], ev["status"], filename)
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("DELETE", r"/api/event-photos/(\d+)")
def api_del_photo(conn, ctx, pid):
    user = need_user(ctx)
    ph = conn.execute("SELECT * FROM event_photos WHERE id=?", (int(pid),)).fetchone()
    if not ph:
        raise ApiError(404, "Picture not found.")
    ev = guard_photos(conn, user, ph["event_id"])
    _remove_file(ph["file_path"])
    conn.execute("DELETE FROM event_photos WHERE id=?", (ph["id"],))
    log(conn, ev["id"], "Event picture removed", user, ev["status"], ev["status"], ph["file_name"])
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("GET", r"/api/event-photos/(\d+)/content")
def api_photo_content(conn, ctx, pid):
    user = need_user(ctx)
    ph = conn.execute("SELECT * FROM event_photos WHERE id=?", (int(pid),)).fetchone()
    if not ph:
        raise ApiError(404, "Picture not found.")
    ev = conn.execute("SELECT * FROM events WHERE id=?", (ph["event_id"],)).fetchone()
    if not ev or not can_view_event(conn, user, ev):
        raise ApiError(403, "You do not have permission to open this picture.")
    data = load_blob(conn, "event_photos", ph)
    ctx["raw"] = (data, ph["mime"] or "image/jpeg", ph["file_name"],
                  ctx["query"].get("download", ["0"])[0] == "1")
    return None


@route("POST", r"/api/vendors/(\d+)/links")
def api_add_link(conn, ctx, vid):
    user = need_user(ctx)
    v = conn.execute("SELECT * FROM vendors WHERE id=?", (int(vid),)).fetchone()
    if not v:
        raise ApiError(404, "Vendor not found.")
    ev = guard_edit(conn, user, v["event_id"])
    url = safe_url(req(ctx["body"].get("url"), "Link URL", 1000))
    label = s(ctx["body"].get("label"), 120)
    conn.execute("INSERT INTO vendor_links(vendor_id,event_id,label,url,created_at) VALUES(?,?,?,?,?)",
                 (v["id"], v["event_id"], label, url, db.now_iso()))
    log(conn, ev["id"], "Vendor link added", user, ev["status"], ev["status"],
        "%s — %s" % (v["vendor_name"], label or url))
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("DELETE", r"/api/vendor-links/(\d+)")
def api_delete_link(conn, ctx, lid):
    user = need_user(ctx)
    l = conn.execute("SELECT * FROM vendor_links WHERE id=?", (int(lid),)).fetchone()
    if not l:
        raise ApiError(404, "Link not found.")
    ev = guard_edit(conn, user, l["event_id"])
    conn.execute("DELETE FROM vendor_links WHERE id=?", (l["id"],))
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


# ----------------------------------------------------------------- decision
@route("POST", r"/api/events/(\d+)/decision")
def api_decision(conn, ctx, eid):
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    if not is_approver(conn, user, ev):
        raise ApiError(403, "Only an assigned approver can submit a decision for this event.")
    if ev["status"] not in (db.ST_PENDING, db.ST_PENDING_VENDORS):
        raise ApiError(409, "A decision has already been recorded for this event.")
    mine = my_assignment(conn, user, ev)
    my_level = mine["level"]
    if mine["status"] != "pending":
        raise ApiError(409, "You have already recorded your decision on this event.")
    if ev["current_level"] and my_level != ev["current_level"]:
        waiting = ", ".join(p["name"] for p in level_approvers(conn, ev["id"], ev["current_level"]))
        raise ApiError(409, "This request is with the %s approver (%s). It reaches you once they approve."
                       % (ordinal(ev["current_level"]), waiting))

    b = ctx["body"]
    decision = (s(b.get("event_decision"), 20) or "").lower()
    if decision not in ("approved", "rejected"):
        raise ApiError(400, "Please approve or reject the event before submitting.")
    event_reason = s(b.get("event_rejection_reason"), 2000)
    if decision == "rejected" and not event_reason:
        raise ApiError(400, "A rejection reason is mandatory when rejecting the event.")

    # ---- which alternative option is being approved
    options = conn.execute("SELECT * FROM event_options WHERE event_id=? ORDER BY sort,id",
                           (ev["id"],)).fetchall()
    opt_ids = [o["id"] for o in options]
    # More than one option can be approved: an event may genuinely run two packages, and
    # then both are happening rather than one being preferred. A single id is still
    # accepted, because that is what one approved option looks like and what every
    # older client sends.
    wanted = b.get("selected_option_ids")
    if wanted is None:
        one = intv(b.get("selected_option_id"))
        wanted = [one] if one else []
    chosen = []
    for oid in wanted:
        oid = intv(oid)
        if not oid or oid in chosen:
            continue
        if oid not in opt_ids:
            raise ApiError(400, "That option does not belong to this event.")
        chosen.append(oid)

    if decision == "approved" and len(options) > 1 and not chosen:
        raise ApiError(400, "Please choose which option or options you are approving "
                            "before submitting.")
    if not chosen:
        chosen = opt_ids[:1]
    # keep them in the event's own order, so "Option A + Option C" never reads backwards
    chosen = [oid for oid in opt_ids if oid in chosen]
    selected = chosen[0] if chosen else None

    marks = ",".join(["?"] * len(chosen)) or "NULL"
    vendors = conn.execute(
        "SELECT * FROM vendors WHERE option_id IN (%s) ORDER BY option_id, id" % marks,
        tuple(chosen)).fetchall() if chosen else []
    by_id = {v["id"]: v for v in vendors}
    incoming = {}
    for item in (b.get("vendors") or []):
        vid = intv((item or {}).get("id"))
        if vid not in by_id:
            raise ApiError(400, "A vendor decision refers to a vendor that is not part of "
                                "the options being approved.")
        d = (s(item.get("decision"), 20) or "pending").lower()
        if d not in ("approved", "rejected", "not_selected", "pending"):
            raise ApiError(400, "Invalid vendor decision.")
        reason = s(item.get("rejection_reason"), 2000)
        if d == "rejected" and not reason:
            raise ApiError(400, "A rejection reason is mandatory for vendor \"%s\"."
                           % by_id[vid]["vendor_name"])
        incoming[vid] = (d, reason)

    ts = db.now_iso()
    currency = db.get_setting(conn, "currency", "SAR")

    # ---- record which options won; every other option's vendors become "not selected"
    if chosen:
        conn.execute("UPDATE events SET selected_option_id=? WHERE id=?", (selected, ev["id"]))
        names = [o["name"] for o in options if o["id"] in chosen]
        chosen_name = " + ".join(names) or "Option A"
        for o in options:
            if o["id"] in chosen:
                conn.execute("UPDATE event_options SET status='selected', updated_at=? WHERE id=?",
                             (ts, o["id"]))
            else:
                conn.execute("UPDATE event_options SET status='not_selected', updated_at=? WHERE id=?",
                             (ts, o["id"]))
                # Who set this package aside belongs in the record. Without a row here the
                # vendor keeps whatever decision was last made about it -- which is often
                # the opposite decision by a different person. An option chosen by the 1st
                # approver and dropped by the 2nd read "Set aside by <the 1st approver>",
                # naming them for doing the reverse of what they did.
                dropped = conn.execute(
                    "SELECT id, approval_status FROM vendors WHERE option_id=?",
                    (o["id"],)).fetchall()
                conn.execute("UPDATE vendors SET approval_status=?, rejection_reason=NULL, updated_at=? "
                             "WHERE option_id=?", (db.VS_NOT_SELECTED, ts, o["id"]))
                for gone in dropped:
                    if gone["approval_status"] == db.VS_NOT_SELECTED:
                        continue
                    conn.execute(
                        "INSERT INTO vendor_approvals(vendor_id,event_id,approver_id,decision,"
                        "rejection_reason,decision_date,created_at) VALUES(?,?,?,?,?,?,?)",
                        (gone["id"], ev["id"], user["id"], db.VS_NOT_SELECTED, None, ts, ts))
        # Against this approver, not only against the event. The event holds the standing
        # choice -- the last word wins -- but each approver's own pick is what lets the
        # next one see what was chosen before them, and lets the history show a change of
        # mind rather than silently overwriting it.
        if decision == "approved":
            conn.execute("UPDATE event_approvers SET selected_option_id=?, selected_option_ids=? "
                         "WHERE event_id=? AND user_id=?",
                         (selected, ",".join(str(x) for x in chosen), ev["id"], user["id"]))
        if len(options) > 1 and decision == "approved":
            mine_ids = ",".join(str(x) for x in chosen)
            earlier = conn.execute(
                "SELECT u.name, a.selected_option_ids ids FROM event_approvers a "
                "JOIN users u ON u.id=a.user_id "
                "WHERE a.event_id=? AND a.user_id<>? AND a.selected_option_ids IS NOT NULL "
                "  AND a.selected_option_ids<>'' AND a.selected_option_ids<>? "
                "ORDER BY a.level, a.id", (ev["id"], user["id"], mine_ids)).fetchall()
            # Not `by_id`: that name already holds the vendors, and the loop below
            # reads it. Shadowing it here turned every decision into a 500.
            option_names = {o["id"]: o["name"] for o in options}
            note = "%d of %d options approved" % (len(chosen), len(options))
            if earlier:
                def named(ids):
                    return " + ".join(option_names.get(intv(x), "?")
                                      for x in (ids or "").split(",") if x)
                note += " — differs from %s" % ", ".join(
                    "%s (%s)" % (r["name"], named(r["ids"])) for r in earlier)
            log(conn, ev["id"], "Manager selected %s" % chosen_name, user, ev["status"], ev["status"],
                note)

    # ---- persist vendor decisions
    for vid, (d, reason) in incoming.items():
        prev = by_id[vid]["approval_status"]
        conn.execute("UPDATE vendors SET approval_status=?, rejection_reason=?, updated_at=? WHERE id=?",
                     (d, reason if d in ("rejected", "not_selected") else None, ts, vid))
        if d not in ("pending",) and d != prev:
            conn.execute(
                "INSERT INTO vendor_approvals(vendor_id,event_id,approver_id,decision,rejection_reason,"
                "decision_date,created_at) VALUES(?,?,?,?,?,?,?)",
                (vid, ev["id"], user["id"], d, reason if d == "rejected" else None, ts, ts))
            verb = {"approved": "included vendor", "rejected": "rejected vendor",
                    "not_selected": "did not select vendor"}.get(d, d + " vendor")
            log(conn, ev["id"], "Approver %s: %s" % (verb, by_id[vid]["vendor_name"]),
                user, prev, d, reason or None)

    # Across every approved option, not just the first: "fully approved" has to mean
    # every vendor of everything approved, or a second package could be half decided
    # and the event would still call itself finished.
    vmarks = ",".join(["?"] * len(chosen)) or "NULL"
    vendors = conn.execute(
        "SELECT * FROM vendors WHERE option_id IN (%s) ORDER BY option_id, id" % vmarks,
        tuple(chosen)).fetchall() if chosen else []
    total_v = len(vendors)
    approved_v = sum(1 for v in vendors if v["approval_status"] == db.VS_APPROVED)
    rejected_v = sum(1 for v in vendors if v["approval_status"] in (db.VS_REJECTED, db.VS_NOT_SELECTED))
    pending_v = sum(1 for v in vendors if v["approval_status"] == db.VS_PENDING)

    # ---- record this approver's own decision in the chain
    conn.execute("UPDATE event_approvers SET status=?, decided_at=? WHERE event_id=? AND user_id=?",
                 ("approved" if decision == "approved" else "rejected", ts, ev["id"], user["id"]))
    if decision == "approved":
        # anyone else sitting at the same level no longer needs to act
        conn.execute("UPDATE event_approvers SET status='skipped', decided_at=? "
                     "WHERE event_id=? AND level=? AND user_id<>? AND status='pending'",
                     (ts, ev["id"], my_level, user["id"]))

    # Their request draft has served its purpose. Leaving it queued puts a "please
    # approve" message for someone who already decided in the requester's outbox,
    # beside the one that still needs sending -- which is how the message that mattered
    # became hard to pick out.
    for row in conn.execute("SELECT email FROM users WHERE id IN ("
                            "SELECT user_id FROM event_approvers WHERE event_id=? AND level=? "
                            "AND status<>'pending')", (ev["id"], my_level)).fetchall():
        conn.execute("UPDATE emails SET status='superseded' WHERE event_id=? AND to_email=? "
                     "AND type='approval_request' AND status='queued'", (ev["id"], row["email"]))

    # ---- is this the end of the chain?
    upcoming = next_level(conn, ev["id"], my_level)
    closes_chain = bool(mine["is_final"]) or upcoming is None
    advance = decision == "approved" and not closes_chain

    if advance:
        # hand the request to the next level; the event stays pending
        conn.execute("UPDATE events SET current_level=?, updated_at=? WHERE id=?",
                     (upcoming, ts, ev["id"]))
        log(conn, ev["id"], "%s approver approved — sent to the %s approver"
            % (ordinal(my_level), ordinal(upcoming)), user, ev["status"], ev["status"],
            event_reason or "Approved at level %d" % my_level)
        recalc_budget(conn, ev["id"])
        row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
        sent = send_level_request(conn, row, upcoming)
        creator = conn.execute("SELECT * FROM users WHERE id=?", (row["created_by"],)).fetchone()
        notify(conn, creator["id"], row["id"], "partial", "Approved at level %d" % my_level,
               "%s — %s approved as the %s approver. Now with %s (%s approver)."
               % (row["event_number"], user["name"], ordinal(my_level),
                  ", ".join(p["name"] for p in sent), ordinal(upcoming)))
        return {"event": event_dict(conn, row, True), "final": False,
                "message": "Approved. The request has moved to the %s approver." % ordinal(upcoming)}

    # ---- event status resolution (spec §13)
    if decision == "rejected":
        new_status = db.ST_REJECTED
    elif pending_v > 0:
        new_status = db.ST_PENDING_VENDORS
    elif rejected_v == 0:
        new_status = db.ST_FULL
    else:
        new_status = db.ST_PARTIAL

    final = new_status != db.ST_PENDING_VENDORS
    if final:
        # the chain ends here — nobody further is asked, whether that is because the
        # request was rejected or because the final approver has signed it off
        conn.execute("UPDATE event_approvers SET status='skipped', decided_at=? "
                     "WHERE event_id=? AND user_id<>? AND status='pending'",
                     (ts, ev["id"], user["id"]))
    conn.execute("UPDATE events SET status=?, decided_at=?, current_level=?, updated_at=? WHERE id=?",
                 (new_status, ts if final else None, None if final else my_level, ts, ev["id"]))
    if not final:
        # vendors still undecided — the same approver comes back to finish
        conn.execute("UPDATE event_approvers SET status='pending', decided_at=NULL "
                     "WHERE event_id=? AND user_id=?", (ev["id"], user["id"]))
    recalc_budget(conn, ev["id"])

    approval = conn.execute(
        "SELECT * FROM approvals WHERE event_id=? AND event_decision IS NULL ORDER BY id DESC LIMIT 1",
        (ev["id"],)).fetchone()
    if approval and final:
        # record WHO actually decided — any of the assigned approvers may act
        conn.execute("UPDATE approvals SET approver_id=?, event_decision=?, event_rejection_reason=?, "
                     "decision_date=? WHERE id=?",
                     (user["id"], decision, event_reason, ts, approval["id"]))
    elif not approval and final:
        conn.execute("INSERT INTO approvals(event_id,approver_id,event_decision,event_rejection_reason,"
                     "decision_date,created_at) VALUES(?,?,?,?,?,?)",
                     (ev["id"], user["id"], decision, event_reason, ts, ts))

    log(conn, ev["id"], "Manager %s the event" % ("approved" if decision == "approved" else "rejected"),
        user, ev["status"], new_status, event_reason)
    if final:
        log(conn, ev["id"], "Approval decision submitted", user, ev["status"], new_status,
            "Event %s · %d approved / %d rejected vendors" % (decision, approved_v, rejected_v))

    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    creator = conn.execute("SELECT * FROM users WHERE id=?", (row["created_by"],)).fetchone()
    base = db.get_setting(conn, "app_base_url", "http://localhost:8080")

    opt_name = None
    if len(options) > 1 and selected:
        opt_name = next((o["name"] for o in options if o["id"] == selected), None)

    if final:
        # every other assigned approver is told the request is closed and their link retired
        conn.execute("UPDATE approval_links SET revoked=1 WHERE event_id=? AND revoked=0", (row["id"],))
        for uid in approver_ids(conn, row["id"]):
            if uid != user["id"]:
                notify(conn, uid, row["id"], "decided_by_other", "Decision already recorded",
                       "%s — %s submitted the decision for %s. No action is needed from you."
                       % (row["event_number"], user["name"], row["event_name"]))

    if final:
        # the outcome of the chain goes to IC, copying the creator and every approver
        ic_email, ic_name = ic_recipient(conn)
        to_email = ic_email or creator["email"]
        to_name = ic_name or creator["name"]
        audience = decision_audience(conn, row["id"], creator, to_email)
        signed = approval_trail(conn, row["id"])

        if new_status == db.ST_FULL:
            subject, body, body_text = mailer.build_fully_approved(row, vendors, user, base, currency, opt_name,
                                                                   creator, signed)
            mailer.queue(conn, to_email, to_name, subject, body, "fully_approved",
                         row["id"], body_text, audience)
            notify(conn, creator["id"], row["id"], "approved", "Event fully approved",
                   "%s — %s and all vendors were approved. Approved budget: %s."
                   % (row["event_number"], row["event_name"], mailer.money(row["approved_budget"], currency)))
        elif new_status == db.ST_PARTIAL:
            subject, body, body_text = mailer.build_partially_approved(row, vendors, user, base, currency,
                                                                       opt_name, creator, signed)
            mailer.queue(conn, to_email, to_name, subject, body, "partially_approved",
                         row["id"], body_text, audience)
            notify(conn, creator["id"], row["id"], "partial", "Event partially approved",
                   "%s — event approved, %d vendor(s) rejected. Approved budget: %s."
                   % (row["event_number"], rejected_v, mailer.money(row["approved_budget"], currency)))
        else:
            subject, body, body_text = mailer.build_rejected(row, user, event_reason, base, currency,
                                                             creator, signed)
            mailer.queue(conn, to_email, to_name, subject, body, "rejected",
                         row["id"], body_text, audience)
            notify(conn, creator["id"], row["id"], "rejected", "Event rejected",
                   "%s — %s was rejected. Reason: %s" % (row["event_number"], row["event_name"], event_reason))
    else:
        notify(conn, creator["id"], row["id"], "partial", "Event approved — vendor review in progress",
               "%s — the event was approved. %d vendor decision(s) are still pending."
               % (row["event_number"], pending_v))

    return {"event": event_dict(conn, row, True), "final": final,
            "message": "Your approval decision has been submitted successfully."}


# ------------------------------------------------------------ notifications
@route("GET", r"/api/notifications")
def api_notifications(conn, ctx):
    user = need_user(ctx)
    items = rows(conn.execute(
        "SELECT n.*, e.event_number, e.event_name FROM notifications n "
        "LEFT JOIN events e ON e.id=n.event_id WHERE n.user_id=? ORDER BY n.id DESC LIMIT 200",
        (user["id"],)))
    unread = sum(1 for i in items if not i["read"])
    return {"notifications": items, "unread": unread}


@route("POST", r"/api/notifications/(\d+)/read")
def api_notif_read(conn, ctx, nid):
    user = need_user(ctx)
    conn.execute("UPDATE notifications SET read=1 WHERE id=? AND user_id=?", (int(nid), user["id"]))
    return {"ok": True}


@route("POST", r"/api/notifications/read-all")
def api_notif_read_all(conn, ctx):
    user = need_user(ctx)
    conn.execute("UPDATE notifications SET read=1 WHERE user_id=?", (user["id"],))
    return {"ok": True}


# -------------------------------------------------------------------- files
@route("GET", r"/api/vendor-files/(\d+)/content")
def api_file_content(conn, ctx, fid):
    user = need_user(ctx)
    f = conn.execute("SELECT * FROM vendor_files WHERE id=?", (int(fid),)).fetchone()
    if not f:
        raise ApiError(404, "File not found.")
    ev = conn.execute("SELECT * FROM events WHERE id=?", (f["event_id"],)).fetchone()
    if not ev or not can_view_event(conn, user, ev):
        raise ApiError(403, "You do not have permission to open this file.")
    data = load_blob(conn, "vendor_files", f)
    download = ctx["query"].get("download", ["0"])[0] == "1"
    ctx["raw"] = (data, f["mime"] or "application/octet-stream", f["file_name"], download)
    return None


# -------------------------------------------------------------------- admin
@route("GET", r"/api/users")
def api_users(conn, ctx):
    need_role(ctx, db.ROLE_ADMIN)
    items = rows(conn.execute(
        "SELECT u.*, m.name manager_name FROM users u LEFT JOIN users m ON m.id=u.manager_id ORDER BY u.name"))
    for i in items:
        i.pop("password_hash", None)
        i["events_created"] = conn.execute("SELECT COUNT(*) c FROM events WHERE created_by=?",
                                           (i["id"],)).fetchone()["c"]
        i["events_to_approve"] = conn.execute("SELECT COUNT(*) c FROM events WHERE approver_id=?",
                                              (i["id"],)).fetchone()["c"]
    return {"users": items}


@route("POST", r"/api/users")
def api_create_user(conn, ctx):
    admin = need_role(ctx, db.ROLE_ADMIN)
    b = ctx["body"]
    name = req(b.get("name"), "Name", 120)
    email = (req(b.get("email"), "E-mail", 160)).lower()
    if not valid_email(email):
        raise ApiError(400, "Please enter a valid e-mail address.")
    if conn.execute("SELECT 1 FROM users WHERE lower(email)=?", (email,)).fetchone():
        raise ApiError(409, "A user with that e-mail already exists.")
    role = s(b.get("role"), 20)
    if role not in db.ROLES:
        raise ApiError(400, "Invalid role.")
    # Nobody picks another person's password, so none is made here. The stored hash is
    # of a throwaway random string precisely so that no password opens this account
    # until its owner sets one -- they do that through the one-time link handed back
    # below. An administrator can let someone in; an administrator cannot be them.
    passwordless = 0 if uses_password(role) else 1
    level = intv(b.get("approver_level"))
    if role == db.ROLE_MANAGER:
        if not level or level < 1 or level > 6:
            raise ApiError(400, "Choose which approver this is (1st, 2nd, 3rd ...).")
    else:
        level = None
    cur = conn.execute(
        "INSERT INTO users(name,email,password_hash,role,department,job_title,phone,manager_id,"
        "can_view_all,can_view_archive,passwordless,approver_level,active,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, email, db.hash_password(secrets.token_urlsafe(32)), role,
         s(b.get("department"), 120), s(b.get("job_title"), 120), s(b.get("phone"), 40),
         intv(b.get("manager_id")), 1 if b.get("can_view_all") else 0,
         1 if b.get("can_view_archive") else 0, passwordless, level,
         1 if b.get("active", True) else 0, db.now_iso()))
    # the insert itself carries the new id on both stores; last_insert_rowid() is SQLite-only
    set_final_approver(conn, cur.lastrowid, b.get("is_final_approver"), role)
    fresh = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    url, _ = issue_setup_link(conn, fresh, admin["id"])
    return {"ok": True, "passwordless": bool(passwordless), "name": fresh["name"],
            "email": fresh["email"], "setup_url": url, "setup_days": SETUP_DAYS}


@route("PUT", r"/api/users/(\d+)")
def api_update_user(conn, ctx, uid):
    admin = need_role(ctx, db.ROLE_ADMIN)
    uid = int(uid)
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        raise ApiError(404, "User not found.")
    b = ctx["body"]
    role = s(b.get("role"), 20) or u["role"]
    if role not in db.ROLES:
        raise ApiError(400, "Invalid role.")
    active = 1 if b.get("active", True) else 0
    if uid == admin["id"] and (role != db.ROLE_ADMIN or not active):
        raise ApiError(400, "You cannot remove your own administrator access.")
    level = intv(b.get("approver_level"))
    if role == db.ROLE_MANAGER:
        if not level or level < 1 or level > 6:
            raise ApiError(400, "Choose which approver this is (1st, 2nd, 3rd ...).")
    else:
        level = None
    conn.execute(
        "UPDATE users SET name=?,email=?,role=?,department=?,job_title=?,phone=?,manager_id=?,"
        "can_view_all=?,can_view_archive=?,approver_level=?,active=? WHERE id=?",
        (req(b.get("name"), "Name", 120), (req(b.get("email"), "E-mail", 160)).lower(), role,
         s(b.get("department"), 120), s(b.get("job_title"), 120), s(b.get("phone"), 40),
         intv(b.get("manager_id")), 1 if b.get("can_view_all") else 0,
         1 if b.get("can_view_archive") else 0, level, active, uid))
    # An administrator cannot type a password in here: it is not theirs to choose. When a
    # role change means this person now needs one, they get a link and pick it themselves.
    setup_url = None
    if uses_password(role) and u["passwordless"]:
        conn.execute("UPDATE users SET passwordless=0 WHERE id=?", (uid,))
        moved = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        setup_url, _ = issue_setup_link(conn, moved, admin["id"])
    set_final_approver(conn, uid, b.get("is_final_approver"), role)
    if not may_hold_password(role) and not u["passwordless"]:
        # moved to a role that cannot hold one at all: retire the password. Merely
        # editing an approver must not do this -- a password they were given on purpose
        # would disappear the next time anyone corrected their job title.
        conn.execute("UPDATE users SET password_hash=?, passwordless=1 WHERE id=?",
                     (db.hash_password(secrets.token_urlsafe(32)), uid))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    if not active:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    # A set-up link is shown once, so it has to come back with the response.
    return {"ok": True, "name": u["name"], "email": u["email"],
            "setup_url": setup_url, "setup_days": SETUP_DAYS}


@route("POST", r"/api/users/(\d+)/password")
def api_reset_password(conn, ctx, uid):
    """Gone: an administrator no longer sets anybody's password.

    Kept as an explicit refusal rather than deleted, so an older page still open in
    somebody's browser gets an answer that says what to do instead of a bare 404.
    """
    need_role(ctx, db.ROLE_ADMIN)
    raise ApiError(410, "Passwords are chosen by the person who uses them. Send a set-up "
                        "link instead, and they will pick their own.")


@route("DELETE", r"/api/users/(\d+)")
def api_delete_user(conn, ctx, uid):
    """Remove an account outright.

    Refused when the person appears anywhere in the record — deleting them would
    tear holes in the approval history. Deactivate those instead: they can no
    longer sign in, but every past decision stays attributable.
    """
    admin = need_role(ctx, db.ROLE_ADMIN)
    uid = int(uid)
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        raise ApiError(404, "User not found.")
    if uid == admin["id"]:
        raise ApiError(400, "You cannot delete your own account.")

    footprint = {
        "created events": conn.execute("SELECT COUNT(*) c FROM events WHERE created_by=?", (uid,)).fetchone()["c"],
        "approval records": conn.execute("SELECT COUNT(*) c FROM approvals WHERE approver_id=?", (uid,)).fetchone()["c"],
        "vendor decisions": conn.execute("SELECT COUNT(*) c FROM vendor_approvals WHERE approver_id=?", (uid,)).fetchone()["c"],
        "history entries": conn.execute("SELECT COUNT(*) c FROM approval_history WHERE performed_by=?", (uid,)).fetchone()["c"],
        "pending approvals": conn.execute(
            "SELECT COUNT(*) c FROM event_approvers a JOIN events e ON e.id=a.event_id "
            "WHERE a.user_id=? AND e.status IN (?,?)", (uid, db.ST_PENDING, db.ST_PENDING_VENDORS)).fetchone()["c"],
        # being named on a request is itself part of the record, whatever came of it
        "approval assignments": conn.execute(
            "SELECT COUNT(*) c FROM event_approvers WHERE user_id=?", (uid,)).fetchone()["c"],
    }
    blocking = {k: v for k, v in footprint.items() if v}
    if blocking:
        raise ApiError(409, "%s appears in the record (%s) and cannot be deleted without losing that "
                            "history. Deactivate the account instead — they lose access but past "
                            "decisions stay attributed."
                       % (u["name"], ", ".join("%d %s" % (v, k) for k, v in blocking.items())),
                       {"footprint": footprint, "suggest": "deactivate"})

    # Nothing of record is left. What remains are references that carry no history of
    # their own -- who reported to them, their sign-ins, their unread notifications --
    # and those have to go first or the delete trips the foreign keys.
    conn.execute("UPDATE users SET manager_id=NULL WHERE manager_id=?", (uid,))
    for table in ("sessions", "known_devices", "notifications"):
        conn.execute("DELETE FROM %s WHERE user_id=?" % table, (uid,))
    conn.execute("DELETE FROM approval_links WHERE approver_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True, "message": "%s has been deleted." % u["name"]}


@route("GET", r"/api/admin/lookups")
def api_admin_lookups(conn, ctx):
    need_role(ctx, db.ROLE_ADMIN)
    return {"items": rows(conn.execute("SELECT * FROM lookups ORDER BY kind,sort,id"))}


@route("POST", r"/api/admin/lookups")
def api_admin_add_lookup(conn, ctx):
    need_role(ctx, db.ROLE_ADMIN)
    kind = s(ctx["body"].get("kind"), 30)
    if kind not in ("event_type", "vendor_category"):
        raise ApiError(400, "Invalid list.")
    value = req(ctx["body"].get("value"), "Value", 80)
    if conn.execute("SELECT 1 FROM lookups WHERE kind=? AND value=?", (kind, value)).fetchone():
        raise ApiError(409, "That entry already exists.")
    mx = conn.execute("SELECT COALESCE(MAX(sort),0) m FROM lookups WHERE kind=?", (kind,)).fetchone()["m"]
    conn.execute("INSERT INTO lookups(kind,value,sort,active) VALUES(?,?,?,1)", (kind, value, mx + 1))
    return {"ok": True}


@route("DELETE", r"/api/admin/lookups/(\d+)")
def api_admin_del_lookup(conn, ctx, lid):
    need_role(ctx, db.ROLE_ADMIN)
    conn.execute("UPDATE lookups SET active=0 WHERE id=?", (int(lid),))
    return {"ok": True}


@route("GET", r"/api/admin/approvals")
def api_admin_approvals(conn, ctx):
    need_role(ctx, db.ROLE_ADMIN)
    items = rows(conn.execute(
        "SELECT a.*, e.event_number, e.event_name, e.status event_status, e.total_budget, e.approved_budget, "
        "u.name approver_name FROM approvals a "
        "JOIN events e ON e.id=a.event_id LEFT JOIN users u ON u.id=a.approver_id ORDER BY a.id DESC"))
    vend = rows(conn.execute(
        "SELECT va.*, v.vendor_name, v.total_amount, e.event_number, u.name approver_name "
        "FROM vendor_approvals va JOIN vendors v ON v.id=va.vendor_id JOIN events e ON e.id=va.event_id "
        "LEFT JOIN users u ON u.id=va.approver_id ORDER BY va.id DESC"))
    hist = rows(conn.execute(
        "SELECT h.*, e.event_number, e.event_name, u.name performer_name FROM approval_history h "
        "JOIN events e ON e.id=h.event_id LEFT JOIN users u ON u.id=h.performed_by "
        "ORDER BY h.id DESC LIMIT 400"))
    return {"approvals": items, "vendor_approvals": vend, "history": hist}


def write_completed(conn, ctx, b, actor):
    """Create the finished record of an event that has already taken place.

    Anything that ran before the hub existed -- or outside it -- has no quotations to
    approve and no chain to walk, so it cannot travel the draft -> approval -> executed
    route. This writes the end state directly: completed, marked executed on the day it
    ran, owned by whoever ran it. Vendors and costs are optional, because for older
    activities there are often no cost records at all, and a zero is more honest than
    an invented figure.

    Idempotent on (name, date), so re-running an import tops up rather than duplicates.
    """
    kind = s(b.get("record_kind"), 20) or KIND_ACTIVITY
    if kind not in RECORD_KINDS:
        raise ApiError(400, "Unknown kind of record.")

    name = req(b.get("event_name"), "Event Name", 200)
    when = s(b.get("event_date"), 20)

    # A recap belongs to a period, not to a day, so the date is worked out: the month or
    # the quarter is the fact worth capturing, and it always publishes on the last
    # Thursday. A date sent alongside a period contradicts it, and the form has no way to
    # express a deliberate date for these kinds -- so the period wins rather than losing
    # quietly to a stale value. Without a period, an explicit date is still respected.
    if kind in (KIND_RECAP, KIND_QUARTER) and intv(b.get("period_year")):
        when = ""
    if not when and kind in (KIND_RECAP, KIND_QUARTER):
        year = intv(b.get("period_year"))
        if not year:
            raise ApiError(400, "Which year does this cover?")
        if kind == KIND_RECAP:
            month = intv(b.get("period_month"))
            if not month or month < 1 or month > 12:
                raise ApiError(400, "Which month does this recap cover?")
            when = last_thursday(year, month).isoformat()
        else:
            when = quarter_close(year, intv(b.get("period_quarter"))).isoformat()

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", when or ""):
        raise ApiError(400, "Provide the date as YYYY-MM-DD.")
    executed_on = s(b.get("execution_date"), 20) or when
    # A recap is shared on the day it is filed under -- the two cannot disagree, and the
    # UI shows this one as the sharing date. Letting a passed value through here put the
    # right Thursday on the calendar and the wrong date on the record.
    if kind in (KIND_RECAP, KIND_QUARTER) and intv(b.get("period_year")):
        executed_on = when
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", executed_on):
        raise ApiError(400, "Provide the execution date as YYYY-MM-DD.")

    owner_email = (s(b.get("creator_email"), 160) or "").lower()
    owner = conn.execute("SELECT id FROM users WHERE lower(email)=? AND active=1",
                         (owner_email,)).fetchone() if owner_email else None
    if owner_email and not owner:
        raise ApiError(400, "No active user with the e-mail %s -- add them first." % owner_email)
    owner_id = owner["id"] if owner else intv(b.get("created_by")) or actor["id"]

    existing = conn.execute("SELECT * FROM events WHERE event_name=? AND event_date=?",
                            (name, when)).fetchone()
    if existing:
        return {"event_id": existing["id"], "event_number": existing["event_number"],
                "created": False}

    ts = db.now_iso()
    misc = 0 if kind in NO_BUDGET_KINDS else num(b.get("miscellaneous_cost"))
    audience = s(b.get("audience"), 160) or (DEFAULT_AUDIENCE if kind in NO_BUDGET_KINDS else None)
    number = db.next_event_number(conn, int(when[:4]))
    cur = conn.execute(
        "INSERT INTO events(event_number,event_name,event_type,event_date,start_time,end_time,"
        "location,expected_attendees,description,miscellaneous_cost,total_budget,approved_budget,"
        "rejected_budget,status,created_by,executed,execution_date,execution_notes,"
        "record_kind,audience,submitted_at,decided_at,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,0,0,0,?,?,1,?,?,?,?,?,?,?,?)",
        (number, name, s(b.get("event_type"), 60) or "Internal Event", when,
         s(b.get("start_time"), 10), s(b.get("end_time"), 10), s(b.get("location"), 200),
         intv(b.get("expected_attendees")), s(b.get("description"), 6000), misc,
         db.ST_COMPLETED, owner_id, executed_on, s(b.get("execution_notes"), 2000),
         kind, audience, ts, ts, ts, ts))
    eid = cur.lastrowid

    # Vendors are optional. When they are given, they belong to a single package and count
    # as approved -- the spend already happened, so there is nothing left to decide.
    vendors = [] if kind in NO_BUDGET_KINDS else \
        [v for v in (b.get("vendors") or []) if s((v or {}).get("vendor_name"), 160)]
    if vendors:
        oid = conn.execute(
            "INSERT INTO event_options(event_id,name,description,sort,status,created_at,updated_at) "
            "VALUES(?,?,?,0,'approved',?,?)", (eid, "Option A", None, ts, ts)).lastrowid
        default_vat = num(db.get_setting(conn, "default_vat_rate", "15"), 15)
        for v in vendors[:20]:
            amount, vat_rate, vat, total, unit, qty = _vendor_totals(v, default_vat)
            conn.execute(
                "INSERT INTO vendors(event_id,option_id,vendor_name,category,contact_name,"
                "contact_email,contact_phone,description,unit_price,quantity,quotation_amount,"
                "vat_rate,vat,total_amount,approval_status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'approved',?,?)",
                (eid, oid, s(v.get("vendor_name"), 160), s(v.get("category"), 60),
                 s(v.get("contact_name"), 120), s(v.get("contact_email"), 160),
                 s(v.get("contact_phone"), 40), s(v.get("description"), 3000),
                 unit, qty, amount, vat_rate, vat, total, ts, ts))
        conn.execute("UPDATE events SET selected_option_id=? WHERE id=?", (oid, eid))
        recalc_budget(conn, eid)
        conn.execute("UPDATE events SET approved_budget=total_budget WHERE id=?", (eid,))

    # A recap goes out with someone's approval, and the record should say whose. When the
    # caller does not name anyone, the standing list is used -- so a record filed through
    # the API, or by an older page, still carries the sign-off it actually had.
    approvers = [x for x in (b.get("approver_ids") or []) if intv(x)]
    if not approvers and kind in NO_BUDGET_KINDS:
        approvers = record_approver_defaults(conn)
    # Nobody signs off their own work, and one of the standing names could be the owner
    # of this particular record -- dropping them here is kinder than refusing the save
    # over a default the person filing it never chose.
    approvers = [x for x in approvers if intv(x) != owner_id]
    approve_record(conn, eid, approvers, executed_on)

    log(conn, eid, "%s recorded" % KIND_LABELS[kind], actor, None, db.ST_COMPLETED,
        "Added straight to the history and the calendar, executed on %s.%s"
        % (executed_on, "" if vendors else " No cost recorded."))
    return {"event_id": eid, "event_number": number, "created": True}


@route("POST", r"/api/events/completed")
def api_add_completed(conn, ctx):
    """Add an event that already happened, so the history holds everything in one place."""
    user = need_role(ctx, db.ROLE_IC, db.ROLE_ADMIN)
    deny_scoped(user, "adding a completed event")
    return write_completed(conn, ctx, ctx["body"], user)


@route("PUT", r"/api/events/(\d+)/record")
def api_edit_completed(conn, ctx, eid):
    """Correct the record of an event that has already happened.

    A completed event is closed to the normal editor, which is right for anything that
    went through approval -- the figures must not drift away from what was approved. The
    descriptive record is a different matter: dates, places and notes for a past activity
    are often only settled afterwards, so the owner can put them right here. Budgets and
    approvals stay untouched.
    """
    user = need_user(ctx)
    ev = conn.execute("SELECT * FROM events WHERE id=?", (int(eid),)).fetchone()
    if not ev:
        raise ApiError(404, "Event not found.")
    deny_scoped(user, "editing an event record")
    if user["role"] != db.ROLE_ADMIN and ev["created_by"] != user["id"]:
        raise ApiError(403, "Only the event owner can edit this record.")
    if ev["status"] != db.ST_COMPLETED and not ev["executed"]:
        raise ApiError(409, "This event has not been completed yet -- edit it the usual way.")

    b = ctx["body"]
    when = s(b.get("event_date"), 20) or ev["event_date"]
    if when and not re.match(r"^\d{4}-\d{2}-\d{2}$", when):
        raise ApiError(400, "Provide the event date as YYYY-MM-DD.")
    executed_on = s(b.get("execution_date"), 20) or ev["execution_date"] or when
    if executed_on and not re.match(r"^\d{4}-\d{2}-\d{2}$", executed_on):
        raise ApiError(400, "Provide the execution date as YYYY-MM-DD.")

    ts = db.now_iso()
    conn.execute(
        "UPDATE events SET event_name=?,event_type=?,event_date=?,start_time=?,end_time=?,"
        "location=?,expected_attendees=?,description=?,execution_date=?,execution_notes=?,"
        "audience=?,updated_at=? WHERE id=?",
        (req(b.get("event_name"), "Event Name", 200) if b.get("event_name") else ev["event_name"],
         s(b.get("event_type"), 60) or ev["event_type"], when,
         s(b.get("start_time"), 10), s(b.get("end_time"), 10),
         s(b.get("location"), 200), intv(b.get("expected_attendees")),
         s(b.get("description"), 6000), executed_on,
         s(b.get("execution_notes"), 2000),
         s(b.get("audience"), 160) or ev["audience"], ts, ev["id"]))
    # Who signed it off is part of the record, and the answer has already changed once,
    # so it is editable here rather than fixed at the moment of filing. Absent from the
    # body means "leave it alone"; an empty list means "nobody", which is a real answer.
    if "approver_ids" in b:
        wanted = [x for x in (b.get("approver_ids") or []) if intv(x)]
        before = [r["name"] for r in conn.execute(
            "SELECT u.name FROM event_approvers ea JOIN users u ON u.id=ea.user_id "
            "WHERE ea.event_id=? ORDER BY ea.sort", (ev["id"],))]
        if wanted:
            approve_record(conn, ev["id"], wanted, executed_on)
        else:
            conn.execute("DELETE FROM event_approvers WHERE event_id=?", (ev["id"],))
            conn.execute("UPDATE events SET approver_id=NULL WHERE id=?", (ev["id"],))
        after = [r["name"] for r in conn.execute(
            "SELECT u.name FROM event_approvers ea JOIN users u ON u.id=ea.user_id "
            "WHERE ea.event_id=? ORDER BY ea.sort", (ev["id"],))]
        if before != after:
            log(conn, ev["id"], "Sign-off changed", user, ev["status"], ev["status"],
                "%s to %s" % (", ".join(before) or "nobody", ", ".join(after) or "nobody"))

    changed = []
    if when != ev["event_date"]:
        changed.append("event date %s to %s" % (ev["event_date"], when))
    if executed_on != ev["execution_date"]:
        changed.append("execution date %s to %s" % (ev["execution_date"], executed_on))
    log(conn, ev["id"], "Completed record edited", user, ev["status"], ev["status"],
        "; ".join(changed) or "Details updated")
    row = conn.execute("SELECT * FROM events WHERE id=?", (ev["id"],)).fetchone()
    return {"event": event_dict(conn, row, True)}


@route("POST", r"/api/admin/import-activity")
def api_import_activity(conn, ctx):
    """Bulk entry point for history_import.py. Same writer as the UI form."""
    user = need_role(ctx, db.ROLE_ADMIN)
    return write_completed(conn, ctx, ctx["body"], user)


@route("GET", r"/api/admin/emails")
def api_admin_emails(conn, ctx):
    need_role(ctx, db.ROLE_ADMIN)
    return {"emails": rows(conn.execute(
        "SELECT id,event_id,to_email,to_name,cc,subject,body_text,type,status,error,sent_at,created_at "
        "FROM emails ORDER BY id DESC LIMIT 200"))}


@route("GET", r"/api/admin/emails/(\d+)")
def api_admin_email(conn, ctx, mid):
    need_role(ctx, db.ROLE_ADMIN)
    row = conn.execute("SELECT * FROM emails WHERE id=?", (int(mid),)).fetchone()
    if not row:
        raise ApiError(404, "Message not found.")
    d = dict(row)
    d["body_html"] = mailer.inline_logo(d["body_html"])   # cid: → data URI for the preview
    return {"email": d}


@route("POST", r"/api/admin/test-email")
def api_test_email(conn, ctx):
    """Send a single test message so an admin can verify SMTP before going live."""
    user = need_role(ctx, db.ROLE_ADMIN)
    if db.get_setting(conn, "smtp_enabled", "0") != "1":
        raise ApiError(400, "Enable SMTP delivery in Settings first, then save, then send a test.")
    to = s(ctx["body"].get("to"), 160) or user["email"]
    if not valid_email(to):
        raise ApiError(400, "Please enter a valid recipient e-mail address.")
    base = db.get_setting(conn, "app_base_url", "http://localhost:8080")
    subject, body, body_text = mailer.build_test(user["name"], base)
    mail_id = mailer.queue(conn, to, user["name"], subject, body, "test", None, body_text)
    row = conn.execute("SELECT status,error FROM emails WHERE id=?", (mail_id,)).fetchone()
    if row["status"] != "sent":
        raise ApiError(400, "Delivery failed: %s" % (row["error"] or "unknown error"))
    return {"ok": True, "message": "Test message delivered to %s." % to}


@route("POST", r"/api/admin/emails/(\d+)/send")
def api_admin_send_email(conn, ctx, mid):
    need_role(ctx, db.ROLE_ADMIN)
    if db.get_setting(conn, "smtp_enabled", "0") != "1":
        raise ApiError(400, "SMTP delivery is disabled. Enable it in Settings before sending.")
    ok, msg = mailer.send_now(conn, int(mid))
    if not ok:
        raise ApiError(400, "Delivery failed: %s" % msg)
    return {"ok": True, "message": "Message delivered."}


@route("GET", r"/api/admin/settings")
def api_get_settings(conn, ctx):
    need_role(ctx, db.ROLE_ADMIN)
    out = {}
    for k in db.DEFAULT_SETTINGS:
        out[k] = db.get_setting(conn, k, db.DEFAULT_SETTINGS[k])
    out["smtp_password"] = "********" if out.get("smtp_password") else ""
    return {"settings": out}


@route("PUT", r"/api/admin/settings")
def api_put_settings(conn, ctx):
    need_role(ctx, db.ROLE_ADMIN)
    b = ctx["body"]
    for k in db.DEFAULT_SETTINGS:
        if k not in b:
            continue
        v = b[k]
        if k == "smtp_password" and v == "********":
            continue
        if k in ("smtp_enabled", "smtp_tls"):
            v = "1" if v in (True, "1", 1, "true") else "0"
        db.set_setting(conn, k, "" if v is None else str(v)[:500])
    return {"ok": True}


@route("GET", r"/api/export/events.xlsx")
def api_export_xlsx(conn, ctx):
    """Everything about events and activities, as an Excel workbook.

    Open to the IC team and to administrators, and scoped the same way the events list
    is: an IC member without wider access exports the activities they own, IC leads and
    administrators export everything. Approvers are deliberately not given a bulk export
    of the whole programme -- they see the requests that come to them.

    One flat sheet cannot hold this without repeating an event once per vendor per
    picture, so it is a workbook of linked sheets: every row carries the Event ID, and
    the reader joins on it.
    """
    user = need_user(ctx)
    deny_scoped(user, "exporting data")
    if user["role"] not in (db.ROLE_IC, db.ROLE_ADMIN):
        raise ApiError(403, "Only the Internal Communication team can export the programme.")

    where, params = scope_sql(user)
    evs = conn.execute("SELECT e.* FROM events e WHERE (%s) ORDER BY e.event_date, e.id"
                       % where, params).fetchall()
    currency = db.get_setting(conn, "currency", "SAR")

    events, options, vendors, chain, timeline, files = [], [], [], [], [], []
    for ev in evs:
        d = event_dict(conn, ev, True)
        num = d["event_number"]
        events.append([
            num, d["event_name"], d["event_type"], d["event_date"],
            d.get("start_time"), d.get("end_time"), d["location"],
            d.get("expected_attendees"), status_label(d["status"]),
            "Yes" if d.get("executed") else "No", d.get("execution_date"),
            d["creator_name"], d.get("creator_email"), d.get("approver_names"),
            len(d.get("options") or []),
            (d.get("vendor_summary") or {}).get("total", 0),
            (d.get("vendor_summary") or {}).get("approved", 0),
            num_or_zero(d.get("vendor_cost")), num_or_zero(d.get("miscellaneous_cost")),
            num_or_zero(d.get("total_budget")), num_or_zero(d.get("approved_budget")),
            num_or_zero(d.get("rejected_budget")), currency,
            d.get("submitted_at"), d.get("decided_at"), d.get("created_at"),
            len(d.get("photos") or []), len(d.get("announcements") or []),
            d.get("description"), d.get("execution_notes"),
        ])

        chosen = d.get("chosen_option_id")
        for o in d.get("options") or []:
            options.append([num, d["event_name"], o["name"],
                            "Yes" if o["id"] == chosen else "No", o.get("status"),
                            len(o.get("vendors") or []),
                            num_or_zero(o.get("vendor_cost")), num_or_zero(o.get("total")),
                            currency, o.get("description")])
            for v in o.get("vendors") or []:
                vendors.append([
                    num, d["event_name"], o["name"], v["vendor_name"], v.get("category"),
                    v.get("contact_name"), v.get("contact_email"), v.get("contact_phone"),
                    num_or_zero(v.get("quotation_amount")), num_or_zero(v.get("vat_rate")),
                    num_or_zero(v.get("vat")), num_or_zero(v.get("total_amount")), currency,
                    vendor_status_text(v.get("approval_status")), v.get("decided_by"),
                    v.get("decided_at"), v.get("rejection_reason"),
                    len(v.get("files") or []), v.get("description"),
                ])
                for f in v.get("files") or []:
                    files.append([num, d["event_name"], "Vendor file",
                                  v["vendor_name"], f.get("file_name"), f.get("kind"),
                                  round((f.get("size") or 0) / 1024.0, 1), f.get("created_at")])

        for a in d.get("approvers") or []:
            chain.append([num, d["event_name"], ordinal(a.get("level")), a["name"],
                          a.get("email"), a.get("job_title"),
                          "Yes" if a.get("is_final") else "No",
                          (a.get("status") or "pending").replace("_", " ").title(),
                          a.get("decided_at")])

        for h in d.get("history") or []:
            timeline.append([num, d["event_name"], h.get("created_at"), h.get("action"),
                             h.get("performer_name"), h.get("status_from"), h.get("status_to"),
                             h.get("note")])

        for p in (d.get("photos") or []):
            files.append([num, d["event_name"], "Event picture", "", p.get("file_name"),
                          p.get("kind"), round((p.get("size") or 0) / 1024.0, 1),
                          p.get("created_at")])
        for p in (d.get("announcements") or []):
            files.append([num, d["event_name"], "Announcement", "", p.get("file_name"),
                          p.get("kind"), round((p.get("size") or 0) / 1024.0, 1),
                          p.get("created_at")])

    book = xlsx.Workbook()
    book.sheet("Events", [
        "Event ID", "Event Name", "Type", "Event Date", "Start", "End", "Location",
        "Attendees", "Status", "Executed", "Execution Date", "Created By",
        "Creator E-mail", "Approvers", "Options", "Vendors", "Vendors Approved",
        "Vendor Cost", "Miscellaneous", "Total Budget", "Approved Budget",
        "Rejected Budget", "Currency", "Submitted", "Decided", "Created",
        "Pictures", "Announcements", "Description", "Execution Notes",
    ], events, money_columns=(18, 19, 20, 21, 22))

    book.sheet("Options", [
        "Event ID", "Event Name", "Option", "Chosen", "Status", "Vendors",
        "Vendor Cost", "Option Total", "Currency", "Description",
    ], options, money_columns=(7, 8))

    book.sheet("Vendors", [
        "Event ID", "Event Name", "Option", "Vendor", "Category", "Contact",
        "Contact E-mail", "Phone", "Quotation", "VAT %", "VAT", "Total", "Currency",
        "Decision", "Decided By", "Decision Date", "Rejection Reason", "Files",
        "Details",
    ], vendors, money_columns=(9, 11, 12))

    book.sheet("Approval chain", [
        "Event ID", "Event Name", "Level", "Approver", "E-mail", "Job Title",
        "Final Approval", "Status", "Decided",
    ], chain)

    book.sheet("Timeline", [
        "Event ID", "Event Name", "When", "Action", "By", "From", "To", "Note",
    ], timeline)

    book.sheet("Files", [
        "Event ID", "Event Name", "Belongs To", "Vendor", "File Name", "Kind",
        "Size (KB)", "Added",
    ], files)

    stamp = datetime.date.today().isoformat()
    ctx["raw"] = (book.save(),
                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  "KABi IC events %s.xlsx" % stamp, True)
    return None


@route("GET", r"/api/export/events.csv")
def api_export(conn, ctx):
    user = need_user(ctx)
    deny_scoped(user, "exporting data")
    if user["role"] not in (db.ROLE_IC, db.ROLE_ADMIN):
        raise ApiError(403, "Only the Internal Communication team can export data.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Event ID", "Event Name", "Type", "Date", "Start", "End", "Location", "Attendees",
                "Vendor Cost", "Miscellaneous", "Total Budget", "Approved Budget", "Rejected Budget",
                "Status", "Created By", "Approver", "Vendors", "Approved Vendors", "Rejected Vendors",
                "Submitted At", "Decided At", "Executed", "Execution Date", "Execution Notes", "Created At"])
    all_events = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    pre = Prefetch(conn, all_events)
    for ev in all_events:
        d = event_dict(conn, ev, pre=pre)
        vs = d["vendor_summary"]
        w.writerow([d["event_number"], d["event_name"], d["event_type"], d["event_date"], d["start_time"],
                    d["end_time"], d["location"], d["expected_attendees"], d["vendor_cost"],
                    d["miscellaneous_cost"], d["total_budget"], d["approved_budget"], d["rejected_budget"],
                    d["status"], d["creator_name"], d["approver_name"], vs["total"], vs["approved"],
                    vs["rejected"], d["submitted_at"], d["decided_at"],
                    "Yes" if d.get("executed") else "No", d.get("execution_date") or "",
                    d.get("execution_notes") or "", d["created_at"]])
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    ctx["raw"] = (data, "text/csv; charset=utf-8",
                  "ic-events-%s.csv" % datetime.date.today().isoformat(), True)
    return None


# =================================================================== handler
def _blame(exc):
    """The last frame inside our own code, as file:line in function.

    A traceback goes to the platform log, but the log is not always to hand — this
    puts the one line that matters in the response itself.
    """
    import os as _os
    import traceback as _tb
    here = _os.path.dirname(_os.path.abspath(__file__))
    best = None
    for frame in _tb.extract_tb(exc.__traceback__):
        try:
            inside = _os.path.commonpath([here, _os.path.abspath(frame.filename)]) == here
        except ValueError:
            inside = False
        if inside:
            best = frame
    if not best:
        return None
    return "%s:%d in %s" % (_os.path.basename(best.filename), best.lineno, best.name)


class Handler(BaseHTTPRequestHandler):
    server_version = "ICEventsHub/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------- plumbing
    @property
    def cookies(self):
        raw = self.headers.get("Cookie")
        jar = SimpleCookie()
        if raw:
            try:
                jar.load(raw)
            except Exception:
                pass
        return {k: v.value for k, v in jar.items()}

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        # Everything the page needs is served from here, so nothing else is allowed to
        # load or run. blob: is for pictures, which are fetched and handed to <img>.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                         "img-src 'self' blob: data:; "
                         "font-src 'self' data: https://fonts.gstatic.com; "
                         "connect-src 'self'; object-src 'none'; "
                         "base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Permissions-Policy",
                         "geolocation=(), camera=(), microphone=(), payment=()")
        if self.is_https():
            self.send_header("Strict-Transport-Security",
                             "max-age=31536000; includeSubDomains")
        for k, v in (headers or {}).items():
            if isinstance(v, (list, tuple)):     # several Set-Cookie headers
                for one in v:
                    self.send_header(k, one)
                continue
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, status, payload, headers=None):
        self._send(status, json.dumps(payload, default=str).encode("utf-8"),
                   "application/json; charset=utf-8", headers)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_HEAD(self):
        self._handle("GET")

    # --------------------------------------------------------------- router
    def is_https(self):
        """Whether the visitor's connection is encrypted.

        Behind a platform proxy the socket here is plain http, so the only honest
        signal is the forwarded header the proxy sets.
        """
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        return proto == "https"

    @staticmethod
    def _drop_script(url):
        """Everything after the running script, which is not part of the route.

        A hosting platform rewrites the request to the file that serves it, so the path
        can arrive as /api/index.py/api/events. The script is any segment ending in .py
        -- the application has no such route, so nothing real is lost by dropping it and
        everything before it.
        """
        cut = url.find(".py")
        if cut == -1:
            return url or "/"
        rest = url[cut + 3:]
        if rest.startswith("?"):
            return "/" + rest
        return rest if rest.startswith("/") else "/"

    def request_path(self):
        """The URL the visitor actually asked for.

        Behind a platform that rewrites requests, the original path can reach the
        function through any of three channels: the header the front end sets, a
        __path parameter put there by the rewrite, or the rewritten path itself. Each
        one has arrived mangled at some point in this project's life, so all three are
        normalised the same way rather than trusted individually -- the bug that cost
        the most here was stripping only the first of them.
        """
        parsed = urllib.parse.urlparse(self._drop_script(self.path or "/"))
        query = urllib.parse.parse_qs(parsed.query)
        path = urllib.parse.unquote(parsed.path) or "/"

        def adopt(candidate):
            """Take a stated path, and its query, applying the same rule."""
            inner = urllib.parse.urlparse(self._drop_script(candidate))
            for key, value in urllib.parse.parse_qs(inner.query).items():
                query.setdefault(key, value)
            return urllib.parse.unquote(inner.path) or "/"

        # The front end states its intended route outright, which no amount of
        # platform rewriting can disturb.
        stated = self.headers.get("X-IC-Path")
        if stated and stated.startswith("/"):
            return adopt(stated), query

        explicit = (query.pop("__path", [None]) or [None])[0]
        if explicit:
            path = adopt(explicit)
        elif path in ("/", "") or path.endswith(".py"):
            # Nothing but the entrypoint survived. Some platforms keep the original
            # elsewhere; failing that, serve the app shell and let the SPA route itself.
            for header in ("x-vercel-original-path", "x-forwarded-uri",
                           "x-original-uri", "x-matched-path"):
                candidate = self.headers.get(header)
                if candidate and candidate.startswith("/"):
                    taken = adopt(candidate)
                    if taken != "/":
                        path = taken
                        break
        return path or "/", query

    def _handle(self, method):
        path, prequery = self.request_path()

        if not path.startswith("/api/"):
            return self._static(path)

        conn = None
        ctx = None          # the error handler reads this, and we may fail before it exists
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                # Answering and closing while the client is still sending gives it a
                # connection reset rather than the reason, so read a little of the body
                # away first: enough for the client to get its answer, not enough to be
                # worth using as a way to make us read gigabytes.
                # Enough for the client to finish sending and read the answer, with a
                # ceiling so a declared length of a gigabyte cannot make us read one.
                to_drain = min(length, MAX_BODY + 8 * 1024 * 1024)
                while to_drain > 0:
                    chunk = self.rfile.read(min(65536, to_drain))
                    if not chunk:
                        break
                    to_drain -= len(chunk)
                raise ApiError(413, "That file is too large. The limit is %.1f MB."
                               % (upload_limit() / (1024.0 * 1024.0)))
            raw = self.rfile.read(length) if length else b""

            # Simple CSRF guard: state-changing calls must come from our own fetch()
            if method in ("POST", "PUT", "DELETE") and self.headers.get("X-Requested-With") != "ic-hub":
                raise ApiError(403, "Invalid request origin.")

            body = {}
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise ApiError(400, "Malformed request body.")
                if not isinstance(body, dict):
                    raise ApiError(400, "Malformed request body.")

            try:
                conn = db.connect()
            except Exception as exc:                          # noqa: BLE001
                # The health check exists to say what is wrong, and being unable to
                # reach the store is the most important thing it can report. Answering
                # "something went wrong" here would hide the one fact worth knowing.
                if path == "/api/health":
                    return self._json(200, {
                        "ok": False,
                        "store": "postgres" if db.using_postgres() else "sqlite",
                        "database_configured": bool(os.environ.get("IC_DATABASE_URL")
                                                    or os.environ.get("DATABASE_URL")),
                        "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                        "hint": ("Set IC_DATABASE_URL to the Supabase pooler connection "
                                 "string (port 6543) and redeploy."),
                    })
                raise
            user = current_user(conn, self)
            ctx = {"body": body, "query": prequery, "user": user, "_path": path,
                   "handler": self, "raw": None, "set_cookie": None, "clear_cookie": False,
                   "cookies": []}

            for m, rx, fn in ROUTES:
                if m != method:
                    continue
                match = rx.match(path)
                if not match:
                    continue
                result = fn(conn, ctx, *match.groups())
                conn.commit()

                if ctx["raw"]:
                    data, ctype, filename, download = ctx["raw"]
                    disp = "attachment" if download else "inline"
                    hdrs = {"Content-Disposition": content_disposition(disp, filename)}
                    return self._send(200, data, ctype, hdrs)

                headers = {}
                jar = list(ctx["cookies"])
                if ctx["set_cookie"]:
                    jar.append((COOKIE_NAME, ctx["set_cookie"],
                                ctx.get("cookie_hours", SESSION_DAYS * 24)))
                if ctx["clear_cookie"]:
                    jar = [c for c in jar if c[0] != COOKIE_NAME] + [(COOKIE_NAME, "", 0)]
                if jar:
                    secure = "; Secure" if self.is_https() else ""
                    headers["Set-Cookie"] = [
                        "%s=%s; Path=/; HttpOnly; SameSite=Lax%s; Max-Age=%d"
                        % (name, value, secure, int(hours * 3600))
                        for name, value, hours in jar]
                return self._json(200, result if result is not None else {"ok": True}, headers)

            raise ApiError(404, "Unknown endpoint: %s %s" % (method, path))

        except ApiError as exc:
            if conn:
                conn.rollback()
            payload = {"error": exc.message}
            payload.update(exc.extra)
            self._json(exc.status, payload)
        except Exception as exc:  # noqa: BLE001
            if conn:
                conn.rollback()
            import traceback
            traceback.print_exc()
            detail = {"error": "Something went wrong at our end. Please try again."}
            try:
                if ctx and (ctx.get("user") or {}).get("role") == db.ROLE_ADMIN:
                    detail = {"error": "Unexpected server error: %s: %s"
                                       % (type(exc).__name__, exc),
                              "where": _blame(exc), "path": path}
            except Exception:       # noqa: BLE001 - reporting must never be the failure
                pass
            self._json(500, detail)
        finally:
            if conn:
                conn.close()

    # --------------------------------------------------------------- static
    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        if rel.startswith("..") or os.path.isabs(rel):
            return self._send(403, b"Forbidden", "text/plain")
        full = os.path.join(WEB_DIR, rel)
        if not os.path.isfile(full):
            full = os.path.join(WEB_DIR, "index.html")   # SPA fallback
            if not os.path.isfile(full):
                return self._send(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"

        # Revalidate on every request: an edited file (same name, new content —
        # a replaced logo, a new build of app.js) must never be served stale.
        stat = os.stat(full)
        etag = '"%x-%x"' % (int(stat.st_mtime), stat.st_size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        with open(full, "rb") as fh:
            data = fh.read()
        self._send(200, data, ctype, {
            "Cache-Control": "no-cache, must-revalidate",
            "ETag": etag,
            "Last-Modified": self.date_time_string(int(stat.st_mtime)),
        })


class HubServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process silently bind the same port
    # and quietly serve stale code. Fail loudly instead.
    allow_reuse_address = False
    daemon_threads = True


def lan_ip():
    """Best-effort LAN address so e-mailed approval links work from other machines."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))          # no packets are sent
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    port = int(os.environ.get("PORT", "8080"))
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    db.init_db()

    ip = lan_ip()
    conn = db.connect()
    current = db.get_setting(conn, "app_base_url", "")
    # Keep an admin-configured public URL; otherwise track this machine's address
    # so the "Review Event" button in approval e-mails resolves for the manager.
    if not current or current.startswith(("http://localhost", "http://127.0.0.1")) or ":%d" % port not in current:
        db.set_setting(conn, "app_base_url", "http://%s:%d" % (ip, port))
    conn.commit()
    conn.close()

    try:
        srv = HubServer(("0.0.0.0", port), Handler)
    except OSError as exc:
        print("Could not start on port %d: %s" % (port, exc))
        print("Another copy is probably already running. Close it, or start on a "
              "different port:  python server.py 8090")
        sys.exit(1)
    print("=" * 64)
    print("  KABi · IC Events Approval Hub")
    print("  On this PC:      http://localhost:%d" % port)
    print("  On the network:  http://%s:%d   <- used in approval e-mails" % (ip, port))
    print("  Database:        %s" % db.DB_PATH)
    # Never the value. This banner prints on every start, into a window that stays open
    # all day and into hub.log on disk -- and DEMO_PASSWORD is the administrator's own
    # password, including a real one set through IC_ADMIN_PASSWORD. Saying whether it is
    # still the published default is the useful part; printing it was never necessary.
    try:
        conn = db.connect()
        row = conn.execute("SELECT password_hash FROM users WHERE role='admin' "
                           "ORDER BY id LIMIT 1").fetchone()
        conn.close()
        if row and db.using_weak_default(row["password_hash"]):
            print("  Administrator:   still using the password from the setup guide "
                  "- change it in My Profile")
    except Exception:                       # noqa: BLE001 - a banner never stops the hub
        pass
    print("  Press Ctrl+C to stop")
    print("=" * 64)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        srv.server_close()


if __name__ == "__main__":
    main()
