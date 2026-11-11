-- One row per bus per stop, with measured flags and delays. Cancelled stops are
-- dropped here; everything else stays so coverage can be reported.

with raw as (
    select * from read_parquet('{{ var("events_glob") }}', union_by_name = true)
),

trip as (
    select agency, service_date, trip_id,
           min(coalesce(sched_dep, sched_arr)) as trip_sched_start,
           max(stop_seq) as trip_last_seq
    from raw
    group by all
)

select
    r.agency,
    r.service_date,
    r.trip_id,
    r.route,
    r.stop_id,
    r.stop_name,
    r.stop_seq,
    t.trip_last_seq,
    t.trip_sched_start,
    r.sched_dep,
    r.actual_dep,
    r.sched_arr,
    r.actual_arr,
    r.dep_status,
    r.arr_status,
    r.dep_status = 'REAL' and r.actual_dep is not null and r.sched_dep is not null as dep_measured,
    r.arr_status = 'REAL' and r.actual_arr is not null and r.sched_arr is not null as arr_measured,
    case when r.dep_status = 'REAL' and r.actual_dep is not null and r.sched_dep is not null
         then epoch(r.actual_dep - r.sched_dep) end as dep_delay_s,
    case when r.arr_status = 'REAL' and r.actual_arr is not null and r.sched_arr is not null
         then epoch(r.actual_arr - r.sched_arr) end as arr_delay_s,
    r.extra_trip,
    r.pass_through
from raw r
join trip t using (agency, service_date, trip_id)
where not r.cancelled
