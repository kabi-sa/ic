# Sharing the HC HUB Supabase project without mixing the data

The IC Events Approval Hub stores its data in the **same Supabase project** as the
HC Report Hub, but in a **separate schema** that the HC HUB code and keys cannot
reach. This document explains exactly how that is enforced, how to verify it at
any time, and what to avoid.

---

## What "separate" means here — precisely

| Shared | Separate |
|---|---|
| The Supabase project and its dashboard | The **schema** (`ic_events` vs `public`) |
| The Postgres cluster and compute (Nano) | The tables — no shared names, no shared rows |
| The connection pool (15 connections) | The credentials each app connects with |
| Backups (one backup covers both) | API exposure — HC HUB's keys cannot see `ic_events` |
| Anyone with project-owner access | Nothing references anything across the two |

**Read that left column honestly:** this is *logical* separation inside one
database, not two physically separate databases. Data cannot leak between the two
apps, but they share compute, backups and the project dashboard. If you ever need
physical separation — different billing, different backups, no shared dashboard —
that means a second Supabase project, and moving is a config change, not a rewrite.

---

## The three barriers that keep the data apart

**1 · Different schema.** Every IC Events table is created as
`ic_events.<table>`. Nothing is created in `public`. Even where names overlap
conceptually (both apps have "users"-like concepts), they are different tables in
different namespaces and cannot collide.

**2 · The HC HUB keys have no access.** HC HUB's browser talks to Postgres with
the **anon key**, which is embedded in its `index.html` and grants full CRUD on
`public`. The migration explicitly removes that role's access to the IC schema:

```sql
revoke all on schema ic_events from anon, authenticated;
revoke all privileges on all tables in schema ic_events from anon, authenticated;
alter default privileges in schema ic_events revoke all on tables from anon, authenticated;
```

The last line matters most: it covers **future** tables too, so a table added
next year is closed by default rather than open by accident.

**3 · Not exposed to the API.** Supabase only serves schemas listed under
*Settings → API → Exposed schemas* (by default just `public`). `ic_events` is not
on that list, so there is no REST or GraphQL route to it at all. IC Events reaches
its data over a direct Postgres connection, using the database credentials held
in Vercel — not the anon key.

Belt and braces: even if someone added `ic_events` to the exposed schemas by
mistake, barrier 2 means the anon role still has no privileges on the tables.

---

## Step by step

### Step 1 — create the schema *(done)*

`supabase/0001_ic_events.sql`, run in the HC HUB project's SQL editor. It created
18 tables inside `ic_events` and applied the revokes above. It reported
`Success. No rows returned`, which is the expected result.

### Step 2 — verify the isolation

In the SQL editor, run `supabase/0010_isolation_audit.sql`. It is read-only and
returns one row per question, each with a **PASS** or **CHECK** verdict:

| check | expected |
|---|---|
| IC tables, all inside `ic_events` | 19 or 20 |
| IC data sitting in `public` (by column fingerprint) | 0 |
| Foreign keys crossing between the two schemas | 0 |
| Views in `public` that read from `ic_events` | 0 |
| Grants on IC tables held by anon / authenticated | 0 |
| anon / authenticated can enter `ic_events` | false, false |
| HC Report Hub tables in `public` | informational — untouched by us |

It replaces `0002_verify_isolation.sql`, which asked whether tables *named* like
ours exist in `public`. That question has a wrong answer built into it: the HC
Report Hub may perfectly well have its own `users` or `events` table, and two
schemas sharing a word is not data mixing. The audit asks instead about the
things that would actually constitute mixing — our rows inside their schema,
foreign keys with one foot in each, and rights that would let one reach the
other. It also stops guessing HC's table names, which it had no business
knowing.

Also glance at *Settings → API → Exposed schemas* — it should list `public`
(and `graphql_public`), **not** `ic_events`.

### Step 3 — get the connection string

Supabase → **Connect** button (top bar, beside the project name) →
**Transaction pooler** tab → copy the URI. It contains `pooler` and ends
`:6543/postgres`. Replace `[YOUR-PASSWORD]` with the real database password
(reset it on *Settings → Database* if you don't have it).

The **transaction pooler** is the right choice for two reasons: serverless
functions open many short-lived connections, and it stops IC Events from
exhausting the 15-connection pool that HC HUB also depends on.

### Step 4 — check it from your PC

Double-click **`VERIFY-SUPABASE.bat`**, paste the string, press Enter. It runs
the connection, schema, isolation and read/write checks and prints PASS/FAIL. The
string is not saved unless you use `SAVE-CONNECTION.bat`.

### Step 5 — deploy with the secret held by Vercel

Vercel → project → **Settings → Environment Variables** → add
`IC_DATABASE_URL` = the string from step 3, for Production and Preview. Vercel
encrypts it; it is never shown again and never appears in the repository
(`.gitignore` excludes `.env`).

### Step 6 — confirm the live deployment

Open `https://<your-app>.vercel.app/api/health`. It needs no sign-in and returns
no business data:

```json
{ "ok": true, "store": "postgres", "schema": "ic_events",
  "isolated": true, "tables": 18, "tables_in_public": 0 }
```

`"isolated": true` means both that nothing landed in `public` and that the anon
role has no access. If it ever reads `false`, something changed in Supabase —
re-run step 2 to see which check broke.

### Step 7 — point e-mails at the new address

Sign in as `ic@kabi.ai` → **Administration → Settings → Application base URL** →
set it to the Vercel URL. That is the address behind **Review Event** in approval
e-mails; while it still says `192.168.x.x`, links sent to approvers will not open.

---

## Things that would break the isolation — don't do these

* **Adding `ic_events` to *Settings → API → Exposed schemas***. This is the one
  switch that would put IC data behind the shared anon key.
* **Using "Harden Data API"** to change which schema is exposed. Leave it alone;
  HC HUB depends on `public` being exposed.
* **Granting the anon or authenticated role anything on `ic_events`**, e.g.
  `grant usage on schema ic_events to anon`.
* **Creating IC tables without the `ic_events.` prefix** while `search_path` is
  set to `public` — they would land in HC HUB's namespace. The migration always
  qualifies names, and step 2 catches it if it ever happens.
* **Putting the service-role key in the browser.** The app never needs it; it
  connects with the database credentials, server-side only.

## Things that are safe

* Running `0001_ic_events.sql` again — every statement is guarded.
* HC HUB deploys, migrations and schema changes — they only touch `public`.
* Backups and restores — they cover the whole project, both schemas together.
* Deleting all IC Events data: `drop schema ic_events cascade;` removes this app
  entirely and cannot affect HC HUB.
