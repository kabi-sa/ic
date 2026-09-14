-- Bring the hosted schema up to the code, now.
-- Safe to run repeatedly: every statement is guarded.
-- Touches only ic_events; HC HUB's public schema is never referenced.

alter table ic_events.events           add column if not exists record_kind          text not null default 'activity';
alter table ic_events.events           add column if not exists audience             text;
alter table ic_events.events           add column if not exists executed             integer not null default 0;
alter table ic_events.events           add column if not exists execution_date       text;
alter table ic_events.events           add column if not exists execution_notes      text;
alter table ic_events.events           add column if not exists selected_option_id   integer;
alter table ic_events.events           add column if not exists current_level        integer;
alter table ic_events.events           add column if not exists rejected_budget      real not null default 0;
alter table ic_events.events           add column if not exists decided_at           text;
alter table ic_events.users            add column if not exists phone                text;
alter table ic_events.users            add column if not exists job_title            text;
alter table ic_events.users            add column if not exists can_view_all         integer not null default 0;
alter table ic_events.users            add column if not exists invited              integer not null default 0;
alter table ic_events.users            add column if not exists can_view_archive     integer not null default 0;
alter table ic_events.users            add column if not exists passwordless         integer not null default 1;
alter table ic_events.users            add column if not exists approver_level       integer;
alter table ic_events.users            add column if not exists is_final_approver    integer not null default 0;
alter table ic_events.sessions         add column if not exists scope_event_id       integer;
alter table ic_events.emails           add column if not exists body_text            text;
alter table ic_events.emails           add column if not exists cc                   text;
alter table ic_events.event_approvers  add column if not exists level                integer not null default 1;
alter table ic_events.event_approvers  add column if not exists status               text not null default 'pending';
alter table ic_events.event_approvers  add column if not exists is_final             integer not null default 0;
alter table ic_events.event_approvers  add column if not exists decided_at           text;
alter table ic_events.event_photos     add column if not exists kind                 text not null default 'photo';
alter table ic_events.vendors          add column if not exists vat_rate             real not null default 15;
alter table ic_events.vendors          add column if not exists quotation_name       text;
alter table ic_events.vendors          add column if not exists quotation_mime       text;
alter table ic_events.vendors          add column if not exists quotation_size       integer;
alter table ic_events.vendors          add column if not exists option_id            integer;

select count(*) as record_kind_present
  from information_schema.columns
 where table_schema = 'ic_events' and table_name = 'events'
   and column_name in ('record_kind', 'audience');
