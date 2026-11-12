# What happens when ingest goes wrong. Uses a real 144-line excerpt of the
# 2026-11-11 Ist-Daten file (3 VBZ line 31 trips, 20 SBB train rows, 20 rows
# from other bus operators). Each test breaks something on purpose, then checks
# the output against a clean conversion of the same file. Nothing is mocked
# except the one crash, which is forced by making the final rename fail.

import os
import subprocess
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from brace.evaluate import require_days
from brace.swiss import convert

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "tests/fixtures/raw/2026-11-11_istdaten_excerpt.csv"
LINES = RAW.read_text(encoding="utf-8").splitlines(keepends=True)
HEADER, BODY = LINES[0], LINES[1:]


def write_raw(path, header=HEADER, body=BODY):
    path.write_text(header + "".join(body), encoding="utf-8")
    return path


def read(p):
    return pd.read_parquet(p).sort_values(["trip_id", "stop_seq"]).reset_index(drop=True)


def crash(self, other):
    raise OSError("killed before rename")


@pytest.fixture
def clean(tmp_path):
    out = tmp_path / "clean" / "ch_vbz"
    out.mkdir(parents=True)
    convert(RAW, out / "2026-11-11.parquet", "VBZ")
    return read(out / "2026-11-11.parquet")


def test_filter_keeps_only_operator_buses(clean):
    assert len(clean) == 104 and clean["trip_id"].nunique() == 3
    assert (clean["agency"] == "ch:VBZ").all() and (clean["route"] == "31").all()


def test_duplicated_lines_are_dropped(tmp_path, clean):
    vbz = [l for l in BODY if ";VBZ;" in l]
    raw = write_raw(tmp_path / "dup.csv", body=BODY + vbz)  # every VBZ line twice
    convert(raw, tmp_path / "d.parquet", "VBZ")
    pd.testing.assert_frame_equal(read(tmp_path / "d.parquet"), clean)


def test_same_day_imported_twice(tmp_path, clean):
    target = tmp_path / "ch_vbz" / "2026-11-11.parquet"
    target.parent.mkdir()
    convert(RAW, target, "VBZ")
    convert(RAW, target, "VBZ")
    assert len(list(target.parent.glob("*.parquet"))) == 1
    pd.testing.assert_frame_equal(read(target), clean)


def test_crash_before_publish_keeps_previous_file(tmp_path, clean, monkeypatch):
    target = tmp_path / "2026-11-11.parquet"
    convert(RAW, target, "VBZ")
    corrected = write_raw(tmp_path / "fix.csv", body=[l.replace(";REAL;", ";PROGNOSE;", 1) for l in BODY])

    monkeypatch.setattr(Path, "rename", crash)
    with pytest.raises(OSError):
        convert(corrected, target, "VBZ")
    monkeypatch.undo()
    # readers only see *.parquet: the half-done run left nothing they would pick up
    assert [p.name for p in tmp_path.glob("*.parquet")] == ["2026-11-11.parquet"]
    pd.testing.assert_frame_equal(read(target), clean)
    convert(corrected, target, "VBZ")  # rerun after the crash
    assert not read(target).equals(clean)


def test_late_correction_replaces_the_day(tmp_path, clean):
    target = tmp_path / "2026-11-11.parquet"
    convert(RAW, target, "VBZ")
    i = next(i for i, l in enumerate(BODY) if ";VBZ;" in l and ";REAL;" in l and l.split(";")[18])
    old = BODY[i].split(";")
    new = old.copy()
    new[18] = new[18][:-2] + ("00" if new[18][-2:] != "00" else "30")  # move the actual departure
    body = BODY.copy()
    body[i] = ";".join(new)
    convert(write_raw(tmp_path / "late.csv", body=body), target, "VBZ")
    after = read(target)
    changed = (after["actual_dep"] != clean["actual_dep"]) & clean["actual_dep"].notna()
    assert len(after) == len(clean) and changed.sum() == 1


def test_bad_schema_fails_and_keeps_last_good_file(tmp_path, clean):
    target = tmp_path / "2026-11-11.parquet"
    convert(RAW, target, "VBZ")
    bad = write_raw(tmp_path / "bad.csv", header=HEADER.replace("AB_PROGNOSE;", "AB_PROGNOSE_NEU;", 1))
    with pytest.raises(ValueError, match="missing columns.*AB_PROGNOSE"):
        convert(bad, target, "VBZ")
    pd.testing.assert_frame_equal(read(target), clean)


def test_empty_day_is_not_published(tmp_path):
    with pytest.raises(ValueError, match="no VBZ bus rows"):
        convert(write_raw(tmp_path / "empty.csv", body=[l for l in BODY if ";VBZ;" not in l]),
                tmp_path / "e.parquet", "VBZ")
    assert not (tmp_path / "e.parquet").exists()


def test_missing_day_is_refused():
    df = pd.DataFrame({"service_date": pd.to_datetime(["2026-11-01", "2026-11-03"]).date})
    with pytest.raises(SystemExit, match="2026-11-02"):
        require_days(df, ("2026-11-01", "2026-11-03"), "train")


def dbt_tables(events_dir, db):
    env = {**os.environ, "BRACE_DB": str(db)}
    subprocess.run(["dbt", "build", "--project-dir", "dbt", "--profiles-dir", "dbt", "--quiet",
                    "--vars", f'{{events_glob: "{events_dir}/*/*.parquet"}}'], cwd=ROOT, env=env, check=True)
    with duckdb.connect(str(db), read_only=True) as con:
        return {t: con.execute(f"select * from {t} order by all").df() for t in ("stop_events_clean", "training_rows")}


def test_recovered_pipeline_matches_clean_run(tmp_path, monkeypatch):
    # clean: one good import. faulty: duplicated file, a crash, then a rerun.
    good = tmp_path / "good" / "ch_vbz"
    good.mkdir(parents=True)
    convert(RAW, good / "2026-11-11.parquet", "VBZ")

    bad = tmp_path / "bad" / "ch_vbz"
    bad.mkdir(parents=True)
    target = bad / "2026-11-11.parquet"
    convert(write_raw(tmp_path / "dup.csv", body=BODY + BODY), target, "VBZ")
    monkeypatch.setattr(Path, "rename", crash)
    with pytest.raises(OSError):
        convert(RAW, target, "VBZ")
    monkeypatch.undo()
    convert(RAW, target, "VBZ")

    a = dbt_tables(tmp_path / "good", tmp_path / "good.duckdb")
    b = dbt_tables(tmp_path / "bad", tmp_path / "bad.duckdb")
    assert len(a["training_rows"]) > 0
    for t in a:
        pd.testing.assert_frame_equal(a[t], b[t])


def test_same_minute_stops_follow_file_order(tmp_path):
    # stops a minute apart often share a scheduled time; then the file's line order decides
    body = BODY.copy()
    i = next(i for i, l in enumerate(body) if ";VBZ;" in l and l.split(";")[14]) + 1  # 2nd stop with an arrival
    a, b = body[i].split(";"), body[i + 1].split(";")
    b[14] = a[14]  # same scheduled arrival minute
    body[i + 1] = ";".join(b)
    tied = write_raw(tmp_path / "tied.csv", body=body)
    body[i], body[i + 1] = body[i + 1], body[i]
    swapped = write_raw(tmp_path / "swapped.csv", body=body)
    convert(tied, tmp_path / "t.parquet", "VBZ")
    convert(swapped, tmp_path / "s.parquet", "VBZ")
    t, s = read(tmp_path / "t.parquet"), read(tmp_path / "s.parquet")
    t, s = (df[df["trip_id"] == a[1]].set_index("stop_id")["stop_seq"] for df in (t, s))
    assert t[a[12]] < t[b[12]] and s[b[12]] < s[a[12]]
