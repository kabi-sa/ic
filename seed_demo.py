"""
Optional demo data for the IC Events Approval Hub.

Creates a realistic set of events across every status so the system can be
explored immediately. It drives the real HTTP API, so budgets, approval
history, notifications and e-mails are all generated exactly as in normal use.

    1. Start the server:   python server.py
    2. In another window:  python seed_demo.py

Add --reset to delete ALL existing events first (users and settings are kept).
"""

import json
import sys
import urllib.request
import urllib.error
import http.cookiejar

import db

BASE = "http://localhost:8080"
PW = db.DEMO_PASSWORD


def client(email):
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("X-Requested-With", "ic-hub")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            return json.loads(op.open(req, timeout=20).read().decode())
        except urllib.error.HTTPError as exc:
            raise SystemExit("API error on %s %s: %s" % (method, path, exc.read().decode()[:300]))

    call("/api/auth/login", "POST", {"email": email, "password": PW})
    return call


def main():
    try:
        urllib.request.urlopen(BASE + "/api/auth/me", timeout=5)
    except Exception:
        raise SystemExit("The server is not running. Start it first:  python server.py")

    if "--reset" in sys.argv:
        conn = db.connect()
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM emails")
        conn.commit()
        conn.close()
        print("Existing events cleared.")

    ic = client("naloraij@kabi.ai")
    ic2 = client("sara.ic@kabi.ai")
    mgr = client("m.manager@kabi.ai")

    approvers = ic("/api/lookups")["approvers"]
    m1 = [a for a in approvers if a["email"] == "m.manager@kabi.ai"][0]["id"]
    m2 = [a for a in approvers if a["email"] == "l.manager@kabi.ai"][0]["id"]

    def make(owner, ev, vendors, submit=False, alt=None):
        """alt = (option name, description, [vendors]) — a second, alternative package."""
        e = owner("/api/events", "POST", ev)["event"]
        for v in vendors:
            owner("/api/events/%d/vendors" % e["id"], "POST", v)
        if alt:
            name, desc, alt_vendors = alt
            oid = owner("/api/events/%d/options" % e["id"], "POST",
                        {"name": name, "description": desc})["option_id"]
            for v in alt_vendors:
                v = dict(v, option_id=oid)
                owner("/api/events/%d/vendors" % e["id"], "POST", v)
        if submit:
            owner("/api/events/%d/submit" % e["id"], "POST")
        return e

    # 1 — fully approved and executed -----------------------------------
    e1 = make(ic, {
        "event_name": "Annual Gathering 2026", "event_type": "Celebration", "event_date": "2026-08-27",
        "start_time": "18:00", "end_time": "22:00", "location": "KABi HQ — Main Hall, Riyadh",
        "expected_attendees": 180, "approver_id": m1, "miscellaneous_cost": 2500,
        "description": "Annual company gathering: dinner, long-service awards, entertainment and a closing "
                       "message from the CEO."},
        [{"vendor_name": "Riyadh Catering Co.", "category": "Catering", "contact_name": "Faisal Al-Dossari",
          "contact_email": "faisal@riyadhcatering.example", "contact_phone": "+966 50 111 2233",
          "description": "Dinner buffet for 180 guests including service staff.",
          "quotation_amount": 15000, "vat_rate": 15},
         {"vendor_name": "Bright Stage Events", "category": "Event Management",
          "contact_name": "Noura Al-Subaie", "contact_email": "noura@brightstage.example",
          "description": "Stage, AV, lighting and show-flow management.",
          "quotation_amount": 20000, "vat_rate": 15,
          "links": [{"label": "Portfolio", "url": "https://example.com/brightstage"}]}],
        submit=True)
    mgr("/api/events/%d/decision" % e1["id"], "POST", {
        "event_decision": "approved",
        "vendors": [{"id": v["id"], "decision": "approved"}
                    for v in ic("/api/events/%d" % e1["id"])["event"]["vendors"]]})
    ic("/api/events/%d/complete" % e1["id"], "POST",
       {"execution_date": "2026-08-27", "execution_notes": "184 attendees. Ran to schedule, excellent feedback."})

    # 2 — partially approved --------------------------------------------
    e2 = make(ic, {
        "event_name": "Employee of the Month — September", "event_type": "Employee Engagement",
        "event_date": "2026-09-15", "start_time": "11:00", "end_time": "12:00",
        "location": "KABi HQ — Lounge", "expected_attendees": 60, "approver_id": m1,
        "miscellaneous_cost": 500,
        "description": "Monthly recognition ceremony for the Employee of the Month, with a short "
                       "announcement, photos and a small reception."},
        [{"vendor_name": "Gift House", "category": "Gifts", "contact_name": "Reem Al-Harthy",
          "contact_email": "reem@gifthouse.example",
          "description": "Engraved trophy, certificate folder and branded gift box.",
          "quotation_amount": 8000, "vat_rate": 15},
         {"vendor_name": "Lens & Light Studio", "category": "Photography", "contact_name": "Omar Hassan",
          "contact_email": "omar@lenslight.example",
          "description": "Photographer for 2 hours plus edited photo set.",
          "quotation_amount": 6500, "vat_rate": 15}],
        submit=True)
    vs = ic("/api/events/%d" % e2["id"])["event"]["vendors"]
    mgr("/api/events/%d/decision" % e2["id"], "POST", {
        "event_decision": "approved",
        "vendors": [{"id": vs[0]["id"], "decision": "approved"},
                    {"id": vs[1]["id"], "decision": "rejected",
                     "rejection_reason": "Photography is covered by the in-house team this quarter — "
                                         "no external budget approved."}]})

    # 3 — pending, waiting for the manager, offering TWO alternative options
    make(ic, {
        "event_name": "Quarterly Townhall — Q4", "event_type": "Internal Event", "event_date": "2026-10-12",
        "start_time": "10:00", "end_time": "13:00", "location": "KABi HQ — Auditorium, Riyadh",
        "expected_attendees": 220, "approver_ids": [m1, m2], "miscellaneous_cost": 3200,
        "description": "Quarterly townhall with the CEO: business update, Q3 results, recognition of top "
                       "performers and an open Q&A session."},
        [{"vendor_name": "Riyadh Catering Co.", "category": "Catering", "contact_name": "Faisal Al-Dossari",
          "contact_email": "faisal@riyadhcatering.example", "contact_phone": "+966 50 111 2233",
          "description": "Coffee break and lunch buffet for 220 guests.",
          "quotation_amount": 26000, "vat_rate": 15},
         {"vendor_name": "Bright Stage Events", "category": "Event Management",
          "contact_name": "Noura Al-Subaie", "contact_email": "noura@brightstage.example",
          "contact_phone": "+966 55 987 6543",
          "description": "Stage design, LED screen, sound, lighting and full show-flow management.",
          "quotation_amount": 41000, "vat_rate": 15,
          "links": [{"label": "Portfolio", "url": "https://example.com/brightstage/portfolio"}]},
         {"vendor_name": "Lens & Light Studio", "category": "Photography", "contact_name": "Omar Hassan",
          "contact_email": "omar@lenslight.example",
          "description": "Photography and a 3-minute highlights video.",
          "quotation_amount": 12500, "vat_rate": 15}],
        submit=True,
        alt=("Option B — in-house production",
             "Same townhall delivered with a lighter external scope: catering plus AV rental only, "
             "photography and show-flow handled internally.",
             [{"vendor_name": "Riyadh Catering Co.", "category": "Catering", "contact_name": "Faisal Al-Dossari",
               "contact_email": "faisal@riyadhcatering.example",
               "description": "Coffee break only — no lunch buffet.", "quotation_amount": 14000, "vat_rate": 15},
              {"vendor_name": "SoundWorks Rental", "category": "Event Management", "contact_name": "Tariq M.",
               "contact_email": "tariq@soundworks.example",
               "description": "Sound and lighting rental, self-operated.", "quotation_amount": 17500, "vat_rate": 15},
              {"vendor_name": "Printing Plus", "category": "Printing", "contact_name": "Sami R.",
               "contact_email": "sami@printingplus.example",
               "description": "Stage backdrop and signage.", "quotation_amount": 6000, "vat_rate": 15}]))

    # 4 — rejected -------------------------------------------------------
    e4 = make(ic2, {
        "event_name": "Team Activity — Padel Tournament", "event_type": "Team Activity",
        "event_date": "2026-09-05", "start_time": "17:00", "end_time": "21:00",
        "location": "Padel Arena, Riyadh", "expected_attendees": 40, "approver_id": m2,
        "description": "Inter-department padel tournament to close the quarter."},
        [{"vendor_name": "Padel Arena", "category": "Venue", "contact_name": "Khalid N.",
          "contact_email": "bookings@padelarena.example",
          "description": "Four courts for four hours, referee and refreshments.",
          "quotation_amount": 12000, "vat_rate": 15}],
        submit=True)
    client("l.manager@kabi.ai")("/api/events/%d/decision" % e4["id"], "POST", {
        "event_decision": "rejected",
        "event_rejection_reason": "The activity budget for Q3 is already committed. Please resubmit for Q4 "
                                  "with a reduced scope."})

    # 5 — draft ----------------------------------------------------------
    make(ic, {
        "event_name": "Awareness Week — Cybersecurity", "event_type": "Awareness", "event_date": "2026-11-02",
        "start_time": "09:00", "end_time": "16:00", "location": "KABi HQ — Training Room B",
        "expected_attendees": 90, "approver_id": m1, "miscellaneous_cost": 1200,
        "description": "A week of awareness sessions on phishing, password hygiene and data classification, "
                       "run together with the IT team."},
        [{"vendor_name": "Printing Plus", "category": "Printing", "contact_name": "Sami R.",
          "contact_email": "sami@printingplus.example",
          "description": "Posters, roll-ups and desk cards.", "quotation_amount": 4500, "vat_rate": 15}])

    print("Demo data created. Sign in as any of:")
    for name, email, role, _dep, _t in db.SEED_USERS:
        print("   %-22s %-22s %s" % (name, email, role))
    print("   password: %s" % PW)


if __name__ == "__main__":
    main()
