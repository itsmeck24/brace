-- The previous bus at this stop must have left at or before decision time.
select *
from {{ ref('training_rows') }}
where prev_bus_age_s < 0
