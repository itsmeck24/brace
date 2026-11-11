# Reads what collect.py saved: feed freshness, field coverage, arrival labels.
#
# usage: uv run python check_feed.py --raw data/raw --gtfs data/static/gtfs --out results/feed_check

import argparse
import csv
import gzip
import json
import math
import platform
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import google.protobuf
from google.transit import gtfs_realtime_pb2 as pb

PHX = timezone(timedelta(hours=-7))  # Arizona has no DST
NEAR_M = 50  # "at the stop" radius for the GPS check
MAX_JUMP = 3  # most stops a bus can plausibly pass between two reports
STATUS = pb.VehiclePosition.VehicleStopStatus.Name


def load(path):
    m = pb.FeedMessage()
    m.ParseFromString(gzip.decompress(path.read_bytes()))
    return m


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def summary(xs):
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(statistics.mean(xs), 1),
        "p50": pct(xs, 0.5),
        "p90": pct(xs, 0.9),
        "max": max(xs),
    }


def meters(lat1, lon1, lat2, lon2):
    # equirectangular is plenty at 50 m scale
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000 * math.hypot(x, y)


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def check_fetches(raw):
    recs = [json.loads(line) for line in open(raw / "fetch_log.jsonl")]
    out = {}
    for feed in ("trip_updates", "vehicles"):
        rs = [r for r in recs if r["feed"] == feed]
        ok = [r for r in rs if r.get("status") == 200 and "header_ts" in r]
        ages = [round(r["fetched_at"] - r["header_ts"]) for r in ok]
        # same header timestamp on back to back polls = feed did not refresh
        repeats = sum(1 for a, b in zip(ok, ok[1:]) if a["header_ts"] == b["header_ts"])
        hdr_steps = [b["header_ts"] - a["header_ts"] for a, b in zip(ok, ok[1:]) if b["header_ts"] != a["header_ts"]]
        gaps = [round(b["fetched_at"] - a["fetched_at"]) for a, b in zip(rs, rs[1:])]
        out[feed] = {
            "polls": len(rs),
            "ok": len(ok),
            "failed": [{k: r.get(k) for k in ("fetched_at", "status", "error")} for r in rs if r not in ok],
            "header_age_s": summary(ages),
            "repeated_header": repeats,
            "header_step_s": summary(hdr_steps),
            "poll_gap_s": summary(gaps),
            "entities": summary([r["entities"] for r in ok]),
            "first_fetch": min(r["fetched_at"] for r in rs),
            "last_fetch": max(r["fetched_at"] for r in rs),
        }
    return out


def read_vehicles(raw):
    # dedupe on (vehicle, timestamp), a report repeats across polls until the bus sends a new one
    seen = {}
    total = 0
    fields = Counter()
    status = Counter()
    for p in sorted((raw / "vehicles").glob("*.pb.gz")):
        fetched = int(p.name.split(".")[0])
        for e in load(p).entity:
            v = e.vehicle
            total += 1
            fields["trip_id"] += bool(v.trip.trip_id)
            fields["stop_id"] += bool(v.stop_id)
            fields["current_stop_sequence"] += v.HasField("current_stop_sequence")
            fields["current_status"] += v.HasField("current_status")
            fields["timestamp"] += bool(v.timestamp)
            fields["position"] += v.HasField("position")
            st = STATUS(v.current_status) if v.HasField("current_status") else None
            status[st or "missing"] += 1
            key = (v.vehicle.id, v.timestamp)
            if key in seen:
                continue
            seen[key] = {
                "vehicle": v.vehicle.id,
                "ts": v.timestamp,
                "fetched": fetched,
                "trip": v.trip.trip_id,
                "route": v.trip.route_id,
                "seq": v.current_stop_sequence if v.HasField("current_stop_sequence") else None,
                "stop": v.stop_id or None,
                "status": st,
                "lat": v.position.latitude,
                "lon": v.position.longitude,
            }
    rows = list(seen.values())
    return rows, total, {k: round(c / total, 3) for k, c in fields.items()}, dict(status)


def read_trip_updates(raw):
    # predictions[(trip, seq)] = list of (fetched, predicted arrival, stop_id)
    preds = defaultdict(list)
    total = 0
    stus = 0
    fields = Counter()
    first_seq = {}
    last_seq = {}
    for p in sorted((raw / "trip_updates").glob("*.pb.gz")):
        fetched = int(p.name.split(".")[0])
        for e in load(p).entity:
            t = e.trip_update
            total += 1
            fields["trip_id"] += bool(t.trip.trip_id)
            fields["vehicle_id"] += bool(t.vehicle.id)
            fields["timestamp"] += bool(t.timestamp)
            seqs = []
            for s in t.stop_time_update:
                stus += 1
                fields["stu_arrival_time"] += bool(s.arrival.time)
                fields["stu_arrival_delay"] += s.arrival.HasField("delay")
                fields["stu_stop_id"] += bool(s.stop_id)
                fields["stu_uncertainty"] += s.arrival.HasField("uncertainty")
                if s.arrival.time:
                    preds[(t.trip.trip_id, s.stop_sequence)].append((fetched, s.arrival.time, s.stop_id))
                seqs.append(s.stop_sequence)
            if seqs:
                first_seq.setdefault(t.trip.trip_id, min(seqs))
                last_seq[t.trip.trip_id] = min(seqs)
    tu_fields = {k: round(c / total, 3) for k, c in fields.items() if not k.startswith("stu_")}
    stu_fields = {k: round(c / stus, 3) for k, c in fields.items() if k.startswith("stu_")}
    dropped = sum(1 for k in last_seq if last_seq[k] > first_seq[k])
    return preds, total, stus, tu_fields, stu_fields, {"trips": len(last_seq), "first_listed_stop_moved": dropped}


def build_labels(rows, stops, trip_ids):
    # Bracket when each bus reached each stop using consecutive reports.
    # observed = it said STOPPED_AT, inferred_seq = the stop counter moved past the stop.
    by_trip = defaultdict(list)
    for r in rows:
        if r["trip"] and r["seq"] is not None:
            by_trip[(r["trip"], r["vehicle"])].append(r)

    labels = []
    skipped_jumps = 0
    skipped_frozen = 0
    for (trip, veh), rs in by_trip.items():
        rs.sort(key=lambda r: r["ts"])
        done = set()
        for prev, cur in zip(rs, rs[1:]):
            # counter sometimes leaps (1 -> 110 in 30 s), skip those
            if cur["seq"] - prev["seq"] > MAX_JUMP:
                skipped_jumps += 1
                continue
            # counter moved but the GPS didn't budge, so the position is stale
            if cur["seq"] > prev["seq"] and (prev["lat"], prev["lon"]) == (cur["lat"], cur["lon"]):
                skipped_frozen += 1
                continue
            if cur["status"] == "STOPPED_AT" and cur["seq"] not in done:
                was_there = prev["seq"] == cur["seq"] and prev["status"] == "STOPPED_AT"
                if not was_there:
                    labels.append(make_label(trip, veh, cur["seq"], cur["stop"], prev, cur, "observed", stops))
                    done.add(cur["seq"])
            hi = cur["seq"] if cur["status"] == "STOPPED_AT" else cur["seq"] - 1
            lo = prev["seq"] if prev["status"] != "STOPPED_AT" else prev["seq"] + 1
            for s in range(lo, hi + 1):
                if s in done or s == cur["seq"] and cur["status"] == "STOPPED_AT":
                    continue
                # only label if the bus was clearly before the stop at prev
                if prev["seq"] > s or (prev["seq"] == s and prev["status"] == "STOPPED_AT"):
                    continue
                labels.append(make_label(trip, veh, s, None, prev, cur, "inferred_seq", stops))
                done.add(s)
    for lab in labels:
        lab["trip_in_static"] = lab["trip"] in trip_ids
    return labels, {"jump": skipped_jumps, "frozen_position": skipped_frozen}


def make_label(trip, veh, seq, stop_id, prev, cur, kind, stops):
    lab = {
        "trip": trip,
        "vehicle": veh,
        "seq": seq,
        "stop": stop_id,
        "kind": kind,
        "lo": prev["ts"],
        "hi": cur["ts"],
        "width_s": cur["ts"] - prev["ts"],
        "_lo_pos": (prev["lat"], prev["lon"]),
        "_hi_pos": (cur["lat"], cur["lon"]),
    }
    return lab


def read_stop_times(path, trips):
    # (trip, seq) -> stop_id, only for trips we saw. The file is ~40 MB so skip the rest.
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["trip_id"] in trips:
                out[(r["trip_id"], int(r["stop_sequence"]))] = r["stop_id"]
    return out


def attach_stop_ids(labels, sched, stops):
    # inferred labels don't know their stop id; the static schedule does
    for lab in labels:
        if lab["stop"] is None:
            lab["stop"] = sched.get((lab["trip"], lab["seq"]))
        elif sched.get((lab["trip"], lab["seq"])) not in (None, lab["stop"]):
            lab["stop_mismatch"] = True
        pos_lo, pos_hi = lab.pop("_lo_pos"), lab.pop("_hi_pos")
        if lab["stop"] in stops:
            s = stops[lab["stop"]]
            lab["dist_at_lo_m"] = round(meters(*s, *pos_lo))
            lab["dist_at_hi_m"] = round(meters(*s, *pos_hi))
            # if the bus really passed the stop, it should be near the line between the two reports
            lab["seg_dist_m"] = round(seg_dist(s, pos_lo, pos_hi))


def seg_dist(p, a, b):
    # point to segment distance on a local flat projection, meters
    lat0 = math.radians(p[0])
    def xy(q):
        return (math.radians(q[1] - p[1]) * math.cos(lat0) * 6371000, math.radians(q[0] - p[0]) * 6371000)
    ax, ay = xy(a)
    bx, by = xy(b)
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)


def prediction_preview(labels, preds):
    # only to compare label width with error size, not an eval of the predictions
    errs = []
    for lab in labels:
        p = preds.get((lab["trip"], lab["seq"]))
        if not p:
            continue
        mid = (lab["lo"] + lab["hi"]) / 2
        for fetched, t, _ in p:
            horizon = mid - fetched
            if horizon < 60:
                continue
            errs.append({"kind": lab["kind"], "horizon_min": horizon / 60, "err_s": t - mid, "width_s": lab["width_s"]})
    out = {}
    for name, lo, hi in (("1-5 min", 1, 5), ("5-10 min", 5, 10), ("10-20 min", 10, 20), ("20+ min", 20, 999)):
        signed = [e["err_s"] for e in errs if lo <= e["horizon_min"] < hi]
        out[name] = summary([round(abs(x)) for x in signed])
        if signed:
            # predicted minus actual > 0 means the bus came early, which is how you miss it
            out[name]["share_bus_early_over_60s"] = round(sum(x > 60 for x in signed) / len(signed), 3)
            out[name]["share_bus_late_over_60s"] = round(sum(x < -60 for x in signed) / len(signed), 3)
    return out


def env_info():
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "*.py"], capture_output=True, text=True).stdout.strip())
    except FileNotFoundError:
        commit, dirty = "", False
    return {
        "command": "uv run python " + " ".join(Path(a).name if i == 0 else a for i, a in enumerate(sys.argv)),
        "commit": (commit or "none") + (" (code changed since commit)" if dirty else ""),
        "python": platform.python_version(),
        "protobuf": google.protobuf.__version__,
        "machine": f"{platform.system()} {platform.release()} {platform.machine()}",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--gtfs", default="data/static/gtfs")
    ap.add_argument("--out", default="results/feed_check")
    args = ap.parse_args()
    raw, gtfs, out = Path(args.raw), Path(args.gtfs), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    stops = {s["stop_id"]: (float(s["stop_lat"]), float(s["stop_lon"])) for s in read_csv(gtfs / "stops.txt")}
    trip_ids = {t["trip_id"] for t in read_csv(gtfs / "trips.txt")}
    feed_info = read_csv(gtfs / "feed_info.txt")[0]

    fetches = check_fetches(raw)
    rows, v_total, v_fields, v_status = read_vehicles(raw)
    preds, tu_total, stus, tu_fields, stu_fields, dropping = read_trip_updates(raw)

    # how often does a vehicle send a new report?
    by_veh = defaultdict(list)
    for r in rows:
        by_veh[r["vehicle"]].append(r["ts"])
    report_gaps = []
    for ts in by_veh.values():
        ts.sort()
        report_gaps += [b - a for a, b in zip(ts, ts[1:])]

    # counter moved but the position is identical
    frozen = 0
    pairs = 0
    by_veh_rows = defaultdict(list)
    for r in rows:
        by_veh_rows[r["vehicle"]].append(r)
    for rs in by_veh_rows.values():
        rs.sort(key=lambda r: r["ts"])
        for a, b in zip(rs, rs[1:]):
            if a["seq"] is None or b["seq"] is None or a["trip"] != b["trip"]:
                continue
            pairs += 1
            if (a["lat"], a["lon"]) == (b["lat"], b["lon"]) and b["seq"] > a["seq"]:
                frozen += 1
    v_age = [r["fetched"] - r["ts"] for r in rows]

    labels, skipped = build_labels(rows, stops, trip_ids)
    sched = read_stop_times(gtfs / "stop_times.txt", {r["trip"] for r in rows if r["trip"]})
    attach_stop_ids(labels, sched, stops)
    mismatches = sum(1 for lab in labels if lab.get("stop_mismatch"))

    by_kind = defaultdict(list)
    for lab in labels:
        by_kind[lab["kind"]].append(lab)
    label_summary = {}
    for k, v in by_kind.items():
        sd = [x["seg_dist_m"] for x in v if "seg_dist_m" in x]
        label_summary[k] = {
            "n": len(v),
            "width_s": summary([x["width_s"] for x in v]),
            "has_stop_coords": len(sd),
            "seg_dist_m": summary(sd),
            "share_gps_consistent": round(sum(d <= NEAR_M for d in sd) / len(sd), 3) if sd else None,
        }
    obs_dist = [lab["dist_at_hi_m"] for lab in by_kind.get("observed", []) if "dist_at_hi_m" in lab]
    label_summary["rt_stop_id_differs_from_schedule"] = mismatches
    label_summary[f"brackets_skipped_jump_over_{MAX_JUMP}"] = skipped["jump"]
    label_summary["brackets_skipped_frozen_position"] = skipped["frozen_position"]
    label_summary["observed_gps_check"] = {
        "dist_to_stop_m": summary(obs_dist),
        "share_within_50m": round(sum(d <= NEAR_M for d in obs_dist) / len(obs_dist), 3) if obs_dist else None,
    }
    rt_trips = {r["trip"] for r in rows if r["trip"]}
    report = {
        "env": env_info(),
        "window_phoenix_time": [
            datetime.fromtimestamp(fetches["vehicles"]["first_fetch"], PHX).isoformat(timespec="seconds"),
            datetime.fromtimestamp(fetches["vehicles"]["last_fetch"], PHX).isoformat(timespec="seconds"),
        ],
        "static_feed_version": feed_info.get("feed_version"),
        "fetches": fetches,
        "vehicles": {
            "observations": v_total,
            "unique_reports": len(rows),
            "vehicles": len(by_veh),
            "field_share": v_fields,
            "status_counts": v_status,
            "report_gap_s": summary(report_gaps),
            "report_age_at_fetch_s": summary(v_age),
            "rt_trips_found_in_static": round(len(rt_trips & trip_ids) / len(rt_trips), 3) if rt_trips else None,
            "consecutive_report_pairs": pairs,
            "pairs_seq_moved_position_frozen": frozen,
        },
        "trip_updates": {
            "trip_updates": tu_total,
            "stop_time_updates": stus,
            "field_share": tu_fields,
            "stop_time_field_share": stu_fields,
            "passed_stops_dropped": dropping,
        },
        "labels": label_summary,
        "prediction_abs_error_preview_s": prediction_preview(labels, preds),
    }
    (out / "report.json").write_text(json.dumps(report, indent=1))
    with open(out / "labels.csv", "w", newline="") as f:
        cols = ["trip", "vehicle", "seq", "stop", "kind", "lo", "hi", "width_s", "dist_at_lo_m", "dist_at_hi_m", "seg_dist_m", "stop_mismatch", "trip_in_static"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for lab in sorted(labels, key=lambda x: (x["trip"], x["seq"])):
            w.writerow(lab)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
