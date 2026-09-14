-- The one table the hosted store is missing, and a look at why.
--
-- `login_codes` holds the one-time sign-in codes. Without it, asking for a code
-- fails with an unexplained error rather than a clean message. Everything else the
-- application expects is present.
--
-- Safe to run repeatedly. Touches only ic_events.

create table if not exists ic_events.login_codes (
    id           integer generated always as identity primary key,
    email        text    not null,
    code_hash    text    not null,
    expires_at   text    not null,
    attempts     integer not null default 0,
    used         integer not null default 0,
    requested_ip text,
    created_at   text    not null
);

-- Codes are looked up by address, and expired ones are swept by date.
create index if not exists login_codes_email_idx on ic_events.login_codes (email);
create index if not exists login_codes_expiry_idx on ic_events.login_codes (expires_at);

-- Same wall as every other table here: the public API roles get nothing.
revoke all on ic_events.login_codes from anon, authenticated;

-- ── why was it missing? ──────────────────────────────────────────────────────
-- The application creates its own additive tables on start-up, and this one never
-- appeared, so the likeliest cause is that the role in the connection string cannot
-- create tables in this schema -- in which case the NEXT table added will go missing
-- the same way, silently. This lists who can, so we know whether to expect that.
select r.rolname                                              as role_,
       has_schema_privilege(r.rolname, 'ic_events', 'USAGE')  as can_enter,
       has_schema_privilege(r.rolname, 'ic_events', 'CREATE') as can_create_tables
  from pg_roles r
 where r.rolname not like 'pg\_%'
   and r.rolname in ('postgres', 'anon', 'authenticated', 'service_role',
                     'authenticator', 'supabase_admin', 'supabase_auth_admin',
                     'supabase_storage_admin', 'dashboard_user')
 order by can_create_tables desc, r.rolname;
