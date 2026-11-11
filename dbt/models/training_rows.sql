-- One row per (measured departure, horizon). The rider decides `horizon_min`
-- minutes before the scheduled departure. Every feature comes from an event whose
-- actual departure was at or before that moment.

with dep as (
    select * from {{ ref('stop_events_clean') }}
    where dep_measured
),

horizons as (
    select unnest({{ var('horizons_min') }}) as horizon_min
),

target as (
    select d.*, h.horizon_min,
           d.sched_dep - to_minutes(h.horizon_min) as decision_time
    from dep d cross join horizons h
),

-- If the bus already left this stop before the rider decided, there's nothing to plan.
live as (
    select * from target where actual_dep > decision_time
),

-- Furthest earlier stop this bus had left by decision time. Both conditions are
-- needed: about 0.6% of departures are recorded earlier than the stop before them,
-- so time order alone can point at a later stop.
own_trip as (
    select l.agency, l.service_date, l.trip_id, l.stop_seq, l.horizon_min,
           max(e.stop_seq) as last_seen_seq,
           arg_max(e.dep_delay_s, e.stop_seq) as last_seen_delay_s,
           arg_max(epoch(l.decision_time - e.actual_dep), e.stop_seq) as last_seen_age_s
    from live l
    join dep e
      on l.agency = e.agency and l.service_date = e.service_date and l.trip_id = e.trip_id
     and e.stop_seq < l.stop_seq
     and e.actual_dep <= l.decision_time
    group by all
),

-- how late the previous bus of the same line was at this same stop
same_stop as (
    select l.agency, l.service_date, l.trip_id, l.stop_seq, l.horizon_min,
           e.dep_delay_s as prev_bus_delay_s,
           epoch(l.decision_time - e.actual_dep) as prev_bus_age_s
    from live l
    asof left join dep e
      on l.agency = e.agency and l.service_date = e.service_date
     and l.route = e.route and l.stop_id = e.stop_id
     and l.decision_time >= e.actual_dep
)

select
    l.agency,
    l.service_date,
    l.trip_id,
    l.route,
    l.stop_id,
    l.stop_seq,
    l.horizon_min,
    l.decision_time,
    l.sched_dep,
    l.actual_dep,
    l.dep_delay_s as target_delay_s,
    -- calendar and position
    hour(l.sched_dep) + minute(l.sched_dep) / 60.0 as sched_hour,
    isodow(l.sched_dep) as weekday,
    l.stop_seq / l.trip_last_seq::double as trip_progress,
    epoch(l.sched_dep - l.trip_sched_start) / 60.0 as min_since_trip_start,
    l.decision_time >= l.trip_sched_start as trip_should_have_started,
    -- what was known at decision time
    o.last_seen_seq,
    l.stop_seq - o.last_seen_seq as stops_to_go,
    o.last_seen_delay_s,
    o.last_seen_age_s,
    s.prev_bus_delay_s,
    s.prev_bus_age_s
from live l
left join own_trip o using (agency, service_date, trip_id, stop_seq, horizon_min)
left join same_stop s using (agency, service_date, trip_id, stop_seq, horizon_min)
