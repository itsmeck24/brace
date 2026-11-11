# Per-day data quality profile of an events folder.
#
# usage: uv run python -m brace.profile --events data/events/ch_vbz --out results/data_profile.json

import argparse
import json
from pathlib import Path

import duckdb

from brace.runinfo import run_info

PER_DAY = """
select
  service_date::varchar as day,
  count(*) as rows,
  count(distinct trip_id) as trips,
  count(distinct route) as routes,
  count(distinct stop_id) as stops,
  round(avg(cancelled::int), 4) as cancelled_share,
  -- departures we can score: measured, has a time, not cancelled, and a timetable time to compare to
  count(*) filter (where dep_status = 'REAL' and actual_dep is not null and not cancelled and sched_dep is not null) as measured_dep,
  round(count(*) filter (where dep_status = 'REAL' and actual_dep is not null and not cancelled)
        / nullif(count(*) filter (where sched_dep is not null and not cancelled), 0), 4) as measured_dep_share,
  round(count(*) filter (where arr_status = 'REAL' and actual_arr is not null and not cancelled)
        / nullif(count(*) filter (where sched_arr is not null and not cancelled), 0), 4) as measured_arr_share
from read_parquet(?)
group by 1 order by 1
"""

DUPES = """
select service_date::varchar as day, count(*) as stops_visited_twice_in_trip from (
  select service_date, trip_id, stop_id, count(*) c from read_parquet(?)
  group by all having c > 1)
group by 1 order by 1
"""

DELAYS = """
select service_date::varchar as day,
  quantile_cont(epoch(actual_dep - sched_dep), [0.01, 0.1, 0.5, 0.9, 0.99]) as dep_delay_s_q01_q10_q50_q90_q99,
  round(avg((actual_dep < sched_dep - interval 30 second)::int), 4) as share_left_30s_early
from read_parquet(?)
where dep_status = 'REAL' and actual_dep is not null and not cancelled and sched_dep is not null
group by 1 order by 1
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    files = str(Path(args.events) / "*.parquet")
    con = duckdb.connect()

    def rows(sql):
        rel = con.execute(sql, [files])
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r)) for r in rel.fetchall()]

    days = {r["day"]: r for r in rows(PER_DAY)}
    for r in rows(DUPES):
        days[r["day"]]["stops_visited_twice_in_trip"] = r["stops_visited_twice_in_trip"]
    for r in rows(DELAYS):
        days[r["day"]].update({k: v for k, v in r.items() if k != "day"})
    for d in days.values():
        d.setdefault("stops_visited_twice_in_trip", 0)

    out = {
        "run": run_info(),
        "events": args.events,
        "source": "opentransportdata.swiss Ist-Daten v2, filtered to one operator's buses",
        "days": list(days.values()),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))
    for d in days.values():
        print(d["day"], d["rows"], d["measured_dep_share"], d["stops_visited_twice_in_trip"], d.get("dep_delay_s_q01_q10_q50_q90_q99"))


if __name__ == "__main__":
    main()
