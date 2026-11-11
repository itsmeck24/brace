# Compares arrival times written down by hand at a stop against the labels
# check_feed.py built from the feed for the same period.
#
# usage: uv run python hand_check.py --obs data/handcheck/observed.csv \
#            --labels results/handcheck/labels.csv --stop 5324 --gtfs data/static/gtfs
#
# observed.csv columns: date (YYYY-MM-DD), time (HH:MM:SS, phone clock), route, bus_number, stopped (y/n)

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone

PHX = timezone(timedelta(hours=-7))
MATCH_WINDOW_S = 300  # a hand time and a label further apart than this aren't the same bus


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def match(observed, labels):
    # Greedy, closest first, each label used once. Small n, so O(n*m) is fine.
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
            "hand_time": datetime.fromtimestamp(o["t"], PHX).strftime("%H:%M:%S"),
            "bus_number": o["bus_number"],
            "label_vehicle": lab["vehicle"],
            "vehicle_matches": o["bus_number"] == lab["vehicle"] if o["bus_number"] else None,
            "kind": lab["kind"],
            "lo": datetime.fromtimestamp(lab["lo"], PHX).strftime("%H:%M:%S"),
            "hi": datetime.fromtimestamp(lab["hi"], PHX).strftime("%H:%M:%S"),
            "inside": inside,
            "outside_by_s": round(off),
        })
    unmatched = [o for o in observed if id(o) not in used_o]
    return out, unmatched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--obs", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--stop", required=True)
    ap.add_argument("--gtfs", default="data/static/gtfs")
    args = ap.parse_args()

    route_of = {r["route_id"]: r["route_short_name"] for r in read_csv(f"{args.gtfs}/routes.txt")}
    trip_route = {t["trip_id"]: route_of[t["route_id"]] for t in read_csv(f"{args.gtfs}/trips.txt")}

    observed = []
    for r in read_csv(args.obs):
        t = datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=PHX)
        observed.append({"t": t.timestamp(), "route": r["route"].strip().upper(),
                         "bus_number": r.get("bus_number", "").strip(), "stopped": r.get("stopped", "y")})

    labels = []
    for lab in read_csv(args.labels):
        if lab["stop"] != args.stop:
            continue
        labels.append({**lab, "lo": int(lab["lo"]), "hi": int(lab["hi"]),
                       "route": trip_route.get(lab["trip"], "?").upper()})

    pairs, unmatched = match(observed, labels)
    res = {
        "observed": len(observed),
        "labels_at_stop": len(labels),
        "matched": len(pairs),
        "inside_bracket": sum(p["inside"] for p in pairs),
        "vehicle_number_checked": sum(p["vehicle_matches"] is not None for p in pairs),
        "vehicle_number_matched": sum(bool(p["vehicle_matches"]) for p in pairs),
        "pairs": pairs,
        "unmatched_hand_times": [datetime.fromtimestamp(o["t"], PHX).strftime("%H:%M:%S") + " " + o["route"] for o in unmatched],
    }
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
