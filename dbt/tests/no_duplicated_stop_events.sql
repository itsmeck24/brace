-- the same bus at the same stop with the same times twice means a trip was listed
-- twice in the source; the adapter drops those, this catches any that get through
select agency, service_date, trip_id, stop_id, sched_arr, sched_dep, actual_arr, actual_dep, count(*) as n
from {{ ref('stop_events_clean') }}
group by all
having count(*) > 1
