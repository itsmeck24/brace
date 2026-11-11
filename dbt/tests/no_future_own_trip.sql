-- The bus's last-seen stop must be before the target stop, and seen at or before decision time.
select *
from {{ ref('training_rows') }}
where last_seen_seq is not null
  and (last_seen_seq >= stop_seq or last_seen_age_s < 0)
