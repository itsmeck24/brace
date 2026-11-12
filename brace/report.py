# Builds docs/dashboard/data.js from results/ and the saved predictions, for the
# static page in docs/dashboard/index.html. Everything shown there comes from
# files written by brace.evaluate and brace.analyze.
#
#   uv run python -m brace.report --test test --forward forward_2026-11-11_2026-11-11

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from brace.analyze import KEYS, MODEL, OPERATING, offset_table, rows_for
from brace.runinfo import run_info

SHOWN = ["quantile_model_s0", "history_lookup", "carry_forward", "schedule"]
NAMES = {"quantile_model_s0": "Quantile model", "history_lookup": "History lookup",
         "carry_forward": "Delay carried forward", "schedule": "Timetable, shifted",
         "fixed_margin": "Fixed margin"}


def csv(path):
    return pd.read_csv(path).replace({np.nan: None})


def replay_cases(db, preds_dir, label, rng):
    # one caught bus and one missed bus, 10 minutes ahead, chosen at random from the model's
    # test rows that had a reading of the bus itself, so the page can show what was known
    val = rows_for(db, pd.read_parquet(Path(preds_dir) / f"{label}_val.parquet"))
    ev = rows_for(db, pd.read_parquet(Path(preds_dir) / f"{label}_eval.parquet"))
    offs = {m: offset_table(val, m, OPERATING) for m in (MODEL, "schedule")}
    ev = ev[(ev["horizon_min"] == 10) & ev["last_seen_age_s"].notna()].copy()
    for m in offs:
        ev[f"{m}_be"] = ev[f"{m}_q10"] + ev["horizon_min"].map(offs[m])
    y = ev["target_delay_s"]
    caught = ev[(y >= ev[f"{MODEL}_be"]) & (y - ev[f"{MODEL}_be"] < 60) & (y - ev["schedule_be"] > 120)]
    missed = ev[y < ev[f"{MODEL}_be"]]
    con = duckdb.connect(db, read_only=True)
    out = []
    for kind, pool in (("caught", caught), ("missed", missed)):
        r = pool.iloc[rng.integers(len(pool))]
        t = con.execute("""select t.route, e.stop_name, t.sched_dep, t.decision_time, t.actual_dep
                           from training_rows t join stop_events_clean e using (agency, service_date, trip_id, stop_seq)
                           where service_date = ? and trip_id = ? and stop_seq = ? and horizon_min = 10""",
                        [str(r["service_date"]), r["trip_id"], int(r["stop_seq"])]).fetchone()
        route, stop_name, sched, decision, actual = t
        before = con.execute("""select stop_seq, stop_name, sched_dep, actual_dep, dep_delay_s
                                from stop_events_clean
                                where service_date = ? and trip_id = ? and stop_seq < ? and dep_measured
                                order by stop_seq""", [str(r["service_date"]), r["trip_id"], int(r["stop_seq"])]).df()
        before["known"] = before["actual_dep"] <= decision
        sec = pd.Timedelta(seconds=1)
        out.append({
            "kind": kind, "day": str(r["service_date"]), "route": route, "stop": stop_name,
            "scheduled": str(sched), "decision": str(decision), "actual": str(actual),
            "be_there_by": str(sched + float(r[f"{MODEL}_be"]) * sec),
            "range80": [str(sched + float(r[f"{MODEL}_q10"]) * sec), str(sched + float(r[f"{MODEL}_q90"]) * sec)],
            "timetable_be_there_by": str(sched + float(r["schedule_be"]) * sec),
            "last_known_delay_s": float(before[before["known"]]["dep_delay_s"].iloc[-1]) if before["known"].any() else None,
            "earlier_stops": [{"seq": int(b.stop_seq), "stop": b.stop_name, "scheduled": str(b.sched_dep),
                               "actual": str(b.actual_dep), "delay_s": float(b.dep_delay_s), "known": bool(b.known)}
                              for b in pd.concat([before[before["known"]].tail(3), before[~before["known"]].head(5)]).itertuples()],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", default="test")
    ap.add_argument("--forward", nargs="*", default=[])
    ap.add_argument("--db", default="data/brace.duckdb")
    ap.add_argument("--predictions", default="data/predictions")
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="docs/dashboard/data.js")
    args = ap.parse_args()

    res, ana = Path(args.results), Path(args.results) / "analysis"
    run = json.loads((res / f"{args.test}_run.json").read_text())
    data = {
        "names": NAMES, "shown": SHOWN, "model": MODEL, "operating": OPERATING,
        "test": {
            "days": run["eval_days"], "rows": run["rows"]["eval"], "train_days": run["train_days"],
            "val_days": run["val_days"], "commit": run["run"]["commit"],
            "summary": csv(res / f"{args.test}_summary.csv").to_dict("records"),
            "tradeoff": csv(ana / f"{args.test}_tradeoff.csv").to_dict("records"),
            "bootstrap": csv(ana / f"{args.test}_bootstrap.csv").to_dict("records"),
            "breakdown": csv(ana / f"{args.test}_breakdown.csv").query("method in @SHOWN").to_dict("records"),
        },
        "forward": [],
        "replay": replay_cases(args.db, args.predictions, args.test, np.random.default_rng(0)),
        "built": run_info()["commit"],
    }
    for label in args.forward:
        r = json.loads((res / f"{label}_run.json").read_text())
        data["forward"].append({
            "label": label, "days": r["eval_days"], "rows": r["rows"]["eval"],
            "scores": csv(res / f"{label}_scores.csv").to_dict("records"),
            "bootstrap": csv(ana / f"{label}_bootstrap.csv").to_dict("records"),
            "breakdown": csv(ana / f"{label}_breakdown.csv").query("method in @SHOWN").to_dict("records"),
        })
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("window.BRACE = " + json.dumps(data, default=str) + ";\n")
    print("wrote", out, f"{out.stat().st_size >> 10} KB")


if __name__ == "__main__":
    main()
