# End to end on the committed line-31 sample: real dbt models, a real (small)
# model, the real API. Nothing is mocked. Takes about half a minute.

import os
import subprocess
from datetime import datetime

import duckdb
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
    db, model_path = str(tmp / "t.duckdb"), str(tmp / "m.joblib")
    env = {**os.environ, "BRACE_DB": db}
    subprocess.run(["dbt", "build", "--project-dir", "dbt", "--profiles-dir", "dbt", "--quiet",
                    "--vars", '{events_glob: "tests/fixtures/events/*/*.parquet"}'], cwd=ROOT, env=env, check=True)
    subprocess.run(["python", "-m", "brace.train", "--db", db, "--out", model_path], cwd=ROOT, check=True)
    os.environ["BRACE_DB"], os.environ["BRACE_MODEL"] = db, model_path
    from brace import api
    api.model.cache_clear()
    with duckdb.connect(db, read_only=True) as con:
        known = con.execute("""select route, stop_id, sched_dep from training_rows
                               where service_date = '2026-11-05' and horizon_min = 10
                                 and last_seen_delay_s is not null limit 1""").fetchone()
    return TestClient(api.app), known


def test_health(client):
    c, _ = client
    assert c.get("/health").json()["ok"]


def test_replay_plan(client):
    c, (route, stop, sched) = client
    r = c.get("/plan", params={"route": route, "stop_id": stop, "sched_dep": sched.isoformat(), "minutes_before": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["mode"] == "replay"
    lo, hi = (datetime.fromisoformat(x) for x in body["range80"])
    assert lo <= datetime.fromisoformat(body["likely_departure"]) <= hi
    assert "caught" in body["what_happened"]


def test_bad_horizon(client):
    c, (route, stop, sched) = client
    r = c.get("/plan", params={"route": route, "stop_id": stop, "sched_dep": sched.isoformat(), "minutes_before": 7})
    assert r.status_code == 400


def test_unknown_departure(client):
    c, (route, stop, _) = client
    r = c.get("/plan", params={"route": route, "stop_id": stop, "sched_dep": "2026-11-05T03:33:00"})
    assert r.status_code == 404
