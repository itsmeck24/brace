# Second evaluation: tradeoff curves,
# trip-level bootstrap at the 10% point, and breakdowns by hour, line and how
# fresh the bus's last reading was. Reads the predictions saved by
# `brace.evaluate --save-predictions`, so nothing is refit here.
#
#   uv run python -m brace.analyze --label test
#   uv run python -m brace.analyze --label forward_2026-11-11_2026-11-11

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from brace.runinfo import run_info

KEYS = ["service_date", "trip_id", "stop_seq", "horizon_min"]
METHODS = ["schedule", "fixed_margin", "history_lookup", "carry_forward", "quantile_model_s0"]
MODEL = "quantile_model_s0"
TARGETS = np.round(np.arange(0.02, 0.301, 0.02), 2)
OPERATING = 0.10
B = 1000
SMALL = 1000
HOUR_BANDS = [(0, 6, "00-06"), (6, 9, "06-09"), (9, 16, "09-16"), (16, 19, "16-19"), (19, 24, "19-24")]


def rows_for(db, preds):
    con = duckdb.connect(db, read_only=True)
    days = sorted(preds["service_date"].astype(str).unique())
    rows = con.execute(
        f"""select service_date, trip_id, stop_seq, horizon_min, route, sched_hour,
                   last_seen_age_s, target_delay_s
            from training_rows where service_date in ({','.join('?' * len(days))})""", days).df()
    for df in (rows, preds):
        df["service_date"] = pd.to_datetime(df["service_date"]).dt.date
    out = preds.merge(rows, on=KEYS, how="left", validate="one_to_one")
    assert out["target_delay_s"].notna().all(), "predictions without a matching training row"
    return out


def offset_table(val, method, target):
    # per horizon: shift q10 so `target` of validation buses leave before it
    d = val["target_delay_s"] - val[f"{method}_q10"]
    return d.groupby(val["horizon_min"]).quantile(target).to_dict()


def outcome(df, method, offs):
    be = df[f"{method}_q10"] + df["horizon_min"].map(offs)
    y = df["target_delay_s"]
    caught = y >= be
    return ~caught, np.where(caught, y - be, 0.0), caught


def per_day_mean(df, missed, wait, caught):
    g = pd.DataFrame({"day": df["service_date"], "h": df["horizon_min"], "m": missed, "w": wait, "c": caught})
    d = g.groupby(["h", "day"]).agg(m=("m", "mean"), w=("w", "sum"), c=("c", "sum"))
    d["wait"] = d["w"] / d["c"]
    return d.groupby("h").agg(missed_rate=("m", "mean"), mean_wait_s=("wait", "mean"))


def tradeoff(val, ev):
    out = []
    for method in METHODS:
        for t in TARGETS:
            offs = offset_table(val, method, t)
            m, w, c = outcome(ev, method, offs)
            r = per_day_mean(ev, m, w, c).reset_index()
            r.insert(0, "target_miss", t)
            r.insert(0, "method", method)
            out.append(r)
    return pd.concat(out).rename(columns={"h": "horizon_min"})


def bootstrap(val, ev, rng):
    # resample whole trips within each day; statistic is the mean over days, like the headline
    out = []
    for h in sorted(ev["horizon_min"].unique()):
        e = ev[ev["horizon_min"] == h]
        per_method = {}
        for method in METHODS:
            m, w, c = outcome(e, method, offset_table(val, method, OPERATING))
            per_method[method] = pd.DataFrame({"day": e["service_date"], "trip": e["trip_id"], "m": m, "w": w, "c": c})
        days = sorted(e["service_date"].unique())
        stats = {method: np.zeros((B + 1, 2)) for method in METHODS}  # row 0 is the point estimate
        for day in days:
            aggs = {}
            for method, f in per_method.items():
                f = f[f["day"] == day]
                aggs[method] = f.groupby("trip").agg(n=("m", "size"), m=("m", "sum"), w=("w", "sum"), c=("c", "sum")).sort_index()
            n_trips = len(next(iter(aggs.values())))
            W = np.vstack([np.ones(n_trips), rng.multinomial(n_trips, np.full(n_trips, 1 / n_trips), size=B)])
            for method, a in aggs.items():
                s = W @ a[["n", "m", "w", "c"]].to_numpy(dtype=float)
                stats[method][:, 0] += s[:, 1] / s[:, 0] / len(days)
                stats[method][:, 1] += s[:, 2] / s[:, 3] / len(days)
        for method in METHODS:
            for i, metric in enumerate(["missed_rate", "mean_wait_s"]):
                v = stats[method][:, i]
                row = {"horizon_min": h, "method": method, "metric": metric, "value": v[0],
                       "lo95": np.quantile(v[1:], 0.025), "hi95": np.quantile(v[1:], 0.975)}
                if method != MODEL:
                    d = stats[MODEL][:, i] - v
                    row.update(model_minus_this=d[0], diff_lo95=np.quantile(d[1:], 0.025),
                               diff_hi95=np.quantile(d[1:], 0.975))
                out.append(row)
    return pd.DataFrame(out)


def freshness(age):
    return pd.cut(age.fillna(-1), [-np.inf, -0.5, 120, 300, np.inf],
                  labels=["none yet", "up to 2 min", "2 to 5 min", "older"]).astype(str)


def hour_band(hour):
    h = hour.astype(int) % 24
    out = pd.Series("", index=hour.index)
    for lo, hi, name in HOUR_BANDS:
        out[(h >= lo) & (h < hi)] = name
    return out


def breakdown(val, ev):
    groups = {"hour": hour_band(ev["sched_hour"]), "line": ev["route"].astype(str),
              "freshness": freshness(ev["last_seen_age_s"])}
    out = []
    for method in METHODS:
        m, w, c = outcome(ev, method, offset_table(val, method, OPERATING))
        covered = (ev["target_delay_s"] >= ev[f"{method}_q10"]) & (ev["target_delay_s"] <= ev[f"{method}_q90"])
        f = pd.DataFrame({"h": ev["horizon_min"], "m": m, "w": w, "c": c, "cov": covered})
        for dim, values in groups.items():
            g = f.assign(group=values.to_numpy()).groupby(["h", "group"]).agg(
                rows=("m", "size"), missed_rate=("m", "mean"), w=("w", "sum"), c=("c", "sum"), coverage80=("cov", "mean"))
            g["mean_wait_s"] = g["w"] / g["c"]
            g = g.drop(columns=["w", "c"]).reset_index()
            g.insert(0, "dimension", dim)
            g.insert(0, "method", method)
            out.append(g)
    b = pd.concat(out).rename(columns={"h": "horizon_min"})
    b["small_group"] = b["rows"] < SMALL
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="test, or forward_FIRST_LAST")
    ap.add_argument("--db", default="data/brace.duckdb")
    ap.add_argument("--predictions", default="data/predictions")
    ap.add_argument("--out", default="results/analysis")
    args = ap.parse_args()

    info = run_info()
    p = Path(args.predictions)
    val = rows_for(args.db, pd.read_parquet(p / f"{args.label}_val.parquet"))
    ev = rows_for(args.db, pd.read_parquet(p / f"{args.label}_eval.parquet"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tradeoff(val, ev).round(5).to_csv(out / f"{args.label}_tradeoff.csv", index=False)
    bootstrap(val, ev, np.random.default_rng(0)).round(5).to_csv(out / f"{args.label}_bootstrap.csv", index=False)
    breakdown(val, ev).round(5).to_csv(out / f"{args.label}_breakdown.csv", index=False)
    meta = {"run": info, "label": args.label, "eval_days": sorted(map(str, ev["service_date"].unique())),
            "eval_rows": len(ev), "model_seed": 0, "operating_target_miss": OPERATING,
            "bootstrap": {"resamples": B, "unit": "trip, within day", "rng_seed": 0},
            "small_group_rows": SMALL}
    (out / f"{args.label}_analysis_run.json").write_text(json.dumps(meta, indent=1, default=str))
    print("wrote", out)


if __name__ == "__main__":
    main()
