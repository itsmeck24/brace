-- A row only exists if the bus had not yet left when the rider decided.
select *
from {{ ref('training_rows') }}
where actual_dep <= decision_time
