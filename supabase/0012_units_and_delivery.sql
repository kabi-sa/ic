-- A quotation's unit price and quantity, and delivery per option.
--
-- Safe to run repeatedly. Touches only ic_events. The back-fills are written so that
-- every total already recorded stays exactly where it is.

alter table ic_events.vendors       add column if not exists unit_price real;
alter table ic_events.vendors       add column if not exists quantity   real not null default 1;
alter table ic_events.event_options add column if not exists delivery_cost real not null default 0;

-- A quotation recorded before there were units is one of something at its own price.
update ic_events.vendors
   set unit_price = quotation_amount, quantity = 1
 where unit_price is null;

-- Miscellaneous cost sat on the event and was added to every option identically, so
-- copying it into each option's delivery leaves every option total unchanged.
update ic_events.event_options o
   set delivery_cost = coalesce(e.miscellaneous_cost, 0)
  from ic_events.events e
 where e.id = o.event_id
   and coalesce(o.delivery_cost, 0) = 0
   and coalesce(e.miscellaneous_cost, 0) > 0;

select (select count(*) from ic_events.vendors where unit_price is not null) as vendors_with_units,
       (select count(*) from ic_events.vendors)                             as vendors_total,
       (select count(*) from ic_events.event_options where delivery_cost > 0) as options_with_delivery;
