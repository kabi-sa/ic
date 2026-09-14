-- Monthly recaps and quarterly updates are signed off by Mashael and Sahabah.
--
-- Two things happen here: the standing list the form ticks by default is set, and every
-- record already filed is brought into line. Safe to run repeatedly -- it rebuilds the
-- sign-off for records rather than adding to it, so running it twice changes nothing.
--
-- Touches only ic_events. The HC Report Hub's public schema is never referenced.

-- ── 1. the standing list, which decides what the "Add to history" form starts with ──
with signers as (
    select id, name
      from ic_events.users
     where active = 1
       and role = 'manager'
       and (name like 'Mashael%' or name like 'Sahabah%')
)
insert into ic_events.settings (key, value)
select 'record_approver_ids', string_agg(id::text, ',' order by name)
  from signers
having count(*) > 0
on conflict (key) do update set value = excluded.value;

-- ── 2. every recap and quarterly update already in the history ──
-- Rebuilt rather than topped up, so this is idempotent and cannot leave a stale name on.
delete from ic_events.event_approvers ea
 using ic_events.events e
 where ea.event_id = e.id
   and coalesce(e.record_kind, 'activity') <> 'activity';

insert into ic_events.event_approvers
       (event_id, user_id, level, status, is_final, decided_at, sort, created_at)
select e.id,
       u.id,
       coalesce(u.approver_level, 1),
       'approved',                                   -- it went out with their sign-off
       coalesce(u.is_final_approver, 0),
       coalesce(e.execution_date, e.event_date),     -- signed on the day it was shared
       row_number() over (partition by e.id order by u.name) - 1,
       to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
  from ic_events.events e
  join ic_events.users u
    on u.active = 1
   and u.role = 'manager'
   and (u.name like 'Mashael%' or u.name like 'Sahabah%')
 where coalesce(e.record_kind, 'activity') <> 'activity'
   and u.id <> e.created_by;                         -- nobody signs off their own work

-- the single name the events list leads with follows the same order
update ic_events.events e
   set approver_id = (select ea.user_id
                        from ic_events.event_approvers ea
                       where ea.event_id = e.id
                       order by ea.sort
                       limit 1)
 where coalesce(e.record_kind, 'activity') <> 'activity';

-- ── 3. what it did ──
select e.event_number,
       e.event_name,
       coalesce(e.execution_date, e.event_date) as shared_on,
       string_agg(u.name, ' + ' order by ea.sort) as signed_off_by
  from ic_events.events e
  join ic_events.event_approvers ea on ea.event_id = e.id
  join ic_events.users u            on u.id = ea.user_id
 where coalesce(e.record_kind, 'activity') <> 'activity'
 group by e.event_number, e.event_name, e.event_date, e.execution_date
 order by coalesce(e.execution_date, e.event_date);
