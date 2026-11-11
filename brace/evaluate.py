# Fits every method on training days, sets each method's "be there by" offset on
# validation days, and scores it on the days being evaluated.
#
#   uv run python -m brace.evaluate --split dev    # score on validation days, use freely
#   uv run python -m brace.evaluate --split test   # frozen test days, run once settings are final

import argparse
import json
import subprocess
import time
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from brace.methods import METHODS
from brace.runinfo import run_info

TRAIN_DAYS = ("2026-10-28", "2026-11-04")
VAL_DAYS = ("2026-11-05", "2026-11-06")
TEST_DAYS = ("2026-11-07", "2026-11-10")
TARGET_MISS = 0.10
SEEDS = (0, 1, 2)


def load(db):
    con = duckdb.connect(db, read_only=True)
    df = con.execute("select * from training_rows").df()
    df["service_date"] = pd.to_datetime(df["service_date"]).dt.date
    return df


def between(df, span):
    a, b = date.fromisoformat(span[0]), date.fromisoformat(span[1])
    return df[(df["service_date"] >= a) & (df["service_date"] <= b)]


def offsets(val, q10):
    # per horizon: how much to add to q10 so exactly TARGET_MISS of validation buses leave before it
    out = {}
    for h in sorted(val["horizon_min"].unique()):
        m = (val["horizon_min"] == h).to_numpy()
        out[int(h)] = float(np.quantile(val["target_delay_s"].to_numpy()[m] - q10[m], TARGET_MISS))
    return out


def score(rows, q10, q50, q90, offs):
    y = rows["target_delay_s"].to_numpy()
    be_there = q10 + rows["horizon_min"].map(offs).to_numpy()
    caught = y >= be_there
    df = pd.DataFrame({
        "day": rows["service_date"].astype(str).to_numpy(),
        "horizon_min": rows["horizon_min"].to_numpy(),
        "missed_raw": y < q10,
        "missed": ~caught,
        "wait_s": np.where(caught, y - be_there, np.nan),
        "covered80": (y >= q10) & (y <= q90),
        "width80_s": q90 - q10,
        "abs_err50_s": np.abs(y - q50),
    })
    return df.groupby(["day", "horizon_min"]).agg(
        n=("missed", "size"),
        missed_rate=("missed", "mean"),
        missed_rate_uncalibrated=("missed_raw", "mean"),
        mean_wait_s=("wait_s", "mean"),
        median_wait_s=("wait_s", "median"),
        coverage80=("covered80", "mean"),
        width80_s=("width80_s", "mean"),
        mae50_s=("abs_err50_s", "mean"),
    ).reset_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--db", default="data/brace.duckdb")
    ap.add_argument("--out", default="results")
    ap.add_argument("--methods", default=",".join(METHODS))
    args = ap.parse_args()

    if args.split == "test":
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "brace", "dbt"], capture_output=True, text=True).stdout
        if dirty.strip():
            raise SystemExit("commit your code before the test run, so the results point at it")

    df = load(args.db)
    train, val = between(df, TRAIN_DAYS), between(df, VAL_DAYS)
    evaluate_on = val if args.split == "dev" else between(df, TEST_DAYS)
    assert not set(train["service_date"]) & set(evaluate_on["service_date"]), "train and eval days overlap"
    print(f"train {len(train)} rows, val {len(val)}, eval {len(evaluate_on)}")

    tables, offsets_used, timing = [], {}, {}
    for name in args.methods.split(","):
        fn = METHODS[name]
        for seed in (SEEDS if name == "quantile_model" else (None,)):
            t0 = time.time()
            kw = {"seed": seed} if seed is not None else {}
            on_val = fn(train, val, **kw)
            offs = offsets(val, on_val[0])
            q10, q50, q90 = on_val if args.split == "dev" else fn(train, evaluate_on, **kw)
            t = score(evaluate_on, q10, q50, q90, offs)
            t.insert(0, "seed", seed)
            t.insert(0, "method", name)
            tables.append(t)
            key = name if seed is None else f"{name}/seed{seed}"
            offsets_used[key] = offs
            timing[key] = round(time.time() - t0, 1)
            print(key, f"{timing[key]}s", t.groupby("horizon_min")[["missed_rate", "mean_wait_s"]].mean().round(3).to_dict())

    scores = pd.concat(tables)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scores.to_csv(out / f"{args.split}_scores.csv", index=False)

    # mean and spread over evaluated days (and seeds for the model)
    summary = scores.groupby(["method", "horizon_min"]).agg(
        missed_rate_mean=("missed_rate", "mean"), missed_rate_std=("missed_rate", "std"),
        mean_wait_s_mean=("mean_wait_s", "mean"), mean_wait_s_std=("mean_wait_s", "std"),
        coverage80_mean=("coverage80", "mean"), width80_s_mean=("width80_s", "mean"),
        mae50_s_mean=("mae50_s", "mean"), mae50_s_std=("mae50_s", "std"),
        days=("day", "nunique"), rows=("n", "sum"),
    ).round(4).reset_index()
    summary.to_csv(out / f"{args.split}_summary.csv", index=False)
    meta = {
        "run": run_info(),
        "split": args.split,
        "train_days": TRAIN_DAYS, "val_days": VAL_DAYS,
        "eval_days": VAL_DAYS if args.split == "dev" else TEST_DAYS,
        "rows": {"train": len(train), "val": len(val), "eval": len(evaluate_on)},
        "target_missed_rate": TARGET_MISS,
        "offsets_s_from_val": offsets_used,
        "seconds_per_method": timing,
        "data": "opentransportdata.swiss Ist-Daten v2, VBZ buses, built by dbt models in dbt/",
    }
    (out / f"{args.split}_run.json").write_text(json.dumps(meta, indent=1, default=str))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
