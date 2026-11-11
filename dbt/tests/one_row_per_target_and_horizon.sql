select agency, service_date, trip_id, stop_seq, horizon_min, count(*) as n
from {{ ref('training_rows') }}
group by all
having count(*) > 1
