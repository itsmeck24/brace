# Compares arrival and departure times written down at a stop with what the
# feed said for the same buses: the label windows from check_feed.py, and the
# predictions that were on screen before the bus came.
#
# usage: uv run python hand_check.py --obs data/handcheck/observed.csv \
#            --labels results/handcheck/labels.csv --raw data/handcheck/raw --stop 5324
#
# observed.csv columns: date (YYYY-MM-DD), route, bus_number, arrive, depart (HH:MM:SS, phone
# clock), stopped (y/n). If the bus didn't stop, leave depart empty.

import argparse
import csv
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from check_feed import read_trip_updates

PHX = timezone(timedelta(hours=-7))
MATCH_WINDOW_S = 300  # a hand time and a label further apart than this aren't the same bus
HORIZONS = (("1-5 min", 1, 5), ("5-10 min", 5, 10), ("10-20 min", 10, 20))


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def clock(ts):
    return datetime.fromtimestamp(ts, PHX).strftime("%H:%M:%S")


def match(observed, labels):
    # greedy, closest pairs first, each label used once
    pairs = []
    for o in observed:
        for lab in labels:
            if lab["route"] != o["route"]:
                continue
            mid = (lab["lo"] + lab["hi"]) / 2
            gap = abs(o["t"] - mid)
            if gap <= MATCH_WINDOW_S:
                pairs.append((gap, id(o), id(lab), o, lab))
    pairs.sort(key=lambda p: p[0])
    used_o, used_l, out = set(), set(), []
    for _, oi, li, o, lab in pairs:
        if oi in used_o or li in used_l:
            continue
        used_o.add(oi)
        used_l.add(li)
        inside = lab["lo"] <= o["t"] <= lab["hi"]
        # how far outside the bracket the hand time is (0 if inside), negative = before lo
        off = 0 if inside else (o["t"] - lab["lo"] if o["t"] < lab["lo"] else o["t"] - lab["hi"])
        out.append({
            "route": o["route"],
            "trip": lab["trip"],
            "seq": int(lab["seq"]),
            "arrive": o["t"],
            "depart": o["t_dep"],
            "bus_number": o["bus_number"],
            "label_vehicle": lab["vehicle"],
            "vehicle_matches": o["bus_number"] == lab["vehicle"] if o["bus_number"] else None,
            "kind": lab["kind"],
            "lo": lab["lo"],
            "hi": lab["hi"],
            "inside": inside,
            "outside_by_s": round(off),
        })
    unmatched = [o for o in observed if id(o) not in used_o]
    return out, unmatched


def vs_predictions(pairs, preds):
    # Is the feed's predicted time closer to when the bus pulled in or when it left?
    # Positive = prediction was later than the real event (bus came early).
    out = {}
    for name, lo, hi in HORIZONS:
        to_arr, to_dep, buses = [], [], set()
        for p in pairs:
            for fetched, t, _ in preds.get((p["trip"], p["seq"]), []):
                if lo <= (p["arrive"] - fetched) / 60 < hi:
                    to_arr.append(t - p["arrive"])
                    to_dep.append(t - p["depart"])
                    buses.add((p["trip"], p["seq"]))
        if not to_arr:
            out[name] = {"n": 0}
            continue
        out[name] = {
            "n": len(to_arr),
            "buses": len(buses),
            "median_pred_minus_arrive_s": round(statistics.median(to_arr)),
            "median_pred_minus_depart_s": round(statistics.median(to_dep)),
            "share_arrived_over_60s_early": round(sum(x > 60 for x in to_arr) / len(to_arr), 3),
            "share_departed_over_60s_early": round(sum(x > 60 for x in to_dep) / len(to_dep), 3),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--obs", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--stop", required=True)
    ap.add_argument("--gtfs", default="data/static/gtfs")
    args = ap.parse_args()

    route_of = {r["route_id"]: r["route_short_name"] for r in read_csv(f"{args.gtfs}/routes.txt")}
    trip_route = {t["trip_id"]: route_of[t["route_id"]] for t in read_csv(f"{args.gtfs}/trips.txt")}

    observed = []
    for r in read_csv(args.obs):
        def ts(hms):
            return datetime.strptime(f"{r['date']} {hms.strip()}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=PHX).timestamp()
        arrive = ts(r["arrive"])
        depart = ts(r["depart"]) if r.get("depart", "").strip() else arrive
        if depart < arrive:
            raise SystemExit(f"depart before arrive on line: {r}")
        observed.append({"t": arrive, "t_dep": depart, "route": r["route"].strip().upper(),
                         "bus_number": r.get("bus_number", "").strip(), "stopped": r.get("stopped", "y")})

    labels = []
    for lab in read_csv(args.labels):
        if lab["stop"] != args.stop:
            continue
        labels.append({**lab, "lo": int(lab["lo"]), "hi": int(lab["hi"]),
                       "route": trip_route.get(lab["trip"], "?").upper()})

    pairs, unmatched = match(observed, labels)
    preds = read_trip_updates(Path(args.raw))[0]
    dwell = [p["depart"] - p["arrive"] for p in pairs]
    res = {
        "observed": len(observed),
        "labels_at_stop": len(labels),
        "matched": len(pairs),
        "inside_bracket": sum(p["inside"] for p in pairs),
        "max_outside_s": max((abs(p["outside_by_s"]) for p in pairs), default=None),
        "vehicle_number_checked": sum(p["vehicle_matches"] is not None for p in pairs),
        "vehicle_number_matched": sum(bool(p["vehicle_matches"]) for p in pairs),
        "dwell_s_median": round(statistics.median(dwell)) if dwell else None,
        "predictions_vs_hand_times": vs_predictions(pairs, preds),
        "pairs": [{**p, "arrive": clock(p["arrive"]), "depart": clock(p["depart"]),
                   "lo": clock(p["lo"]), "hi": clock(p["hi"])} for p in pairs],
        "unmatched_hand_times": [clock(o["t"]) + " " + o["route"] for o in unmatched],
    }
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
