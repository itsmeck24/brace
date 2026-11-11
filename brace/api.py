# One endpoint: when should I be at the stop?
#
#   BRACE_MODEL=models/brace.joblib BRACE_DB=data/brace.duckdb uv run uvicorn brace.api:app
#   curl 'localhost:8000/plan?route=31&stop_id=8591...&sched_dep=2026-11-09T17:12&minutes_before=10'
#
# For a recorded day it replays what was known at decision time (the same rows the
# evaluation used). Otherwise it plans from the timetable and history only.

import os
from datetime import datetime, timedelta
from functools import lru_cache

import duckdb
import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query

from brace.methods import MIN_GROUP, predict_quantile_model

app = FastAPI(title="BRACE", description="When to be at the bus stop, with a stated catch rate.")


@lru_cache
def model():
    return joblib.load(os.environ.get("BRACE_MODEL", "models/brace.joblib"))


def db():
    return duckdb.connect(os.environ.get("BRACE_DB", "data/brace.duckdb"), read_only=True)


def history_count(fitted, route, stop_id, hour):
    # how many past departures back the finest history level for this stop
    for keys, stats in fitted["history"][0]:
        key = tuple({"route": route, "stop_id": stop_id, "hour": hour}[k] for k in keys)
        key = key[0] if len(key) == 1 else key
        if key in stats.index:
            return int(stats.loc[key, "n"]), keys
    return 0, None


def replay_row(con, agency, route, stop_id, sched_dep, horizon):
    df = con.execute("""
        select * from training_rows
        where agency = ? and route = ? and stop_id = ? and sched_dep = ? and horizon_min = ?
    """, [agency, route, stop_id, sched_dep, horizon]).df()
    return df.iloc[:1] if len(df) else None


def plan_row(con, agency, route, stop_id, sched_dep, horizon):
    # timetable-only row: nothing is known about the bus yet
    ev = con.execute("""
        select * from stop_events_clean
        where agency = ? and route = ? and stop_id = ? and sched_dep = ?
        limit 1
    """, [agency, route, stop_id, sched_dep]).df()
    if not len(ev):
        return None
    e = ev.iloc[0]
    return pd.DataFrame([{
        "route": route, "stop_id": stop_id, "horizon_min": horizon,
        "sched_hour": sched_dep.hour + sched_dep.minute / 60, "weekday": sched_dep.isoweekday(),
        "trip_progress": e["stop_seq"] / e["trip_last_seq"],
        "min_since_trip_start": (sched_dep - e["trip_sched_start"]).total_seconds() / 60,
        "trip_should_have_started": sched_dep - timedelta(minutes=horizon) >= e["trip_sched_start"],
        "stops_to_go": np.nan, "last_seen_delay_s": np.nan, "last_seen_age_s": np.nan,
        "prev_bus_delay_s": np.nan, "prev_bus_age_s": np.nan,
    }])


@app.get("/health")
def health():
    m = model()
    return {"ok": True, "trained_on": m["train_days"], "offsets_from": m["val_days"]}


@app.get("/plan")
def plan(
    route: str,
    stop_id: str,
    sched_dep: datetime,
    minutes_before: int = Query(10, description="when you decide, in minutes before the scheduled departure"),
    agency: str = "ch:VBZ",
):
    m = model()
    if minutes_before not in m["offsets"]:
        raise HTTPException(400, f"minutes_before must be one of {sorted(m['offsets'])}")
    con = db()
    row = replay_row(con, agency, route, stop_id, sched_dep, minutes_before)
    mode = "replay"
    if row is None:
        row, mode = plan_row(con, agency, route, stop_id, sched_dep, minutes_before), "timetable"
    if row is None:
        raise HTTPException(404, "no such scheduled departure for this route and stop")

    n, level = history_count(m["fitted"], route, stop_id, sched_dep.hour)
    knows_bus = mode == "replay" and not (pd.isna(row["last_seen_delay_s"].iloc[0]) and pd.isna(row["prev_bus_delay_s"].iloc[0]))
    if n < MIN_GROUP and not knows_bus:
        return {"status": "insufficient data", "history_departures": n}

    q10, q50, q90 = (float(x[0]) for x in predict_quantile_model(m["fitted"], row))
    be_there = sched_dep + timedelta(seconds=q10 + m["offsets"][minutes_before])
    out = {
        "status": "ok",
        "mode": mode,
        "scheduled_departure": sched_dep.isoformat(),
        "be_there_by": be_there.replace(microsecond=0).isoformat(),
        "aimed_catch_rate": 0.9,
        "likely_departure": (sched_dep + timedelta(seconds=q50)).replace(microsecond=0).isoformat(),
        "range80": [(sched_dep + timedelta(seconds=s)).replace(microsecond=0).isoformat() for s in (q10, q90)],
        "history_level": level,
    }
    if mode == "replay":
        actual = row["actual_dep"].iloc[0].to_pydatetime()
        out["what_happened"] = {"actual_departure": actual.isoformat(), "caught": actual >= be_there}
    return out
