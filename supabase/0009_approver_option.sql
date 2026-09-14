-- Each approver's own package choice, so a later approver can see what was chosen
-- before them and agree or overturn it.
--
-- Safe to run repeatedly. Touches only ic_events.

alter table ic_events.event_approvers
    add column if not exists selected_option_id integer;

-- Back-fill: where an event has a standing choice and an approver has already approved,
-- that is the package they signed off on. It is the best available answer for decisions
-- taken before the column existed, and it is only applied where nothing is recorded yet.
update ic_events.event_approvers ea
   set selected_option_id = e.selected_option_id
  from ic_events.events e
 where ea.event_id = e.id
   and ea.status = 'approved'
   and ea.selected_option_id is null
   and e.selected_option_id is not null;

select count(*) filter (where selected_option_id is not null) as approvers_with_a_choice,
       count(*)                                               as approvers_total
  from ic_events.event_approvers
 where status = 'approved';
