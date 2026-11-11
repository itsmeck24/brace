# Fits the served model on the training days, sets its "be there by" offsets on the
# validation days (same as the evaluation), and saves it for the API.
#
# usage: uv run python -m brace.train --db data/brace.duckdb --out models/brace.joblib

import argparse
from pathlib import Path

import joblib

from brace.evaluate import TRAIN_DAYS, VAL_DAYS, between, load, offsets
from brace.methods import fit_quantile_model, predict_quantile_model
from brace.runinfo import run_info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/brace.duckdb")
    ap.add_argument("--out", default="models/brace.joblib")
    ap.add_argument("--seed", type=int, default=0)
    # defaults are the evaluated setup; the daily job passes a rolling window
    ap.add_argument("--train-days", nargs=2, default=TRAIN_DAYS, metavar=("FIRST", "LAST"))
    ap.add_argument("--val-days", nargs=2, default=VAL_DAYS, metavar=("FIRST", "LAST"))
    args = ap.parse_args()

    info = run_info()
    df = load(args.db)
    train, val = between(df, args.train_days), between(df, args.val_days)
    if train.empty or val.empty:
        raise SystemExit(f"no rows for train {args.train_days} or val {args.val_days}")
    fitted = fit_quantile_model(train, seed=args.seed)
    q10, _, _ = predict_quantile_model(fitted, val)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "fitted": fitted,
        "offsets": offsets(val, q10),
        "train_days": tuple(args.train_days),
        "val_days": tuple(args.val_days),
        "run": info,
    }, args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()
