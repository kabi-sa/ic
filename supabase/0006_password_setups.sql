-- Set-up links: each person chooses their own password, so nothing here stores one.
-- Safe to run repeatedly. Touches only ic_events; HC HUB's public schema is untouched.

create table if not exists ic_events.password_setups (
    id           integer generated always as identity primary key,
    token_digest text    not null,
    user_id      integer not null,
    expires_at   text    not null,
    used         integer not null default 0,
    used_at      text,
    used_ip      text,
    created_by   integer,
    created_at   text    not null
);

create index if not exists password_setups_digest_idx
    on ic_events.password_setups (token_digest);

revoke all on ic_events.password_setups from anon, authenticated;

select count(*) as setup_table_present
  from information_schema.tables
 where table_schema = 'ic_events' and table_name = 'password_setups';
