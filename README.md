# KABi · IC Events Approval Hub

An internal web application for Internal Communication events: create an event, add vendors and
quotations, calculate the budget automatically, send one approval request to a manager, and get an
independent decision on the event **and on every vendor** — with a mandatory reason for each rejection,
full approval history, execution tracking and an event archive.

Built with the Python standard library and SQLite. **No packages to install.**

---

## 1. Run it

Double-click **`START.bat`**, or from a terminal:

```bash
python server.py
```

The console prints two addresses:

```
On this PC:      http://localhost:8080
On the network:  http://192.168.x.x:8080   <- used in approval e-mails
```

Sign in with any of the seeded accounts (password **`Welcome@2026`** — change it under *Profile*):

| Name                 | E-mail              | Role               |
|----------------------|---------------------|--------------------|
| Internal Communication | ic@kabi.ai        | Admin              |
| Njood Aloraij        | naloraij@kabi.ai    | IC User            |
| Sara Al-Harbi        | sara.ic@kabi.ai     | IC User            |
| Mohammed Al-Otaibi   | m.manager@kabi.ai   | Manager / Approver |
| Layla Al-Qahtani     | l.manager@kabi.ai   | Manager / Approver |

Optional — fill the system with realistic example events (start the server first):

```bash
python seed_demo.py --reset
```

> `--reset` deletes all existing events. Users and settings are kept.

---

## 2. Getting the request to the approver

There are two ways, and you don't need IT for the first one.

### Option A — send from your own mailbox (works today, no setup)

Every message is composed the moment it's needed and waits in the app. On the event page, the
requester sees a **Send from your mailbox** panel with one button per approver:

* **Open designed e-mail** *(recommended)* — downloads a `.eml` file carrying the full branded HTML
  with the KABi logo embedded. It has an `X-Unsent: 1` header, so Outlook opens it as a **composable
  draft with a Send button**. Press Send and it goes from your own address, fully designed.
* **Copy formatted** — puts the rich HTML on the clipboard; paste into a new Outlook message. Use this
  if the `.eml` opens read-only on your Outlook configuration.
* **Plain text** — a `mailto:` link. The `mailto:` standard carries text only and browsers cap the URL
  length, so this version is a formatted plain-text rendering of the same content. Handy on mobile.

After you send, the app asks whether it went out and records *"Approval request e-mailed manually"* in
the event history.

### Option B — automatic delivery (SMTP)

E-mail is **composed always, delivered only when you switch it on.** Every message the system
generates is stored in *Administration → E-mail outbox*, where you can preview exactly what the
recipient sees. Nothing leaves your network until an administrator does this:

1. Sign in as **ic@kabi.ai** → **Administration → Settings**.
2. Fill in the SMTP details (host, port, username, password, from-address).
   * Microsoft 365: `smtp.office365.com`, port `587`, STARTTLS on.
   * Port `465` automatically uses implicit SSL.
3. Tick **Enable SMTP delivery** → **Save settings**.
4. Use **Send a test message** to confirm before going live.

Check **Application base URL** on the same page. It must be an address your colleagues can reach
(the LAN address printed at startup, or a proper server name if the hub is hosted centrally) —
that is the address behind the **Review Event** button in the approval e-mail.

### The five messages

| # | Trigger | To | Subject |
|---|---------|----|---------|
| 1 | IC user submits an event | Approver | `Action Required: Event Approval – <event>` |
| 2 | Event + all vendors approved | Requester | `Event Approved – <event>` |
| 3 | Event approved, some vendors rejected | Requester | `Event Approval Update – <event>` |
| 4 | Event rejected | Requester | `Event Rejected – <event>` |
| 5 | Event marked as executed | Approver + requester | `Event Executed – <event>` |

Each one carries the event ID, date, location, description, budget, the vendor list with amounts and
decisions, every rejection reason, the approver's name and the decision date.

---

## 3. The workflow

```
IC user creates an event            -> status: Draft   (ID auto-generated: IC-2026-001)
  adds one or more vendors          -> vendor total = quotation + VAT, calculated automatically
  optionally adds alternative       -> "Option A" (vendors A+B+C) vs "Option B" (vendors C+D+E),
    options                            each option carries its own separate total
  uploads quotations, images, links -> option total = its vendors + miscellaneous
  submits for approval              -> status: Pending Approval, e-mail 1 to the manager
Manager clicks "Review Event" in the e-mail
  confirms name + e-mail            -> no password; the link opens that one event only
  picks one option                  -> the other options are recorded as "not selected"
  approves or rejects the event     -> a reason is mandatory to reject
  decides on each vendor separately -> a reason is mandatory for each rejected vendor
                                    -> the approved budget updates live as decisions change
  clicks "Submit Decision"          -> nothing is saved before this click
IC user is notified by e-mail + in-app
After the event happens
  "Mark as executed" + date + notes -> status: Completed, e-mail 5, moves to the Completed tab
```

### Alternative options

An event may carry up to six alternative packages. Each option is self-contained: its own vendors,
its own vendor cost, and its own total (its vendors + the shared miscellaneous cost). The approver
picks exactly one; every vendor in the options that were not picked is marked **Not selected** and
costs nothing. Before a choice is made, the event's headline figure follows the first option.

### More than one approver

The **Approver / Manager** field is a multi-select. Everyone ticked receives the approval e-mail with
their **own** personal review link, and the event appears in each of their queues. Whoever submits a
decision first records it for everyone — the others are notified that no action is needed and their
links stop working. The approval history stores who actually decided.

If you need *every* approver to sign off (a chain rather than a race), that is a different flow —
tell me and I'll add it.

### Passwordless review links

The **Review Event** button in the approval e-mail carries a one-time token. The manager confirms
their name and the e-mail address the request was sent to — no account password — and lands directly
on the approval page. **They are asked only once per device:** after the first confirmation the
browser is remembered for 180 days, so every later approval link opens straight into the approval
page. "Forget this device" is in their menu for shared computers.

Each review session:

* can open **only** the event the link was issued for (any other ID returns 403);
* cannot create or edit events, reach administration, or change the account password;
* lasts 30 days; the link itself expires after 45 days;
* is revoked and reissued whenever the event is resubmitted, and retired once a decision is recorded;
* records the name and e-mail entered into the approval history, so the decision is attributed.

Being remembered as one approver grants nothing on another approver's link — that still requires
confirmation. Managers who prefer a normal account can sign in with a password and see all requests.

**Resulting status** (vendor decisions are counted within the selected option only)

| Event decision | Vendor decisions | Status |
|---|---|---|
| Approved | all approved | Fully Approved |
| Approved | some approved, some rejected | Partially Approved |
| Approved | some still undecided | Pending Vendor Approval |
| Rejected | — | Rejected |

**Budget** — approved budget = approved vendors in the selected option + miscellaneous cost.
Rejecting a vendor removes its amount immediately; rejecting the event drops the approved budget to
zero. The manager sees all of this update live before committing anything.

**Editing rules** — a Draft is fully editable; a pending event is locked and must be returned with
**Withdraw & Edit** (the approval history is preserved and it must be resubmitted); a Fully Approved
event is locked.

---

## 4. Roles

| | IC User | Manager | Admin |
|---|---|---|---|
| Create / edit own draft events, vendors, quotations | ✓ | | ✓ |
| Submit for approval, withdraw, cancel | ✓ | | ✓ |
| See events | own only (unless granted "view all") | only those assigned to them | all |
| Approve / reject event and vendors | | only where assigned as approver | only where assigned |
| Manage users, event types, vendor categories, settings | | | ✓ |
| Approval records, full history, e-mail outbox, CSV export | | | ✓ |

Every rule is enforced **server-side**. Changing an ID in the URL returns `403` — including for
quotation downloads and the approval page.

---

## 5. Files

```
ic-events-hub/
  server.py        HTTP server, routing, permissions, approval logic, budget calculation
  db.py            SQLite schema, migrations, password hashing, seeded users
  mailer.py        The five e-mail templates (KABi brand) + outbox + SMTP delivery
  seed_demo.py     Optional realistic demo data
  START.bat        Double-click launcher
  web/             Front end: index.html, app.js, app.css, icons.js, assets/ (KABi logos)
  data/ic_hub.db   The database — this file is your data, back it up
  uploads/         Quotation files, vendor images and attachments
```

**Backup** = copy the `data/` and `uploads/` folders.

Tables: `users`, `events`, `vendors`, `vendor_files`, `vendor_links`, `approvals`,
`vendor_approvals`, `notifications`, `approval_history`, `emails`, `lookups`, `settings`, `sessions`.

---

## 6. Security

* Passwords stored as PBKDF2-SHA256, 120 000 iterations, unique salt per user.
* Sessions are HttpOnly cookies, 7-day expiry, invalidated on logout, password change or deactivation.
* Every state-changing request requires an `X-Requested-With` header (CSRF guard).
* Login throttling after 6 failed attempts.
* Uploads: 15 MB limit, extension whitelist (PDF, JPG, PNG, DOC, DOCX, XLS, XLSX, PPT, PPTX, CSV, TXT),
  files stored under random names and served only after a permission check.
* All SQL uses parameterised queries; all user text is HTML-escaped on output.

The server binds to all interfaces so colleagues on the same network can open the link in the e-mail.
If you only want it reachable from this PC, change `"0.0.0.0"` to `"127.0.0.1"` in `server.py`.

---

## 7. Branding

Official KABi palette and typography throughout the app and the e-mails: Mid Blue `#216AB1`,
Turquoise `#1EAFD9`, Topaz `#0EB3AE`, Meteorite `#3D3185`, Soft Green `#3BB691`; Poppins for
headings, Calibri/Arial for body text. The white KABi logo is used on coloured backgrounds and the
colour mark as the browser icon — never recoloured or distorted. Interface icons are Tabler Icons
(inline SVG, no emoji, no external requests).

To swap the logo, replace the files in `web/assets/` and restart the server.

---

## 8. Deliberately out of scope for v1

Procurement, invoicing, payments, contracts, attendance, surveys, calendar and finance integrations —
as specified. The schema and routing are structured so any of these can be added later without
reworking the approval engine.
