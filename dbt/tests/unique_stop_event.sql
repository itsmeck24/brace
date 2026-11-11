-- (agency, service_date, trip_id, stop_seq) must identify one row
select agency, service_date, trip_id, stop_seq, count(*) as n
from {{ ref('stop_events_clean') }}
group by all
having count(*) > 1
