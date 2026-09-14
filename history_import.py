"""Import KABi's past activities into the hub, from the monthly recap archive.

Everything here was read out of the KABians Activities panel (فعاليات الكابينز) of the
Monthly Recap sheets in ../Monthly Recap, month by month from Jul 2025 to Jul 2026, with
photographs taken from the same folders and the announcement designs from
../Activities & initiatives and ../IC Announcements.

These activities predate the hub, so they carry no quotations and no approval trail, and
no cost is recorded -- there are no cost records for them, and inventing figures would be
worse than leaving them at zero. Each one is written straight to its finished state:
completed, marked executed on the day it ran, owned by the person who ran it, so it shows
on the calendar and in the history exactly like an activity approved through the system.

Usage
-----
    python history_import.py                       # against a local hub
    python history_import.py https://your-app.vercel.app

The importer signs in as the IC administrator, so it needs the admin password. It is
idempotent: an activity that is already recorded is topped up with any missing files
rather than duplicated, so a re-run is safe.
"""

import base64
import http.cookiejar
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# Where the source material lives: the "IC" folder with Monthly Recap, Activities &
# initiatives and the rest. It used to be simply the parent of this one, which held
# while the hub sat inside that folder in OneDrive. The hub now runs from outside
# OneDrive -- a live SQLite file and a .git directory are not things a sync client
# should be touching -- while the source material rightly stays there, shared and
# backed up. So the two are no longer neighbours, and the path is stated rather than
# assumed. IC_SOURCE_ROOT overrides it for anyone whose copy is somewhere else again.
def _source_root():
    stated = os.environ.get("IC_SOURCE_ROOT")
    if stated:
        return stated
    for candidate in (os.path.dirname(HERE),
                      os.path.join(os.environ.get("OneDrive", ""), "Desktop", "IC"),
                      os.path.join(os.environ.get("OneDriveCommercial", ""), "Desktop", "IC")):
        if candidate and os.path.isdir(os.path.join(candidate, "Monthly Recap")):
            return candidate
    return os.path.dirname(HERE)                # nothing found; fail with a clear path


IC_ROOT = _source_root()
RECAPS = os.path.join(IC_ROOT, "Monthly Recap")
DESIGNS = os.path.join(IC_ROOT, "Activities & initiatives")
ANNOUNCE = os.path.join(IC_ROOT, "IC Announcements")

ADMIN_EMAIL = "ic@kabi.ai"
ADMIN_NAME = "Internal Communication"
ADMIN_PASSWORD = os.environ.get("IC_ADMIN_PASSWORD", "IC@kabi2026")
OWNER_EMAIL = "naloraij@kabi.ai"                # every past activity was run by Njood

# Files the hub will not accept as uploads, and the recap sheet itself, which is the
# newsletter rather than a picture of the activity.
SKIP_EXT = {".mp4", ".mov", ".avi", ".xlsx", ".xls", ".pptx", ".ppt"}
JPEG_ALIASES = {".jfif", ".jpe"}          # same bytes, non-standard suffix

# ---------------------------------------------------------------------------
# The activities, as recorded in the recaps. `photos` and `announcements` are file
# names relative to the folder named in `photo_dir` / to the design folders.
# ---------------------------------------------------------------------------
ACTIVITIES = [
    {
        "event_name": "International Friendship Day 2025",
        "event_type": "Awareness",
        "event_date": "2025-07-30",
        "location": "Riyadh, Jordan and Palestine offices",
        "description": (
            "International Friendship Day, run across all three offices. KABians wrote "
            "notes of appreciation to one another and pinned them to a shared wall, in "
            "Riyadh, Jordan and Palestine."),
        "photo_dir": "Jul 25",
        "photos": ["Jordan.jpg", "Palestine.jpg", "Palestine1.jpg", "R.jpg",
                   "Riyadh1.jpg", "Ryadh.jpg", "kh.jpg"],
        "announcements": ["يوم الصداقة العالمي.pdf"],
    },
    {
        "event_name": "Ice Cream Day 2025",
        "event_type": "Employee Engagement",
        "event_date": "2025-08-24",
        "location": "Riyadh office",
        "description": (
            "An ice cream stand set up in the Riyadh office for the team to enjoy "
            "through the working day."),
        "photo_dir": "Aug 25",
        "photos": "*",
        "announcements": [],
    },
    {
        "event_name": "Saudi National Day 2025 — Craft Activity",
        "event_type": "Celebration",
        "event_date": "2025-09-23",
        "location": "Riyadh office",
        "description": (
            "Saudi National Day celebrated in the Riyadh office with a hands-on craft "
            "activity, where KABians modelled and painted heritage landmarks and "
            "discovered a few hidden talents along the way."),
        "photo_dir": "Sep 25",
        "photos": "*",
        "announcements": ["SND Ar.png", "SND En.png"],
    },
    {
        "event_name": "International Coffee Day 2025",
        "event_type": "Employee Engagement",
        "event_date": "2025-10-01",
        "location": "Riyadh, Jordan and Palestine offices",
        "description": (
            "International Coffee Day marked in all three offices, with coffee served "
            "alongside printed cards carrying a small message to each KABian."),
        "photo_dir": "Oct 25",
        "photos": ["JO cofe.jpg", "PA cofe.jpg", "RHD cofe.jpg"],
        "announcements": ["Coffee Day.docx"],
    },
    {
        "event_name": "World Mental Health Day 2025",
        "event_type": "Awareness",
        "event_date": "2025-10-10",
        "location": "Riyadh, Jordan and Palestine offices",
        "description": (
            "World Mental Health Day, with awareness cards placed at every desk and a "
            "reminder to take a real break, across Riyadh, Jordan and Palestine."),
        "photo_dir": "Oct 25",
        "photos": ["JO.jpg", "PA.jpg"],
        "announcements": ["الصحة النفسية.pdf"],
    },
    {
        "event_name": "Pink October 2025 — Breast Cancer Awareness",
        "event_type": "Awareness",
        "event_date": "2025-10-20",
        "location": "Riyadh and Palestine offices",
        "description": (
            "Breast cancer awareness through October. Pink ribbons were handed out and "
            "an awareness announcement circulated under the line \"Awareness today, a "
            "healthier tomorrow\"."),
        "photo_dir": "Oct 25",
        "photos": ["Pink Oct PA.jpg", "Pink Oct RHD.jpg"],
        "announcements": ["Pink October.png", "Pink Oct.png"],
    },
    {
        "event_name": "World Kindness Day 2025",
        "event_type": "Awareness",
        "event_date": "2025-11-13",
        "location": "Riyadh, Jordan and Palestine offices",
        "description": (
            "World Kindness Day, celebrated in all three offices with balloons and "
            "hand-written notes of kindness left for colleagues to find."),
        "photo_dir": "Nov 25",
        "photos": ["kindness day JO.mp4", "kindness day KSA.jpeg",
                   "kindness day PA 1.jpeg", "kindness day PA.jpeg",
                   "JO Summer.jpeg", "JO. Summer.jpeg"],
        "announcements": ["يوم اللطف العالمي.pdf", "World Kindness Day.png"],
    },
    {
        "event_name": "International Men's Day 2025",
        "event_type": "Celebration",
        "event_date": "2025-11-19",
        "location": "Riyadh office",
        "description": (
            "International Men's Day marked with a small gesture of appreciation for "
            "the men of KABi."),
        "photo_dir": "Nov 25",
        "photos": ["يوم الرجل.jpeg"],
        "announcements": ["Men Day.png"],
    },
    {
        "event_name": "Arabic Language Day 2025",
        "event_type": "Celebration",
        "event_date": "2025-12-18",
        "location": "Riyadh office",
        "description": (
            "World Arabic Language Day on 18 December, celebrated with engraved wooden "
            "bookmarks carrying classical Arabic words and their meanings."),
        "photo_dir": "Dec 25",
        "photos": "*",
        "announcements": ["اللغة العربية.png"],
    },
    {
        "event_name": "KABians Annual Gathering 2026 — A Transformation That Shapes the Future",
        "event_type": "Internal Event",
        "event_date": "2026-01-15",
        "location": "Riyadh, Jordan and Palestine offices",
        "description": (
            "The annual KABians gathering, held under the theme \"A transformation that "
            "shapes the future\". The year's achievements were reviewed and the "
            "Employee of the Year, Above & Beyond and Difference Maker awards presented, "
            "with all three offices taking part."),
        "photo_dir": "Jan 26",
        "photos": "*",
        "announcements": ["الاجتماع السنوي.png", "Annual Gathering.png"],
    },
    {
        "event_name": "International Pizza Day 2026",
        "event_type": "Employee Engagement",
        "event_date": "2026-02-09",
        "location": "Riyadh and Palestine offices",
        "description": (
            "International Pizza Day, with pizza laid on for the team in the Riyadh and "
            "Palestine offices."),
        "photo_dir": "Feb 26",
        "photos": "*",
        "announcements": ["Pizza Day.png"],
    },
    {
        "event_name": "KABians Ramadan Iftar 2026",
        "event_type": "Internal Event",
        "event_date": "2026-03-12",
        "location": "Riyadh, Jordan and Palestine offices",
        "description": (
            "The Ramadan iftar for KABians, gathering the team around one table in each "
            "of the three offices."),
        "photo_dir": "Mar 26",
        "photos": "*",
        "announcements": ["الافطار.png", "Ramadan.docx"],
    },
    {
        "event_name": "Trees Day 2026",
        "event_type": "Awareness",
        "event_date": "2026-04-28",
        "location": "Riyadh office",
        "description": (
            "Trees Day on 28 April. Plants were placed around the office with cards "
            "carrying a line on planting today for the generations that follow."),
        "photo_dir": "Apr 26",
        "photos": "*",
        "announcements": ["Tree Day 29 Apr.pdf"],
    },
    {
        "event_name": "World Day for Cultural Diversity 2026",
        "event_type": "Celebration",
        "event_date": "2026-05-21",
        "location": "Riyadh office",
        "description": (
            "World Day for Cultural Diversity, where KABians brought in dishes from "
            "their own kitchens and cultures to share, and voted for a winning dish."),
        "photo_dir": "May 26",
        "photos": "*",
        "announcements": ["World Day for Cultural Diversity.png",
                          "التصويت للطبق الفائز.png", "الطبق الفائز.png"],
    },
    {
        "event_name": "International HR Day 2026",
        "event_type": "Celebration",
        "event_date": "2026-05-20",
        "location": "Riyadh office",
        "description": (
            "International Human Resources Day, marked with a note of thanks to the "
            "people whose work sits behind every hire and every career at KABi."),
        "photo_dir": "May 26",
        "photos": [],
        "announcements": ["International HR Day.png"],
    },
    {
        "event_name": "Guess the Score — National Team Match Predictions 2026",
        "event_type": "Team Activity",
        "event_date": "2026-06-18",
        "location": "Riyadh office",
        "description": (
            "A prediction board for the national team's matches. KABians wrote their "
            "score predictions on the office wall through the tournament, and the "
            "closest guesses were announced afterwards."),
        "photo_dir": "Jun 26",
        "photos": "*",
        "announcements": ["الفائزين (المنتخب).png", "KSAvsURG.docx"],
    },
    {
        "event_name": "International Friendship Day 2026",
        "event_type": "Awareness",
        "event_date": "2026-07-30",
        "location": "Riyadh office",
        "description": (
            "International Friendship Day on 30 July. A friendship wall was set up for "
            "KABians to leave notes for one another through the day."),
        "photo_dir": "Jul 26",
        "photos": "*",
        "announcements": ["يوم الصداقة العالمي.pdf"],
    },
]


class Hub:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-Requested-With", "ic-hub")
        req.add_header("X-IC-Path", path)          # survives platform path rewriting
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            resp = self.op.open(req, timeout=120)
            return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw or b"{}")
            except ValueError:
                return exc.code, {"error": raw[:200].decode("utf-8", "replace")}

    def sign_in(self):
        code, out = self.call("/api/auth/login", "POST",
                              {"name": ADMIN_NAME, "email": ADMIN_EMAIL,
                               "password": ADMIN_PASSWORD})
        if code != 200:
            raise SystemExit("Could not sign in as %s: %s"
                             % (ADMIN_EMAIL, out.get("error", code)))


def gather(activity):
    """Absolute paths for the pictures and announcement designs of one activity."""
    folder = os.path.join(RECAPS, activity["photo_dir"])
    wanted = activity["photos"]
    pics = []
    if wanted == "*":
        recap_sheet = None
        for name in sorted(os.listdir(folder)):
            low = name.lower()
            if "recap" in low or low in ("jul 2025.png", "aug.png", "sep.png", "mar 26.png"):
                recap_sheet = name                     # the newsletter, not the activity
                continue
            if os.path.splitext(low)[1] in SKIP_EXT:
                continue
            pics.append(os.path.join(folder, name))
        del recap_sheet
    else:
        for name in wanted:
            path = os.path.join(folder, name)
            if os.path.exists(path) and os.path.splitext(name)[1].lower() not in SKIP_EXT:
                pics.append(path)

    anns = []
    for name in activity["announcements"]:
        for root in (DESIGNS, ANNOUNCE):
            path = os.path.join(root, name)
            if os.path.exists(path):
                anns.append(path)
                break
    return pics, anns


# A hosted function caps the whole request body, and base64 inflates a file by a third,
# so anything much over 2.5 MB has to be brought down before it can be sent.
BODY_SAFE = 2_500_000


def shrink(raw, name):
    """Re-encode an oversized image to fit the upload, keeping it presentable.

    Re-encoding is tried at full size first, and the dimensions are only reduced when
    that is not enough. A recap sheet is a page of text saved as PNG: it is six megabytes
    because PNG is lossless, not because it is too big, and at full size it re-encodes to
    about 1.4 MB. Scaling it to fit a photograph's rule instead cost three quarters of the
    resolution for no reason -- and these sheets are the record, so they have to stay
    readable. Phone photographs still end up scaled, further down the list.
    """
    try:
        from PIL import Image
    except ImportError:
        return raw, name, "Pillow is not installed, cannot resize"
    import io as _io
    try:
        im = Image.open(_io.BytesIO(raw))
        im = im.convert("RGB")
    except Exception as exc:
        return raw, name, "not readable as an image (%s)" % type(exc).__name__
    for longest, quality in ((None, 92), (None, 88), (2600, 88),
                             (2000, 85), (1600, 80), (1200, 75)):
        copy = im.copy()
        if longest:
            copy.thumbnail((longest, longest), Image.LANCZOS)
        buf = _io.BytesIO()
        copy.save(buf, "JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        if len(out) <= BODY_SAFE:
            return out, os.path.splitext(name)[0] + ".jpg", None
    return out, os.path.splitext(name)[0] + ".jpg", None


def upload(hub, event_id, path, kind, caption):
    with open(path, "rb") as fh:
        raw = fh.read()
    name = os.path.basename(path)
    # .jfif is JPEG under another name; the hub takes the standard extensions only
    stem, ext = os.path.splitext(name)
    if ext.lower() in (".jfif", ".jpe"):
        name = stem + ".jpg"

    if len(raw) > BODY_SAFE:
        if os.path.splitext(name)[1].lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            raw, name, why = shrink(raw, name)
            if why:
                return False, "too large to send and %s" % why
        else:
            return False, ("%.1f MB exceeds what the hosted upload accepts -- attach it "
                           "by hand from the event page" % (len(raw) / 1e6))
    body = {"filename": name, "kind": kind,
            "caption": caption,
            "data": base64.b64encode(raw).decode("ascii")}
    code, out = hub.call("/api/events/%d/photos" % event_id, "POST", body)
    return code == 200, out.get("error")


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    hub = Hub(base)
    hub.sign_in()
    print("signed in to %s as %s\n" % (base, ADMIN_EMAIL))

    made = skipped = pics_done = anns_done = failed = 0
    for act in ACTIVITIES:
        code, out = hub.call("/api/admin/import-activity", "POST", {
            "event_name": act["event_name"],
            "event_type": act["event_type"],
            "event_date": act["event_date"],
            "location": act["location"],
            "description": act["description"],
            "execution_date": act["event_date"],
            "execution_notes": "Recorded from the %s monthly recap." % act["photo_dir"],
            "creator_email": OWNER_EMAIL,
        })
        if code != 200:
            print("FAILED  %-58s %s" % (act["event_name"][:58], out.get("error")))
            failed += 1
            continue
        eid = out["event_id"]
        fresh = out.get("created")
        made += 1 if fresh else 0
        skipped += 0 if fresh else 1

        pics, anns = gather(act)

        # What is already attached, so a re-run tops up instead of piling on copies --
        # and so any duplicates an earlier run left behind are cleared out.
        code, detail = hub.call("/api/events/%d" % eid)
        held = (detail.get("event") or {})
        attached, dupes = set(), []
        for item in (held.get("photos") or []) + (held.get("announcements") or []):
            # Compare stems: a resized copy of the same picture arrives as .jpg, so the
            # extension is not part of the file's identity here.
            key = (item.get("kind"),
                   os.path.splitext(item.get("file_name") or "")[0].lower())
            if key in attached:
                dupes.append(item["id"])
            else:
                attached.add(key)
        for pid in dupes:
            hub.call("/api/event-photos/%d" % pid, "DELETE")
        if dupes:
            print("   - removed %d duplicate file(s) from an earlier run" % len(dupes))

        def already(path, kind):
            stem = os.path.splitext(os.path.basename(path))[0].lower()
            return (kind, stem) in attached

        pics = [x for x in pics if not already(x, "photo")]
        anns = [x for x in anns if not already(x, "announcement")]
        p_ok = a_ok = 0
        for path in pics:
            ok, err = upload(hub, eid, path, "photo", act["event_name"])
            p_ok += 1 if ok else 0
            if not ok:
                print("   ! picture %-46s %s" % (os.path.basename(path)[:46], err))
        for path in anns:
            ok, err = upload(hub, eid, path, "announcement",
                             "Announcement shared with employees")
            a_ok += 1 if ok else 0
            if not ok:
                print("   ! announcement %-41s %s" % (os.path.basename(path)[:41], err))
        pics_done += p_ok
        anns_done += a_ok
        code, after = hub.call("/api/events/%d" % eid)
        held = (after.get("event") or {})
        print("%-9s %-56s %2d pics  %d ann  (+%d/+%d this run)"
              % (out["event_number"], act["event_name"][:56],
                 len(held.get("photos") or []), len(held.get("announcements") or []),
                 p_ok, a_ok))

    print("\n%d activities recorded (%d already present), %d pictures, %d announcements"
          % (made, skipped, pics_done, anns_done))
    if failed:
        print("%d failed -- see the lines above" % failed)


if __name__ == "__main__":
    main()
