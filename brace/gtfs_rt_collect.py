# Polls the Valley Metro realtime feeds and saves every response as gzipped
# protobuf, plus one log line per fetch. Only the header gets parsed, so raw
# data is kept even if parsing breaks later.
#
# usage: VM_API_KEY=... uv run python collect.py --minutes 60 --every 30

import argparse
import gzip
import json
import os
import sys
import time
from pathlib import Path

import requests
from google.transit import gtfs_realtime_pb2

BASE = "https://mna.mecatran.com/utw/ws/gtfsfeed"
FEEDS = {"trip_updates": "realtime", "vehicles": "vehicles"}


def fetch(session, feed, key):
    url = f"{BASE}/{FEEDS[feed]}/valleymetro"
    t0 = time.time()
    rec = {"feed": feed, "fetched_at": round(t0, 3)}
    try:
        r = session.get(url, params={"apiKey": key}, timeout=20)
        rec["status"] = r.status_code
        rec["elapsed_s"] = round(time.time() - t0, 3)
        rec["bytes"] = len(r.content)
        rec["content_type"] = r.headers.get("Content-Type")
        if r.status_code != 200:
            return rec, None
        msg = gtfs_realtime_pb2.FeedMessage()
        msg.ParseFromString(r.content)
        rec["header_ts"] = msg.header.timestamp
        rec["entities"] = len(msg.entity)
        return rec, r.content
    except Exception as e:
        # network errors and non-protobuf bodies both land here; log and move on
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--every", type=float, default=30)
    ap.add_argument("--out", default="data/raw")
    args = ap.parse_args()

    key = os.environ.get("VM_API_KEY")
    if not key:
        sys.exit("set VM_API_KEY (the key published on phoenixopendata.com)")

    out = Path(args.out)
    for f in FEEDS:
        (out / f).mkdir(parents=True, exist_ok=True)
    log = open(out / "fetch_log.jsonl", "a")

    s = requests.Session()
    s.headers["User-Agent"] = "valley-eta feed check (student project)"

    end = time.time() + args.minutes * 60
    while time.time() < end:
        tick = time.time()
        for feed in FEEDS:
            rec, body = fetch(s, feed, key)
            if body is not None:
                name = f"{int(rec['fetched_at'])}.pb.gz"
                (out / feed / name).write_bytes(gzip.compress(body))
                rec["file"] = f"{feed}/{name}"
            log.write(json.dumps(rec) + "\n")
            log.flush()
        time.sleep(max(0, args.every - (time.time() - tick)))


if __name__ == "__main__":
    main()
