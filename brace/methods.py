# The five ways of guessing a departure delay. Each one gets fit on training rows
# and returns q10, q50, q90 (seconds late) for any rows.

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

QS = (0.1, 0.5, 0.9)
MIN_GROUP = 30  # fewer past departures than this and the lookup falls back to a coarser group


def schedule(train, rows):
    z = np.zeros(len(rows))
    return z, z, z


def fixed_margin(train, rows):
    q = np.quantile(train["target_delay_s"], QS)
    return tuple(np.full(len(rows), v) for v in q)


def _hour(df):
    return df["sched_hour"].astype(int)


def history_table(train):
    # delay quantiles at three levels of detail, coarsest last
    levels = [["route", "stop_id", "hour"], ["route", "hour"], ["route"]]
    t = train.assign(hour=_hour(train))
    tables = []
    for keys in levels:
        g = t.groupby(keys)["target_delay_s"]
        stats = g.quantile(list(QS)).unstack()
        stats.columns = ["q10", "q50", "q90"]
        stats["n"] = g.size()
        tables.append((keys, stats[stats["n"] >= MIN_GROUP]))
    return tables, np.quantile(train["target_delay_s"], QS)


def history_lookup(train, rows, table=None):
    tables, fallback = table or history_table(train)
    r = rows.assign(hour=_hour(rows))
    out = np.full((len(r), 3), np.nan)
    for keys, stats in tables:
        hit = r[keys].merge(stats, left_on=keys, right_index=True, how="left")[["q10", "q50", "q90"]].to_numpy()
        fill = np.isnan(out[:, 0]) & ~np.isnan(hit[:, 0])
        out[fill] = hit[fill]
    miss = np.isnan(out[:, 0])
    out[miss] = fallback
    return out[:, 0], out[:, 1], out[:, 2]


def _carry_point(rows, hist_q50):
    # what the bus is doing now, else what the last bus at this stop did, else history
    own = rows["last_seen_delay_s"].to_numpy(dtype=float)
    prev = rows["prev_bus_delay_s"].to_numpy(dtype=float)
    src = np.where(~np.isnan(own), 0, np.where(~np.isnan(prev), 1, 2))
    point = np.where(src == 0, own, np.where(src == 1, prev, hist_q50))
    return point, src


def carry_forward(train, rows):
    table = history_table(train)
    _, train_q50, _ = history_lookup(train, train, table)
    p_train, s_train = _carry_point(train, train_q50)
    resid = train["target_delay_s"].to_numpy() - p_train
    # residual spread depends on how far ahead and on which source the guess came from
    keys = pd.DataFrame({"h": train["horizon_min"].to_numpy(), "s": s_train, "r": resid})
    spread = keys.groupby(["h", "s"])["r"].quantile(list(QS)).unstack()

    _, rows_q50, _ = history_lookup(train, rows, table)
    p, s = _carry_point(rows, rows_q50)
    idx = pd.MultiIndex.from_arrays([rows["horizon_min"].to_numpy(), s])
    off = spread.reindex(idx).to_numpy()
    return p + off[:, 0], p + off[:, 1], p + off[:, 2]


FEATURES = [
    "horizon_min", "sched_hour", "weekday", "trip_progress", "min_since_trip_start",
    "trip_should_have_started", "stops_to_go", "last_seen_delay_s", "last_seen_age_s",
    "prev_bus_delay_s", "prev_bus_age_s", "hist_q10", "hist_q50", "hist_q90", "route_code",
]


def _features(rows, hist, routes):
    X = pd.DataFrame({c: rows[c] for c in FEATURES if c in rows})
    X["trip_should_have_started"] = X["trip_should_have_started"].astype(float)
    X["hist_q10"], X["hist_q50"], X["hist_q90"] = hist
    X["route_code"] = rows["route"].map(routes).fillna(-1)
    return X[FEATURES].astype(float)


def _out_of_day_history(train):
    # history features for training rows must not include their own day,
    # or the model learns to trust a lookup that already saw the answer
    out = np.full((len(train), 3), np.nan)
    days = train["service_date"].to_numpy()
    for d in np.unique(days):
        mask = days == d
        q = history_lookup(train[~mask], train[mask])
        out[mask] = np.column_stack(q)
    return out[:, 0], out[:, 1], out[:, 2]


def quantile_model(train, rows, seed=0, max_train_rows=1_500_000):
    if len(train) > max_train_rows:
        train = train.sample(max_train_rows, random_state=seed)
    routes = {r: i for i, r in enumerate(sorted(train["route"].unique()))}
    X_train = _features(train, _out_of_day_history(train), routes)
    X_rows = _features(rows, history_lookup(train, rows), routes)
    cat = [FEATURES.index("route_code")]
    preds = []
    for q in QS:
        m = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=300, learning_rate=0.1,
                                          categorical_features=cat, random_state=seed)
        m.fit(X_train, train["target_delay_s"])
        preds.append(m.predict(X_rows))
    lo, mid, hi = np.sort(np.column_stack(preds), axis=1).T  # quantiles can cross; keep them ordered
    return lo, mid, hi


METHODS = {
    "schedule": schedule,
    "fixed_margin": fixed_margin,
    "history_lookup": history_lookup,
    "carry_forward": carry_forward,
    "quantile_model": quantile_model,
}
