-- ════════════════════════════════════════════════════════════════════════════
--  Isolation check — paste into the HC HUB SQL editor and Run.
--  One row, six columns. Expected values are in the comment on each line.
-- ════════════════════════════════════════════════════════════════════════════

select
  -- expect 18
  (select count(*)
     from information_schema.tables
    where table_schema = 'ic_events')                            as ic_tables,

  -- expect 0  (nothing of ours in HC HUB's schema)
  (select count(*)
     from information_schema.tables
    where table_schema = 'public'
      and table_name in ('events','vendors','event_options','event_approvers',
                         'approvals','vendor_approvals','approval_history','emails',
                         'lookups','settings','vendor_files','vendor_links',
                         'event_photos','known_devices','approval_links',
                         'users','sessions','notifications'))     as leaked_into_public,

  -- expect 0  (the HC HUB anon key holds no rights on our tables)
  (select count(*)
     from information_schema.role_table_grants
    where table_schema = 'ic_events'
      and grantee in ('anon','authenticated'))                    as api_role_grants,

  -- expect false, false
  has_schema_privilege('anon','ic_events','USAGE')                as anon_can_use,
  has_schema_privilege('authenticated','ic_events','USAGE')       as authd_can_use,

  -- expect 6  (HC HUB's own tables still there, untouched)
  (select count(*)
     from information_schema.tables
    where table_schema = 'public'
      and table_name in ('reports','report_history','archived_reports',
                         'profiles','invites','sections'))         as hc_hub_tables;


-- ─────────────────────────────────────────────────────────────────────────────
--  Optional detail: list what was created. Run separately if you want to see
--  the 18 table names.
-- ─────────────────────────────────────────────────────────────────────────────
-- select table_name
--   from information_schema.tables
--  where table_schema = 'ic_events'
--  order by table_name;
