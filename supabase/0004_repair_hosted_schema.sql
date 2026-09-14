-- Repair an ic_events schema created from an earlier copy of 0001_ic_events.sql.
-- Safe to run repeatedly: every statement is guarded, and existing rows are kept.
-- Touches only the ic_events schema; HC HUB's public schema is never referenced.

-- ── 1. columns added after the first release ───────────────────────────────
alter table ic_events.emails          add column if not exists cc text;
alter table ic_events.events          add column if not exists selected_option_id integer;
alter table ic_events.events          add column if not exists current_level      integer;
alter table ic_events.events          add column if not exists decided_at         text;
alter table ic_events.users           add column if not exists passwordless       integer not null default 1;
alter table ic_events.users           add column if not exists approver_level     integer;
alter table ic_events.users           add column if not exists is_final_approver  integer not null default 0;
alter table ic_events.users           add column if not exists can_view_archive   integer not null default 0;
alter table ic_events.sessions        add column if not exists scope_event_id     integer;
alter table ic_events.event_approvers add column if not exists level              integer not null default 1;
alter table ic_events.event_approvers add column if not exists status             text not null default 'pending';
alter table ic_events.event_approvers add column if not exists is_final           integer not null default 0;
alter table ic_events.event_approvers add column if not exists decided_at         text;
alter table ic_events.event_photos    add column if not exists kind               text not null default 'photo';
alter table ic_events.vendors         add column if not exists option_id          integer;

-- ── 2. settings (the app falls back to defaults, but writes need the rows) ──
insert into ic_events.settings(key, value) values
  ('org_name', 'KABi'),
  ('app_name', 'IC Events Approval Hub'),
  ('currency', 'SAR'),
  ('default_vat_rate', '15'),
  ('app_base_url', 'https://ic-beta.vercel.app'),   -- emailed approval links resolve here
  ('smtp_enabled', '0'),
  ('smtp_host', ''),
  ('smtp_port', '587'),
  ('smtp_user', ''),
  ('smtp_password', ''),
  ('smtp_tls', '1'),
  ('mail_from', 'ic-hub@kabi.ai'),
  ('mail_from_name', 'KABi IC Events Approval Hub')
on conflict (key) do nothing;

update ic_events.settings set value = 'https://ic-beta.vercel.app'
 where key = 'app_base_url' and value like '%localhost%';

-- ── 3. the Event Type and Vendor Category drop-downs ──────────────────────
insert into ic_events.lookups(kind, value, sort, active) values
  ('event_type', 'Internal Event', 0, 1),
  ('event_type', 'Employee Engagement', 1, 1),
  ('event_type', 'Celebration', 2, 1),
  ('event_type', 'Awareness', 3, 1),
  ('event_type', 'Workshop', 4, 1),
  ('event_type', 'Team Activity', 5, 1),
  ('event_type', 'Campaign', 6, 1),
  ('event_type', 'Other', 7, 1),
  ('vendor_category', 'Catering', 0, 1),
  ('vendor_category', 'Event Management', 1, 1),
  ('vendor_category', 'Decoration', 2, 1),
  ('vendor_category', 'Entertainment', 3, 1),
  ('vendor_category', 'Photography', 4, 1),
  ('vendor_category', 'Gifts', 5, 1),
  ('vendor_category', 'Printing', 6, 1),
  ('vendor_category', 'Transportation', 7, 1),
  ('vendor_category', 'Venue', 8, 1),
  ('vendor_category', 'Other', 9, 1)
on conflict (kind, value) do nothing;

-- ── 4. confirm ────────────────────────────────────────────────────────────
select (select count(*) from ic_events.lookups where kind='event_type' and active=1) as event_types,
       (select count(*) from ic_events.lookups where kind='vendor_category' and active=1) as vendor_categories,
       (select count(*) from ic_events.settings)                                    as settings,
       (select count(*) from ic_events.users)                                       as accounts,
       (select count(*) from information_schema.columns
          where table_schema='ic_events' and table_name='emails' and column_name='cc') as cc_column;
