-- An approver can approve more than one option, so a decision is a set.
--
-- Safe to run repeatedly. Touches only ic_events. Nothing already decided moves:
-- a single approved option is just a set of one.

alter table ic_events.event_approvers
    add column if not exists selected_option_ids text;

-- A decision already recorded approved exactly one option. Writing it as a list of one
-- means the trail reads the same way for old and new decisions alike.
update ic_events.event_approvers
   set selected_option_ids = selected_option_id::text
 where selected_option_id is not null
   and (selected_option_ids is null or selected_option_ids = '');

select count(*) filter (where selected_option_ids is not null) as decisions_with_a_set,
       count(*) filter (where selected_option_id is not null)  as decisions_with_an_option
  from ic_events.event_approvers;
