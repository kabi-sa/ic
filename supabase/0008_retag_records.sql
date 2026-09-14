-- Put the hosted history's recaps and quarterly updates right.
--
-- Three things were wrong, all downstream of one fact: record_kind did not exist on the
-- hosted store when the history was imported, so every imported row took the column's
-- default of 'activity' when 0005 finally added it.
--
--   1. Thirteen monthly recaps and three quarterly updates are filed as ordinary
--      activities -- so they show a budget, ask for vendors, and sit under the wrong
--      filter instead of under Monthly recaps / Quarterly updates.
--   2. They have no audience.
--   3. Any record whose date is not the last Thursday of its month is misplaced on the
--      calendar. The Aug 2026 recap sits on Monday the 31st, because the "Add to
--      history" form used to send a hidden date field that overrode the period.
--
-- Safe to run repeatedly, and safe to run after 0007. Each step is guarded, and the
-- sign-off is rebuilt rather than added to.
--
-- Touches only ic_events. The HC Report Hub's public schema is never referenced.

-- ── 1. classify them by the name the import gave them ──
-- The prefixes are exact: locally 13 of 13 "Monthly Recap%" and 3 of 3
-- "Quarterly Update%" are records, and no activity carries either prefix.
update ic_events.events
   set record_kind = 'monthly_recap'
 where event_name like 'Monthly Recap%'
   and coalesce(record_kind, 'activity') = 'activity';

update ic_events.events
   set record_kind = 'quarterly_update'
 where event_name like 'Quarterly Update%'
   and coalesce(record_kind, 'activity') = 'activity';

-- ── 2. a record goes out to an audience, where an activity has a location ──
update ic_events.events
   set audience = 'KABi MENA'
 where coalesce(record_kind, 'activity') <> 'activity'
   and (audience is null or audience = '');

-- ── 3. file each one on the last Thursday of its month ──
-- Step back from the month's final day to the nearest Thursday: isodow runs Mon=1..Sun=7
-- and Thursday is 4, so (isodow - 4 + 7) % 7 is how many days to go back. This is a
-- no-op for a record already on the right day -- verified against all sixteen whose
-- dates are known good.
with target as (
    select e.id,
           (last_day.d - (((extract(isodow from last_day.d)::int - 4 + 7) % 7)
                          * interval '1 day'))::date as thursday
      from ic_events.events e
      cross join lateral (
          select (date_trunc('month', e.event_date::date)
                  + interval '1 month' - interval '1 day')::date as d
      ) as last_day
     where coalesce(e.record_kind, 'activity') <> 'activity'
       and e.event_date is not null
)
update ic_events.events e
   set event_date     = t.thursday::text,
       execution_date = t.thursday::text        -- the sharing date cannot disagree
  from target t
 where e.id = t.id
   and (e.event_date <> t.thursday::text
        or coalesce(e.execution_date, '') <> t.thursday::text);

-- ── 4. the sign-off: Mashael and Sahabah, on every record ──
delete from ic_events.event_approvers ea
 using ic_events.events e
 where ea.event_id = e.id
   and coalesce(e.record_kind, 'activity') <> 'activity';

insert into ic_events.event_approvers
       (event_id, user_id, level, status, is_final, decided_at, sort, created_at)
select e.id,
       u.id,
       coalesce(u.approver_level, 1),
       'approved',
       coalesce(u.is_final_approver, 0),
       coalesce(e.execution_date, e.event_date),
       row_number() over (partition by e.id order by u.name) - 1,
       to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
  from ic_events.events e
  join ic_events.users u
    on u.active = 1
   and u.role = 'manager'
   and (u.name like 'Mashael%' or u.name like 'Sahabah%')
 where coalesce(e.record_kind, 'activity') <> 'activity'
   and u.id <> e.created_by;

update ic_events.events e
   set approver_id = (select ea.user_id
                        from ic_events.event_approvers ea
                       where ea.event_id = e.id
                       order by ea.sort
                       limit 1)
 where coalesce(e.record_kind, 'activity') <> 'activity';

-- ── 5. what the history looks like now ──
select e.event_number,
       e.event_name,
       e.record_kind,
       e.event_date,
       to_char(e.event_date::date, 'Dy')                  as falls_on,
       e.audience,
       string_agg(u.name, ' + ' order by ea.sort)          as signed_off_by
  from ic_events.events e
  left join ic_events.event_approvers ea on ea.event_id = e.id
  left join ic_events.users u            on u.id = ea.user_id
 where coalesce(e.record_kind, 'activity') <> 'activity'
 group by e.event_number, e.event_name, e.record_kind, e.event_date, e.audience
 order by e.event_date;
