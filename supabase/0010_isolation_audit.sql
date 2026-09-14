-- ════════════════════════════════════════════════════════════════════════════
--  Are the IC Events Approval Hub and the HC Report Hub actually separate?
--
--  Read-only. Changes nothing. Paste into the Supabase SQL editor and Run.
--  One row per question, with the answer and a plain PASS / CHECK verdict.
--
--  This replaces 0002, which asked whether tables with our names exist in
--  `public`. That question has a false answer built in: the HC Report Hub may
--  perfectly well have its own `users` or `events` table, and a name collision
--  between two schemas is not data mixing -- it is two separate tables that
--  happen to share a word. So the checks below ask about things that would
--  actually constitute mixing: our rows inside their schema, foreign keys
--  crossing between the two, and rights that would let one reach the other.
-- ════════════════════════════════════════════════════════════════════════════

with
-- 1 ── where our tables live
ic_tables as (
    select count(*) as n from information_schema.tables
     where table_schema = 'ic_events'
),

-- 2 ── our data inside their schema. Not "a table with our name", but a table
--      carrying our fingerprint: columns only this application creates.
ic_fingerprints as (
    select count(distinct table_name) as n
      from information_schema.columns
     where table_schema = 'public'
       and column_name in ('event_number', 'approver_level', 'record_kind',
                           'is_final_approver', 'scope_event_id', 'quotation_amount',
                           'selected_option_id', 'passwordless')
),

-- 3 ── structural coupling: a foreign key with one foot in each schema, either way
crossing_keys as (
    select count(*) as n
      from information_schema.table_constraints tc
      join information_schema.constraint_column_usage ccu
        on ccu.constraint_name = tc.constraint_name
       and ccu.constraint_schema = tc.constraint_schema
     where tc.constraint_type = 'FOREIGN KEY'
       and ((tc.table_schema = 'ic_events' and ccu.table_schema = 'public')
         or (tc.table_schema = 'public'    and ccu.table_schema = 'ic_events'))
),

-- 4 ── views or functions of ours parked in their schema
ic_views as (
    select count(*) as n from information_schema.views
     where table_schema = 'public'
       and view_definition ilike '%ic_events.%'
),

-- 5 ── the public API roles hold nothing on our tables
role_grants as (
    select count(*) as n from information_schema.role_table_grants
     where table_schema = 'ic_events' and grantee in ('anon', 'authenticated')
),

-- 6 ── ...and cannot even enter the schema to look
anon_usage as (
    select has_schema_privilege('anon', 'ic_events', 'USAGE') as b
),
authd_usage as (
    select has_schema_privilege('authenticated', 'ic_events', 'USAGE') as b
),

-- 7 ── what else is in this database, so the separation is visible rather than asserted
other_schemas as (
    select count(distinct table_schema) as n from information_schema.tables
     where table_schema not in ('ic_events', 'information_schema', 'pg_catalog',
                                'pg_toast', 'extensions', 'graphql', 'graphql_public',
                                'realtime', 'storage', 'vault', 'auth', 'net',
                                'pgsodium', 'pgsodium_masks', 'supabase_migrations')
),
public_tables as (
    select count(*) as n from information_schema.tables where table_schema = 'public'
),

-- 8 ── every table this application expects. Listing our own names is fair; it is
--      guessing at the other hub's that was not.
expected(name) as (
    values ('approval_history'),('approval_links'),('approvals'),('emails'),
           ('event_approvers'),('event_options'),('event_photos'),('events'),
           ('known_devices'),('login_codes'),('lookups'),('notifications'),
           ('password_setups'),('sessions'),('settings'),('users'),
           ('vendor_approvals'),('vendor_files'),('vendor_links'),('vendors')
),
absent as (
    select coalesce(string_agg(e.name, ', ' order by e.name), 'none') as names,
           count(*) as n
      from expected e
     where not exists (select 1 from information_schema.tables t
                        where t.table_schema = 'ic_events' and t.table_name = e.name)
)

select * from (
    select 1 as ord,
           'IC tables, all inside ic_events'              as check_,
           (select n::text from ic_tables)                as answer,
           '19 or 20'                                     as expected,
           case when (select n from ic_tables) >= 19 then 'PASS' else 'CHECK' end as verdict
    union all
    select 2, 'IC data sitting in public (by column fingerprint)',
           (select n::text from ic_fingerprints), '0',
           case when (select n from ic_fingerprints) = 0 then 'PASS' else 'CHECK' end
    union all
    select 3, 'Foreign keys crossing between the two schemas',
           (select n::text from crossing_keys), '0',
           case when (select n from crossing_keys) = 0 then 'PASS' else 'CHECK' end
    union all
    select 4, 'Views in public that read from ic_events',
           (select n::text from ic_views), '0',
           case when (select n from ic_views) = 0 then 'PASS' else 'CHECK' end
    union all
    select 5, 'Grants on IC tables held by anon / authenticated',
           (select n::text from role_grants), '0',
           case when (select n from role_grants) = 0 then 'PASS' else 'CHECK' end
    union all
    select 6, 'anon can enter the ic_events schema',
           (select b::text from anon_usage), 'false',
           case when (select b from anon_usage) then 'CHECK' else 'PASS' end
    union all
    select 7, 'authenticated can enter the ic_events schema',
           (select b::text from authd_usage), 'false',
           case when (select b from authd_usage) then 'CHECK' else 'PASS' end
    union all
    select 8, 'HC Report Hub tables in public, untouched by us',
           (select n::text from public_tables), 'whatever HC has',
           'INFO'
    union all
    select 9, 'IC tables expected but absent from ic_events',
           (select names from absent), 'none',
           case when (select n from absent) = 0 then 'PASS' else 'CHECK' end
    union all
    select 10, 'Other application schemas in this database',
           (select n::text from other_schemas), 'informational',
           'INFO'
) rows_
order by ord;
