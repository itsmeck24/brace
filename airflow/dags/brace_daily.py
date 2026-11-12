# Daily refresh: yesterday's Swiss file -> events -> dbt build (with tests) ->
# profile -> retrain. Each step is a shell command that also works by hand.
#
# Runs in the official Airflow image with the repo mounted at /opt/brace
# (see airflow/docker-compose.yml).

import os
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag

REPO = "/opt/brace"
BIN = os.environ.get("BRACE_BIN", "/home/airflow/brace-venv/bin")


# manual runs in Airflow 3 have no logical date (so no ds); fall back to when the run was triggered
def day(offset):
    return "{{ macros.ds_add((dag_run.logical_date or dag_run.run_after) | ds, %d) }}" % offset


YESTERDAY = day(-1)


@dag(
    schedule="0 6 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    tags=["brace"],
)
def brace_daily():
    ingest = BashOperator(
        task_id="ingest_swiss_day",
        bash_command=f"cd {REPO} && {BIN}/python -m brace.swiss --start {YESTERDAY} --end {YESTERDAY} --operator VBZ",
    )
    # dbt build fails the task if any data test fails, so nothing downstream runs on bad data
    build = BashOperator(
        task_id="dbt_build",
        bash_command=f"cd {REPO} && {BIN}/dbt build --project-dir dbt --profiles-dir dbt",
    )
    profile = BashOperator(
        task_id="profile",
        bash_command=(f"cd {REPO} && {BIN}/python -m brace.profile --events data/events/ch_vbz "
                      f"--out data/daily_profile.json --source 'opentransportdata.swiss Ist-Daten v2, VBZ buses'"),
    )
    # train on the 8 days before the last 2, set the "be there by" offset on the last 2
    train = BashOperator(
        task_id="train",
        bash_command=(f"cd {REPO} && {BIN}/python -m brace.train --db $BRACE_DB --out models/brace_latest.joblib "
                      f"--train-days {day(-10)} {day(-3)} --val-days {day(-2)} {day(-1)}"),
    )
    ingest >> build >> profile >> train


brace_daily()
