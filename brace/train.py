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
    args = ap.parse_args()

    df = load(args.db)
    train, val = between(df, TRAIN_DAYS), between(df, VAL_DAYS)
    fitted = fit_quantile_model(train, seed=args.seed)
    q10, _, _ = predict_quantile_model(fitted, val)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "fitted": fitted,
        "offsets": offsets(val, q10),
        "train_days": TRAIN_DAYS,
        "val_days": VAL_DAYS,
        "run": run_info(),
    }, args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()
