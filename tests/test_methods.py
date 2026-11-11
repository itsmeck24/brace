from datetime import date

import numpy as np
import pandas as pd

from brace.evaluate import offsets, score
from brace.methods import _carry_point, _out_of_day_history, history_lookup


def rows(n, delay, route="31", stop="A", hour=8.0, day=date(2026, 10, 28), horizon=10, **extra):
    return pd.DataFrame({
        "route": [route] * n, "stop_id": [stop] * n, "sched_hour": [hour] * n,
        "service_date": [day] * n, "horizon_min": [horizon] * n,
        "target_delay_s": np.asarray(delay, dtype=float),
        **{k: [v] * n for k, v in extra.items()},
    })


def test_offset_hits_target_miss_rate():
    val = rows(1000, np.arange(1000))
    offs = offsets(val, np.zeros(1000))
    # 10% of delays 0..999 are below ~100 s
    assert 95 <= offs[10] <= 105
    got = score(val, np.zeros(1000), np.zeros(1000), np.zeros(1000), offs)
    assert abs(got["missed_rate"].iloc[0] - 0.10) < 0.01


def test_wait_only_counts_caught_buses():
    val = rows(4, [0, 60, 120, 180])
    got = score(val, np.full(4, 50.0), np.full(4, 100.0), np.full(4, 150.0), {10: 0.0})
    r = got.iloc[0]
    assert r["missed_rate"] == 0.25          # the bus at 0 s left before 50 s
    assert r["mean_wait_s"] == (10 + 70 + 130) / 3
    assert r["coverage80"] == 0.5            # 60 and 120 are inside [50, 150]


def test_history_falls_back_when_group_is_small():
    train = pd.concat([rows(40, np.full(40, 100.0), stop="A"), rows(5, np.full(5, 900.0), stop="B")])
    q10, q50, q90 = history_lookup(train, rows(1, [0], stop="B"))
    # stop B has 5 rows, too few; falls back to route-hour level which is mostly stop A
    assert q50[0] == 100.0


def test_carry_point_prefers_own_trip_then_prev_bus():
    r = pd.DataFrame({"last_seen_delay_s": [30.0, np.nan, np.nan], "prev_bus_delay_s": [99.0, 45.0, np.nan]})
    point, src = _carry_point(r, np.array([7.0, 7.0, 7.0]))
    assert list(point) == [30.0, 45.0, 7.0]
    assert list(src) == [0, 1, 2]


def test_training_history_excludes_own_day():
    d1, d2 = date(2026, 10, 28), date(2026, 10, 29)
    train = pd.concat([rows(40, np.full(40, 10.0), day=d1), rows(40, np.full(40, 500.0), day=d2)])
    q10, q50, q90 = _out_of_day_history(train)
    # day 1 rows only see day 2's history and the other way round
    assert set(q50[:40]) == {500.0}
    assert set(q50[40:]) == {10.0}
