# BRACE

Bus Range And Calibration Engine. It tells you when to be at the bus stop so you catch the bus
9 times out of 10, and how long you'll usually wait.

The timetable says 17:12. The bus might leave at 17:11 or 17:19. Trusting the timetable means
waiting or missing the bus. BRACE learns from past departures and what the bus is doing right now,
and answers: "be there by 17:12:40, you'd usually wait about a minute and a half."

## Results

Zurich city buses (VBZ), 14 days of measured departures from Swiss open data. Trained on
2026-10-28 to 11-04, "be there by" offsets set on 11-05 and 11-06 (each method shifted to miss 10%
there), then scored once on 4 frozen days, 2026-11-07 to 11-10 (Saturday to Tuesday), 1.34 million
rider decisions. Numbers are the mean over the 4 test days, ± the spread between days. The model
row also averages 3 seeds.

**Average wait at the stop, for a bus you catch:**

| Method | Decide 5 min ahead | 10 min ahead | 20 min ahead | Buses missed (10 min ahead) |
| --- | --- | --- | --- | --- |
| Timetable, shifted to miss 10% | 107.7 s ± 10.8 | 107.7 s | 107.7 s | 9.5% ± 1.3 |
| History by line, stop and hour | 99.7 s ± 9.6 | 99.7 s | 99.7 s | 10.5% ± 2.0 |
| Current delay carried forward | 93.5 s ± 6.2 | 110.8 s | 129.2 s | 9.3% ± 0.5 |
| **Quantile model** | **74.8 s ± 5.8** | **87.3 s ± 6.7** | **96.1 s ± 7.2** | **9.1% ± 0.4** |

- Deciding 10 minutes ahead, the model cut waiting by 19% compared with the shifted timetable and
  by 12% compared with the history lookup, while missing fewer buses than either.
- The gain shrinks with distance: 5 minutes ahead it saves 25% over history, 20 minutes ahead only
  4% (96.1 s vs 99.7 s). Twenty minutes out, most buses haven't started their trip, so there is
  little live information to use.
- The model's miss rate stayed between 8.6% and 10.3% on every test day, horizon and seed. The
  target set before the test was at most 10% on every day, so it was narrowly missed once. The
  history lookup missed 13.3% on Sunday 11-08: offsets set on two weekdays did not carry over to a
  quiet Sunday.
- Carrying the current delay forward is good 5 minutes ahead and worse than plain history 20
  minutes ahead. An old reading of the bus is worse than knowing the line.

Raw numbers: `results/test_summary.csv`, per day in `results/test_scores.csv`, with the command,
commit and versions in `results/test_run.json`. Data quality per day: `results/data_profile_ch_vbz.json`.

## How it works

```
daily source file ──> adapter ──> stop events (Parquet) ──> dbt: clean + tests ──> training rows
                                                                                     │
                       FastAPI /plan <── saved model <── fit on earlier days ────────┘
```

1. **Adapters** turn each agency's data into one stop-event table: one row per bus per stop with
   scheduled and actual times. There are two today:
   - `brace/swiss.py` for Swiss Ist-Daten, which has measured departure times.
   - `brace/gtfs_rt.py` for GTFS-Realtime feeds (tried on Valley Metro in Phoenix). GTFS-Realtime
     never states when a bus arrived, so arrivals are inferred as time windows and labeled that way.
2. **dbt** builds the cleaned events and the training rows in DuckDB. Each training row is a moment
   when a rider decides (5, 10 or 20 minutes before the scheduled departure), with features that
   were known at that moment. Tests check that no feature uses anything that happened later.
3. **Methods** (`brace/methods.py`): the timetable, a fixed margin, a history lookup by line, stop
   and hour, carrying the current delay forward, and a quantile gradient boosting model.
4. **Evaluation** (`brace/evaluate.py`): fit on 8 days, set every method's "be there by" offset on
   the next 2 days so each one misses 10% there, then score once on 4 later days nobody looked at.
5. **API** (`brace/api.py`): `/plan` returns "be there by", the likely departure and an 80% range.
   On a recorded day it replays exactly what was known at decision time and also shows what
   actually happened.

## Run it

Needs Python 3.13 and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run pytest -q                       # 17 tests, under 2 minutes
```

Full pipeline on the 14 days used here (downloads about 8 GB, keeps about 35 MB):

```
uv run python -m brace.swiss --start 2026-10-28 --end 2026-11-10 --operator VBZ
uv run dbt build --project-dir dbt --profiles-dir dbt
uv run python -m brace.evaluate --split dev
uv run python -m brace.evaluate --split test
uv run python -m brace.train
uv run uvicorn brace.api:app
```

Then:

```
curl 'localhost:8000/plan?route=31&stop_id=853081211&sched_dep=2026-11-09T17:08&minutes_before=10'
```

Small version with no download, using the line 31 sample committed in `tests/fixtures`:

```
BRACE_DB=sample.duckdb uv run dbt build --project-dir dbt --profiles-dir dbt --vars '{events_glob: "tests/fixtures/events/*/*.parquet"}'
uv run python -m brace.evaluate --split dev --db sample.duckdb --out sample_results
```

## Tests

| What | Real or mocked |
| --- | --- |
| Label logic for GTFS-Realtime brackets | Real functions, hand-made vehicle reports |
| Calibration offset, scoring, history fallback, own-day exclusion | Real functions, small frames |
| API end to end | Real dbt build and a real model on the line 31 sample. Nothing mocked |
| dbt data tests | Run on the real data: unique stop events, no future data in features, horizons |

CI runs the tests, the dbt build and a small evaluation on the sample, with no network.

## Data and licenses

- Zurich bus data: [opentransportdata.swiss](https://opentransportdata.swiss), Ist-Daten v2. Free
  to use and publish with the source cited ([terms](https://opentransportdata.swiss/en/terms-of-use/)).
  The sample in `tests/fixtures` is a small extract of it.
- Phoenix data: Valley Metro GTFS-Realtime and GTFS from
  [Phoenix Open Data](https://phoenixopendata.com/dataset/general-transit-feed-specification)
  (CC BY / ODC-BY). Not included in this repo.
- This is an unofficial project, not endorsed by or affiliated with any transit agency or city.

## Limitations

- 14 days of autumn data from one operator. Four test days, no holidays, no snow.
- Replay only. The API answers for recorded days or from the timetable; it does not read a live
  feed yet.
- The model was slightly cautious on the test days (9.0% to 9.4% missed against a 10% aim), so
  riders would wait a little longer than needed. Its raw 80% range covered 78%, a bit narrow.
- "Actual" times come from the operator's control systems. The data documentation says their
  quality varies. Only rows marked as measured are used: 95.5% to 98.9% of scheduled departures
  per day.
- Timetable times are given to the minute, so a "delay" includes up to a minute of rounding.
- Phoenix goes through the same adapter and dbt tests, but GTFS-Realtime has no measured
  departures, so nothing is trained or scored on it. 52 minutes of feed were collected.
- No real riders used this. "Would have waited" and "would have missed" are computed on recorded
  departures.

## Roadmap (not built)

- Live mode from the Swiss realtime feed instead of recorded days.
- More operators through the same adapter, and a check of whether the model carries over to them.
- A weekly report of how each line's catch rate holds up.
