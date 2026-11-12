# Swiss Ist-Daten adapter: downloads daily files from opentransportdata.swiss and
# turns them into the common stop-event table (see events.py), one Parquet file
# per day. Only one operator and product are kept, the rest of the 660 MB file is
# dropped right after download.
#
# usage: uv run python -m brace.swiss --start 2026-10-28 --end 2026-11-10 --operator VBZ

import argparse
import re
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import requests

DATASET_PAGE = "https://data.opentransportdata.swiss/en/dataset/ist-daten-v2"
UA = "brace (student project)"
CRAWL_DELAY_S = 10  # from data.opentransportdata.swiss/robots.txt


def list_files(session):
    html = session.get(DATASET_PAGE, timeout=30).text
    urls = re.findall(r'href="(https://data\.opentransportdata\.swiss/dataset/[^"]+/download/(\d{4}-\d{2}-\d{2})_istdaten\.csv)"', html, re.I)
    return {d: u for u, d in urls}


# Swiss columns -> common stop-event columns. Scheduled times are minute
# precision, actual/forecast times have seconds. All local Zurich time.
#
# Two things the raw files do that this has to handle:
# - some trips are listed twice, line for line. Exact copies are dropped.
# - stops a minute apart share a scheduled time, so time alone can't order them.
#   Ties are broken by the order of lines in the file, which follows the route.
TRANSFORM = """
with src as (
  select *, row_number() over () as src_line
  from read_csv(?, delim=';', header=true, all_varchar=true)
  where BETREIBER_ABK = ? and upper(PRODUKT_ID) = 'BUS'
),
src_unique as (
  select * exclude (src_line), min(src_line) as src_line from src group by all
)
select
  'ch:' || BETREIBER_ABK                                   as agency,
  strptime(BETRIEBSTAG, '%d.%m.%Y')::date                  as service_date,
  FAHRT_BEZEICHNER                                         as trip_id,
  LINIEN_TEXT                                              as route,
  BPUIC                                                    as stop_id,
  HALTESTELLEN_NAME                                        as stop_name,
  row_number() over (partition by FAHRT_BEZEICHNER
                     order by coalesce(strptime(ANKUNFTSZEIT, '%d.%m.%Y %H:%M'),
                                       strptime(ABFAHRTSZEIT, '%d.%m.%Y %H:%M')), src_line)
                                                           as stop_seq,
  strptime(ANKUNFTSZEIT, '%d.%m.%Y %H:%M')                 as sched_arr,
  strptime(ABFAHRTSZEIT, '%d.%m.%Y %H:%M')                 as sched_dep,
  try_strptime(AN_PROGNOSE, '%d.%m.%Y %H:%M:%S')           as actual_arr,
  try_strptime(AB_PROGNOSE, '%d.%m.%Y %H:%M:%S')           as actual_dep,
  AN_PROGNOSE_STATUS                                       as arr_status,
  AB_PROGNOSE_STATUS                                       as dep_status,
  FAELLT_AUS_TF = 'true'                                   as cancelled,
  ZUSATZFAHRT_TF = 'true'                                  as extra_trip,
  DURCHFAHRT_TF = 'true'                                   as pass_through
from src_unique
"""

REQUIRED = ["BETRIEBSTAG", "FAHRT_BEZEICHNER", "BETREIBER_ABK", "PRODUKT_ID", "LINIEN_TEXT", "BPUIC",
            "HALTESTELLEN_NAME", "ANKUNFTSZEIT", "AN_PROGNOSE", "AN_PROGNOSE_STATUS", "ABFAHRTSZEIT",
            "AB_PROGNOSE", "AB_PROGNOSE_STATUS", "FAELLT_AUS_TF", "ZUSATZFAHRT_TF", "DURCHFAHRT_TF"]


def convert(csv_path, out_path, operator):
    # writes the whole day or nothing: a failed run leaves the previous file in place
    con = duckdb.connect()
    cols = [r[0] for r in con.execute(
        "describe select * from read_csv(?, delim=';', header=true, all_varchar=true)", [str(csv_path)]).fetchall()]
    missing = [c for c in REQUIRED if c not in cols]
    if missing:
        raise ValueError(f"{csv_path}: source file is missing columns {missing}")
    rel = con.sql(TRANSFORM, params=[str(csv_path), operator])
    tmp = out_path.with_suffix(".tmp")
    rel.write_parquet(str(tmp))
    n = con.execute("select count(*) from read_parquet(?)", [str(tmp)]).fetchone()[0]
    if n == 0:
        tmp.unlink()
        raise ValueError(f"{csv_path}: no {operator} bus rows")
    tmp.rename(out_path)  # only a finished file gets the real name
    return n


def download(session, url, dest):
    with session.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--operator", default="VBZ")
    ap.add_argument("--out", default="data/events")
    ap.add_argument("--replace", action="store_true", help="fetch again even if the day exists (corrected files)")
    args = ap.parse_args()

    out = Path(args.out) / f"ch_{args.operator.lower()}"
    out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = UA

    files = list_files(s)
    d, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    while d <= end:
        target = out / f"{d}.parquet"
        if target.exists() and not args.replace:
            print(d, "already there")
        elif str(d) not in files:
            print(d, "not on the dataset page", file=sys.stderr)
        else:
            time.sleep(CRAWL_DELAY_S)
            with tempfile.TemporaryDirectory() as tmp:
                raw = Path(tmp) / "day.csv"
                t0 = time.time()
                download(s, files[str(d)], raw)
                n = convert(raw, target, args.operator)
                print(d, f"{raw.stat().st_size >> 20} MB raw -> {n} rows, {time.time() - t0:.0f}s")
        d += timedelta(days=1)


if __name__ == "__main__":
    main()
