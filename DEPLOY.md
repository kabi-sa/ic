# Deploying to Vercel + Supabase (HC HUB project)

The app runs on Postgres when `IC_DATABASE_URL` is set, and on the local SQLite
file when it isn't — so the same code serves both `python server.py` on your PC
and the hosted version.

## Data isolation — how HC HUB stays untouched

| | HC Report Hub | IC Events Approval Hub |
|---|---|---|
| Schema | `public` | **`ic_events`** |
| Tables | reports, report_history, archived_reports, profiles, invites, sections | events, vendors, approvals, users … (18 tables) |
| Reached by | browser, with the **anon key** | the IC Events server only, over a direct Postgres connection |
| Exposed to the Supabase REST API | yes | **no** — `usage` is revoked from `anon` and `authenticated` |

Nothing is created in `public`; no foreign key, view or trigger crosses between
the two. The HC HUB anon key is a shared team credential with full CRUD on
`public` — because `ic_events` is not exposed to the API and not granted to that
role, it cannot read a single row of event, budget or vendor data. Files
(quotations, photos, announcements) are stored as blobs inside `ic_events`, not
in a shared storage bucket.

Verified after each change:

```bash
python -c "import re;s=open('supabase/0001_ic_events.sql').read().lower();\
print([c for c in re.findall(r'create table if not exists ([a-z_.]+)',s) if not c.startswith('ic_events.')] or 'isolation OK')"
```

---

## Step 1 — create the schema (once)

Supabase → the **HC HUB** project → **SQL Editor** → paste all of
`supabase/0001_ic_events.sql` → **Run**.

It ends by printing either `Isolation OK: no IC Events tables in public` or a
warning listing anything that leaked. Expect the first.

## Step 2 — get the connection string

Supabase → **Project Settings → Database → Connection pooling** → mode
**Transaction** → copy the URI. It looks like:

```
postgresql://postgres.wtpiygxowjjmdcyxorln:[PASSWORD]@aws-0-<region>.pooler.supabase.com:6543/postgres
```

Use the **pooler on port 6543** (serverless functions open many short
connections; the direct 5432 port will exhaust them). Append `?sslmode=require`.

## Step 3 — push the code

```bash
git remote add origin https://github.com/<your-account>/ic-events-hub.git
git push -u origin main
```

`.gitignore` already excludes `data/`, `uploads/`, `.env` and logs, so no
database, uploaded file or secret is committed.

## Step 4 — create the Vercel project

1. Vercel → **Add New → Project** → import the repo.
2. Framework preset: **Other**. No build command; no output directory.
3. **Environment Variables** → add, for Production *and* Preview:

   | Name | Value |
   |---|---|
   | `IC_DATABASE_URL` | the pooler URI from step 2, with `?sslmode=require` |

4. **Deploy.**

Vercel installs `psycopg[binary]` from `requirements.txt` and routes every path
to `api/index.py`, which reuses the same request handler as the local server.

## Step 5 — first run

Open the deployment URL. The first request seeds the settings, the event-type and
vendor-category lists, and the five accounts — the same as locally:

* `ic@kabi.ai` / `IC@kabi2026` — administrator, the only password account
* everyone else signs in with name + e-mail

Then, signed in as the administrator:

1. **Administration → Settings → Application base URL** → set it to the Vercel
   URL. This is the address behind **Review Event** in approval e-mails; if it
   still points at `192.168.x.x`, links in e-mails will not resolve.
2. Change the administrator password.
3. Add your real people and remove the placeholder accounts.

## Step 6 — e-mail

Unchanged by hosting: messages are composed and stored in the outbox, and you
either send them from your own mailbox (**Open designed e-mail** → the `.eml`
draft) or switch on SMTP in Settings for automatic delivery. Being on HTTPS also
restores **Copy formatted**, which the browser blocks on plain `http://`.

## Rolling back

The local copy keeps working — leave `IC_DATABASE_URL` unset and run
`python server.py`. The two stores are independent; nothing is shared.

## Notes

* **Free-tier size.** Files live in the database. Supabase's free tier gives
  500 MB — fine for quotations, but a few hundred event photos will use it up.
  Moving blobs to a private Supabase Storage bucket is a contained change when
  that day comes.
* **Cold starts.** The first request after idle takes a second or two while the
  function boots and connects.
* **Backups.** Supabase handles them for the whole project; `ic_events` is
  included automatically.
