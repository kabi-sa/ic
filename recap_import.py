"""Put KABi's monthly recaps and quarterly updates into the history.

These are not activities. Nobody ran an event called "Oct 2025" -- IC published a recap,
and the calendar is more useful for showing when. Each one is filed under the last
Thursday of the period it covers, which is when it goes out, and the published sheet is
attached so the record is the thing itself rather than a note about it.

    python recap_import.py                        # against a local hub
    python recap_import.py https://your-hub

Safe to re-run: an entry already present is topped up with any missing file rather than
duplicated.
"""

import os
import sys

from history_import import Hub, upload, IC_ROOT, ADMIN_EMAIL, OWNER_EMAIL

RECAPS = os.path.join(IC_ROOT, "Monthly Recap")
QUARTERS = os.path.join(IC_ROOT, "Quarterly Updates")

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

# folder -> (year, month, the published sheet)
MONTHLY = [
    ("Jul 25", 2025, 7, "Jul 2025.png"),
    ("Aug 25", 2025, 8, "Aug.png"),
    ("Sep 25", 2025, 9, "Sep.png"),
    ("Oct 25", 2025, 10, "Oct Recap.png"),
    ("Nov 25", 2025, 11, "Nov Recap.png"),
    ("Dec 25", 2025, 12, "Dec Recap.png"),
    ("Jan 26", 2026, 1, "Jan 26 recap.png"),
    ("Feb 26", 2026, 2, "Feb26 recap.png"),
    ("Mar 26", 2026, 3, "Mar 26.png"),
    ("Apr 26", 2026, 4, "Apr 2026 recap.png"),
    ("May 26", 2026, 5, "May Recap.png"),
    ("Jun 26", 2026, 6, "Jun 2026 Recap.png"),
    ("Jul 26", 2026, 7, "Recap Jul 2026.png"),
]

# folder -> (year, quarter, the published sheet)
QUARTERLY = [
    ("3rd Quarter of 2025", 2025, 3, "3rd Quarter.png"),
    ("4th Q 2025", 2025, 4, "4th Quarter.png"),
    ("Q1 2026", 2026, 1, "Q1 2026.png"),
]

RECAP_BLURB = (
    "KABi Pulse, Stay in The Loop — the monthly recap for {month} {year}, shared with "
    "everyone. It carries the KABians activities of the month, a moment to celebrate, "
    "the picture of the month, and the recommendation and fact of the month.")

QUARTER_BLURB = (
    "The quarterly update for Q{quarter} {year}, shared with everyone: what the quarter "
    "delivered across the business, the clients it was delivered for, and where the next "
    "one is heading.")


def record(hub, body, files, label):
    """Create one entry and attach whatever belongs to it."""
    code, out = hub.call("/api/events/completed", "POST", body)
    if code != 200:
        print("FAILED  %-46s %s" % (label[:46], out.get("error")))
        return 0, 0
    eid = out["event_id"]

    # what is already attached, so a re-run tops up instead of duplicating
    code, detail = hub.call("/api/events/%d" % eid)
    held = detail.get("event") or {}
    have = {os.path.splitext(f.get("file_name") or "")[0].lower()
            for f in (held.get("photos") or []) + (held.get("announcements") or [])}

    added = 0
    for path, kind in files:
        if os.path.splitext(os.path.basename(path))[0].lower() in have:
            continue
        ok, why = upload(hub, eid, path, kind, label)
        if ok:
            added += 1
        else:
            print("   ! %-42s %s" % (os.path.basename(path)[:42], why))
    print("%-11s %-46s %s  (+%d file%s)"
          % (out["event_number"], label[:46],
             "new" if out.get("created") else "already recorded",
             added, "" if added == 1 else "s"))
    return (1 if out.get("created") else 0), added


def extras(folder, sheet):
    """Anything else in the folder: photographs that went with the update."""
    out = []
    for name in sorted(os.listdir(folder)):
        if name == sheet:
            continue
        if os.path.splitext(name)[1].lower() in (".jpg", ".jpeg", ".png", ".webp", ".jfif"):
            out.append((os.path.join(folder, name), "photo"))
    return out


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    hub = Hub(base)
    hub.sign_in()
    print("signed in to %s as %s\n" % (base, ADMIN_EMAIL))

    made = files = 0
    print("Monthly recaps")
    for folder, year, month, sheet in MONTHLY:
        path = os.path.join(RECAPS, folder, sheet)
        if not os.path.exists(path):
            print("   ! missing sheet for %s %d: %s" % (MONTHS[month - 1], year, sheet))
            continue
        label = "Monthly Recap — %s %d" % (MONTHS[month - 1][:3], year)
        m, f = record(hub, {
            "record_kind": "monthly_recap",
            "event_name": label,
            "event_type": "Campaign",
            "period_year": year, "period_month": month,
            "location": "Shared with all KABians",
            "description": RECAP_BLURB.format(month=MONTHS[month - 1], year=year),
            "execution_notes": "Published on the last Thursday of the month.",
            "creator_email": OWNER_EMAIL,
        }, [(path, "announcement")], label)
        made += m
        files += f

    print("\nQuarterly updates")
    for folder, year, quarter, sheet in QUARTERLY:
        full = os.path.join(QUARTERS, folder)
        path = os.path.join(full, sheet)
        if not os.path.exists(path):
            print("   ! missing sheet for Q%d %d: %s" % (quarter, year, sheet))
            continue
        label = "Quarterly Update — Q%d %d" % (quarter, year)
        m, f = record(hub, {
            "record_kind": "quarterly_update",
            "event_name": label,
            "event_type": "Campaign",
            "period_year": year, "period_quarter": quarter,
            "location": "Shared with all KABians",
            "description": QUARTER_BLURB.format(quarter=quarter, year=year),
            "execution_notes": "Published on the last Thursday of the quarter.",
            "creator_email": OWNER_EMAIL,
        }, [(path, "announcement")] + extras(full, sheet), label)
        made += m
        files += f

    print("\n%d new entries, %d files attached" % (made, files))


if __name__ == "__main__":
    main()
