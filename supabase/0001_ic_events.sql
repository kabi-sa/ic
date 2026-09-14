-- ════════════════════════════════════════════════════════════════════════════
--  KABi · IC Events Approval Hub — Supabase schema
--  Run once in the HC HUB project's SQL editor.
--
--  ISOLATION — this is the whole point of this file:
--    • Everything lives in its own schema `ic_events`. Nothing is created in
--      `public`, so it cannot collide with the HC Report Hub's tables
--      (reports, report_history, archived_reports, profiles, invites, sections).
--    • `ic_events` is NOT added to the PostgREST exposed schemas, and usage is
--      revoked from `anon` and `authenticated`. The HC HUB anon key — a shared
--      team credential with full CRUD on `public` — therefore cannot read a
--      single row of event, budget or vendor data.
--    • Only the IC Events server reaches this schema, over a direct Postgres
--      connection using its own role/credentials.
--    • No foreign key, view or trigger crosses into `public`.
--
--  Safe to re-run: every statement is guarded.
-- ════════════════════════════════════════════════════════════════════════════

create schema if not exists ic_events;

-- Keep the API roles out. (Supabase grants these on `public` by default; we
-- explicitly deny them here so nothing is exposed by accident later.)
revoke all on schema ic_events from anon, authenticated;
revoke all privileges on all tables in schema ic_events from anon, authenticated;
alter default privileges in schema ic_events revoke all on tables from anon, authenticated;

set search_path to ic_events;

-- ── people ─────────────────────────────────────────────────────────────────
create table if not exists ic_events.users (
    id                integer generated always as identity primary key,
    name              text    not null,
    email             text    not null unique,
    password_hash     text    not null,
    role              text    not null,
    department        text,
    job_title        text,
    phone            text,
    invited           integer not null default 0,
    can_view_archive  integer not null default 0,
    passwordless      integer not null default 1,
    approver_level    integer,
    is_final_approver integer not null default 0,
    manager_id        integer references ic_events.users(id),
    can_view_all      integer not null default 0,
    active            integer not null default 1,
    created_at        text    not null
);

create table if not exists ic_events.sessions (
    token          text primary key,
    user_id        integer not null references ic_events.users(id) on delete cascade,
    scope_event_id integer,
    created_at     text not null,
    expires_at     text not null
);

create table if not exists ic_events.known_devices (
    token        text primary key,
    user_id      integer not null references ic_events.users(id) on delete cascade,
    name_used    text,
    created_at   text not null,
    last_used_at text,
    expires_at   text not null
);

-- ── events ─────────────────────────────────────────────────────────────────
create table if not exists ic_events.events (
    id                 integer generated always as identity primary key,
    event_number       text    not null unique,
    event_name         text    not null,
    event_type         text,
    event_date         text,
    start_time         text,
    end_time           text,
    location           text,
    expected_attendees integer,
    description        text,
    miscellaneous_cost double precision not null default 0,
    total_budget       double precision not null default 0,
    approved_budget    double precision not null default 0,
    rejected_budget    double precision not null default 0,
    status             text    not null default 'draft',
    created_by         integer not null references ic_events.users(id),
    approver_id        integer references ic_events.users(id),
    submitted_at       text,
    decided_at         text,
    executed           integer not null default 0,
    execution_date     text,
    execution_notes    text,
    selected_option_id integer,
    current_level      integer,
    created_at         text    not null,
    updated_at         text    not null
);

create table if not exists ic_events.approval_links (
    token       text primary key,
    event_id    integer not null references ic_events.events(id) on delete cascade,
    approver_id integer not null references ic_events.users(id) on delete cascade,
    expires_at  text not null,
    revoked     integer not null default 0,
    opened_at   text,
    opened_name text,
    opened_email text,
    open_count  integer not null default 0,
    created_at  text not null
);

create table if not exists ic_events.event_approvers (
    id         integer generated always as identity primary key,
    event_id   integer not null references ic_events.events(id) on delete cascade,
    user_id    integer not null references ic_events.users(id) on delete cascade,
    level      integer not null default 1,
    status     text    not null default 'pending',
    is_final   integer not null default 0,
    decided_at text,
    sort       integer not null default 0,
    created_at text    not null,
    unique (event_id, user_id)
);

create table if not exists ic_events.event_options (
    id          integer generated always as identity primary key,
    event_id    integer not null references ic_events.events(id) on delete cascade,
    name        text    not null,
    description text,
    sort        integer not null default 0,
    status      text    not null default 'pending',
    vendor_cost double precision not null default 0,
    total       double precision not null default 0,
    created_at  text    not null,
    updated_at  text    not null
);

create table if not exists ic_events.vendors (
    id               integer generated always as identity primary key,
    event_id         integer not null references ic_events.events(id) on delete cascade,
    option_id        integer references ic_events.event_options(id) on delete cascade,
    vendor_name      text    not null,
    category         text,
    contact_name     text,
    contact_email    text,
    contact_phone    text,
    description      text,
    quotation_amount double precision not null default 0,
    vat_rate         double precision not null default 15,
    vat              double precision not null default 0,
    total_amount     double precision not null default 0,
    approval_status  text    not null default 'pending',
    rejection_reason text,
    quotation_file   text,
    quotation_name   text,
    quotation_mime   text,
    quotation_size   integer,
    created_at       text    not null,
    updated_at       text    not null
);

-- ── files (kept in-schema so nothing lands in a shared bucket) ─────────────
create table if not exists ic_events.vendor_files (
    id         integer generated always as identity primary key,
    vendor_id  integer not null references ic_events.vendors(id) on delete cascade,
    event_id   integer not null references ic_events.events(id) on delete cascade,
    kind       text    not null default 'attachment',
    file_path  text    not null,
    file_name  text    not null,
    mime       text,
    size       integer,
    caption    text,
    content    bytea,
    created_at text    not null
);

create table if not exists ic_events.event_photos (
    id          integer generated always as identity primary key,
    event_id    integer not null references ic_events.events(id) on delete cascade,
    kind        text    not null default 'photo',
    file_path   text    not null,
    file_name   text    not null,
    mime        text,
    size        integer,
    caption     text,
    content     bytea,
    uploaded_by integer references ic_events.users(id),
    created_at  text    not null
);

create table if not exists ic_events.vendor_links (
    id         integer generated always as identity primary key,
    vendor_id  integer not null references ic_events.vendors(id) on delete cascade,
    event_id   integer not null references ic_events.events(id) on delete cascade,
    label      text,
    url        text not null,
    created_at text not null
);

-- ── decisions & audit ──────────────────────────────────────────────────────
create table if not exists ic_events.approvals (
    id                     integer generated always as identity primary key,
    event_id               integer not null references ic_events.events(id) on delete cascade,
    approver_id            integer not null references ic_events.users(id),
    event_decision         text,
    event_rejection_reason text,
    decision_date          text,
    created_at             text not null
);

create table if not exists ic_events.vendor_approvals (
    id               integer generated always as identity primary key,
    vendor_id        integer not null references ic_events.vendors(id) on delete cascade,
    event_id         integer not null references ic_events.events(id) on delete cascade,
    approver_id      integer not null references ic_events.users(id),
    decision         text    not null,
    rejection_reason text,
    decision_date    text,
    created_at       text    not null
);

create table if not exists ic_events.approval_history (
    id              integer generated always as identity primary key,
    event_id        integer not null references ic_events.events(id) on delete cascade,
    action          text    not null,
    performed_by    integer references ic_events.users(id),
    role            text,
    comments        text,
    previous_status text,
    new_status      text,
    created_at      text    not null
);

create table if not exists ic_events.notifications (
    id         integer generated always as identity primary key,
    user_id    integer not null references ic_events.users(id) on delete cascade,
    event_id   integer references ic_events.events(id) on delete cascade,
    type       text    not null,
    title      text    not null,
    message    text,
    read       integer not null default 0,
    created_at text    not null
);

create table if not exists ic_events.emails (
    id         integer generated always as identity primary key,
    event_id   integer references ic_events.events(id) on delete cascade,
    to_email   text not null,
    to_name    text,
    cc         text,
    subject    text not null,
    body_html  text not null,
    body_text  text,
    type       text not null,
    status     text not null default 'queued',
    error      text,
    sent_at    text,
    created_at text not null
);

-- ── configuration ──────────────────────────────────────────────────────────
create table if not exists ic_events.lookups (
    id     integer generated always as identity primary key,
    kind   text    not null,
    value  text    not null,
    sort   integer not null default 0,
    active integer not null default 1,
    unique (kind, value)
);

create table if not exists ic_events.settings (
    key   text primary key,
    value text
);

-- ── indexes ────────────────────────────────────────────────────────────────
create index if not exists idx_ic_events_creator   on ic_events.events(created_by);
create index if not exists idx_ic_events_approver  on ic_events.events(approver_id);
create index if not exists idx_ic_vendors_event    on ic_events.vendors(event_id);
create index if not exists idx_ic_vendors_option   on ic_events.vendors(option_id);
create index if not exists idx_ic_options_event    on ic_events.event_options(event_id);
create index if not exists idx_ic_vfiles_vendor    on ic_events.vendor_files(vendor_id);
create index if not exists idx_ic_vlinks_vendor    on ic_events.vendor_links(vendor_id);
create index if not exists idx_ic_ephotos_event    on ic_events.event_photos(event_id);
create index if not exists idx_ic_notif_user       on ic_events.notifications(user_id, read);
create index if not exists idx_ic_hist_event       on ic_events.approval_history(event_id);
create index if not exists idx_ic_eapprovers_event on ic_events.event_approvers(event_id);

-- ── verify the isolation held ───────────────────────────────────────────────
do $$
declare leaked text;
begin
  select string_agg(table_name, ', ') into leaked
  from information_schema.tables
  where table_schema = 'public'
    and table_name in ('events','vendors','event_options','event_approvers','approvals',
                       'vendor_approvals','approval_history','emails','lookups','settings',
                       'vendor_files','vendor_links','event_photos','known_devices',
                       'approval_links');
  if leaked is not null then
    raise warning 'IC Events tables found in public schema: % — they should only exist in ic_events', leaked;
  else
    raise notice 'Isolation OK: no IC Events tables in public. HC HUB data untouched.';
  end if;
end $$;
