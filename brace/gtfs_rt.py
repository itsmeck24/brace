# GTFS-Realtime adapter: turns what gtfs_rt_collect.py saved into the common
# stop-event table. GTFS-RT never says when a bus actually reached a stop, so the
# arrival is the middle of a time window between two vehicle reports, and the
# status says so (WINDOW_STOPPED / WINDOW_PASSED). Nothing here is marked REAL.
#
# usage: uv run python -m brace.gtfs_rt --raw data/raw --gtfs data/static/gtfs \
#            --agency us:valleymetro --tz-offset -7 --out data/events/us_valleymetro

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from brace.gtfs_rt_labels import attach_stop_ids, build_labels, read_stop_times, read_vehicles

STATUS = {"observed": "WINDOW_STOPPED", "inferred_seq": "WINDOW_PASSED"}


def gtfs_time(service_date, hms):
    # GTFS times can run past 24:00 for trips after midnight
    h, m, s = (int(x) for x in hms.split(":"))
    return datetime.combine(service_date, datetime.min.time()) + timedelta(hours=h, minutes=m, seconds=s)


def to_events(raw, gtfs, agency, tz_offset_h):
    tz = timezone(timedelta(hours=tz_offset_h))
    stops = {}
    names = {}
    with open(gtfs / "stops.txt", newline="", encoding="utf-8-sig") as f:
        for s in csv.DictReader(f):
            stops[s["stop_id"]] = (float(s["stop_lat"]), float(s["stop_lon"]))
            names[s["stop_id"]] = s["stop_name"]
    routes = {}
    with open(gtfs / "routes.txt", newline="", encoding="utf-8-sig") as f:
        route_name = {r["route_id"]: r["route_short_name"] for r in csv.DictReader(f)}
    with open(gtfs / "trips.txt", newline="", encoding="utf-8-sig") as f:
        trip_ids = set()
        for t in csv.DictReader(f):
            trip_ids.add(t["trip_id"])
            routes[t["trip_id"]] = route_name.get(t["route_id"], t["route_id"])

    rows, _, _, _ = read_vehicles(raw)
    labels, skipped = build_labels(rows, stops, trip_ids)
    sched_stop = read_stop_times(gtfs / "stop_times.txt", {r["trip"] for r in rows if r["trip"]})
    attach_stop_ids(labels, sched_stop, stops)

    # scheduled times for the labeled (trip, seq) pairs
    want = {(lab["trip"], lab["seq"]) for lab in labels}
    sched = {}
    with open(gtfs / "stop_times.txt", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k = (r["trip_id"], int(r["stop_sequence"]))
            if k in want:
                sched[k] = (r["arrival_time"], r["departure_time"])

    out = []
    for lab in labels:
        mid = datetime.fromtimestamp((lab["lo"] + lab["hi"]) / 2, tz).replace(tzinfo=None)
        day = mid.date()
        arr, dep = sched.get((lab["trip"], lab["seq"]), (None, None))
        out.append({
            "agency": agency,
            "service_date": day,
            "trip_id": lab["trip"],
            "route": routes.get(lab["trip"]),
            "stop_id": lab["stop"],
            "stop_name": names.get(lab["stop"]),
            "stop_seq": lab["seq"],
            "sched_arr": gtfs_time(day, arr) if arr else None,
            "sched_dep": gtfs_time(day, dep) if dep else None,
            "actual_arr": mid,
            "actual_dep": None,
            "arr_status": STATUS[lab["kind"]],
            "dep_status": None,
            "cancelled": False,
            "extra_trip": lab["trip"] not in trip_ids,
            "pass_through": lab["kind"] == "inferred_seq",
            "arr_window_s": lab["width_s"],
            "gps_seg_dist_m": lab.get("seg_dist_m"),
        })
    df = pd.DataFrame(out)
    # two vehicles reporting the same trip: can't tell which one is right, so drop both
    dup = df.duplicated(["service_date", "trip_id", "stop_seq"], keep=False)
    skipped["two_vehicles_same_trip_stop"] = int(dup.sum())
    return df[~dup], skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--gtfs", required=True)
    ap.add_argument("--agency", required=True)
    ap.add_argument("--tz-offset", type=float, required=True, help="hours from UTC, no DST handling")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    df, skipped = to_events(Path(args.raw), Path(args.gtfs), args.agency, args.tz_offset)
    df = df.astype({"actual_dep": "datetime64[us]", "dep_status": "string", "sched_arr": "datetime64[us]",
                    "sched_dep": "datetime64[us]", "actual_arr": "datetime64[us]"})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for day, part in df.groupby("service_date"):
        part.to_parquet(out / f"{day}.parquet", index=False)
    print(f"{len(df)} events, skipped brackets {skipped}, statuses {df['arr_status'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
