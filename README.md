# valley-eta

How much should you trust a Valley Metro bus arrival estimate at your stop right now?

The app might say a bus is 4 minutes away when it actually shows up in 11. This project measures
how wrong those estimates are, by stop, by time of day and by how far ahead they were made.

## Status

Feed check only. Before building anything else, this stage answers one question: can we get
credible "the bus actually arrived at this time" labels from the public feed? If not, the project
becomes a feed-quality monitor with no model.

Nothing here is a result yet. Measured numbers will go in `results/` with the command, commit and
environment that produced them.

## What the feed gives you

- Trip updates and vehicle positions, protobuf, buses only. Light rail and streetcar are not in the
  realtime feed. [Phoenix Open Data](https://phoenixopendata.com/dataset/general-transit-feed-specification)
- Trip updates drop each stop once the bus passes it, so the feed never states an actual arrival.
  Labels have to come from vehicle positions.
- A label is a time window between two vehicle reports: the last one before the stop and the first
  one at or past it. A report saying `STOPPED_AT` gives an `observed` label. The stop counter moving
  past the stop gives an `inferred_seq` label. Each label is checked against GPS: the stop should be
  near the line between the two positions.
- Two feed problems get filtered out and counted: the stop counter jumping many stops at once, and
  the counter moving while the GPS position stays exactly the same.

## Run it

Needs Python 3.13 and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run pytest -q
```

Collect for an hour, then check what was collected. The API key is the shared one published in the
resource URLs on the Phoenix Open Data page above.

```
curl -L -o googletransit.zip "https://www.phoenixopendata.com/dataset/3eae9a4a-98b9-40c8-8df7-8c00c1756235/resource/28ccc0a5-49c8-495c-b91f-193de5ce2cb7/download/googletransit.zip"
mkdir -p data/static && unzip -q googletransit.zip -d data/static/gtfs
VM_API_KEY=... uv run python collect.py --minutes 60 --every 30 --out data/raw
uv run python check_feed.py --raw data/raw --gtfs data/static/gtfs --out results/feed_check
```

Tests call the real label and matching functions on small hand-made inputs. Nothing is mocked, and
nothing touches the network.

## Hand check

The GPS check only shows that labels are self-consistent. To check them against reality, someone
stands at a stop and writes down when each bus arrives.

Stop: 5th St & Myrtle Av westbound in Tempe, sign code 12507, `stop_id` 5324. The weekday schedule
has 25 buses there between 3 and 4 pm on routes 48, 62, 66 and the Orbit routes Mercury, Earth,
Venus and Jupiter.

1. Start `collect.py` about 10 minutes before arriving and keep it running until 10 minutes after.
2. Stay 30 to 45 minutes. For each westbound bus, write `date,route,bus_number,arrive,depart,stopped`
   to `data/handcheck/observed.csv`. Times are the phone clock to the second: `arrive` when the bus
   stops with doors open, `depart` when it starts moving again. If it passes without stopping, put
   the moment the front passes the pole in `arrive` and leave `depart` empty. Route is as shown on
   the sign (48, MERC, EART, ...).
3. Run `check_feed.py` on that collection, then
   `uv run python hand_check.py --obs data/handcheck/observed.csv --labels results/handcheck/labels.csv --raw data/handcheck/raw --stop 5324`.

Pass bar, set before the visit: at least 10 matched arrivals, at least 80% of them inside their
label window, and none more than 60 s outside. It is still unconfirmed whether the feed's vehicle id
is the fleet number painted on the bus. Writing bus numbers down checks that.

The departure times answer a second question. In the first 14 minutes of feed data, buses seemed
to reach stops earlier than predicted surprisingly often, mostly at timepoint stops where early
buses wait. If the feed's predicted time is really the departure time, the prediction should be
closer to `depart` than to `arrive`. `hand_check.py` reports both.

This covers one stop, one direction and one afternoon. It says nothing about long dwells, early
morning service or other routes.

## Data and license

- Realtime feed: Creative Commons Attribution, per the
  [dataset page](https://phoenixopendata.com/dataset/general-transit-feed-specification).
  Transitland lists it as CC-BY-4.0. [Transitland](https://www.transit.land/feeds/f-valleymetro~rt)
- Static schedule: Open Data Commons Attribution, per the
  [dataset page](https://phoenixopendata.com/dataset/valley-metro-bus-schedule).
- The portal's [Terms of Use](https://phoenixopendata.com/pages/terms-of-use) (read 2026-11-11)
  allow use, reproduction and redistribution of the data. They also say the data comes as is, can
  stop at any time, and may not be used with City of Phoenix trademarks. Users agree to indemnify the
  City. They say nothing about polling rates or automated access.
- This is an unofficial project. It is not endorsed by, sponsored by or affiliated with the City of
  Phoenix or Valley Metro, and its views are not theirs.
- Raw feed data is not committed. There is no free historical archive (Transitland's needs a
  subscription), so data has to be collected.
- The collector polls every 30 s. No rate limit is published.

## Related work

Arrival-time uncertainty for buses is well studied, and nothing here is new. The closest approach
is O'Sullivan et al., IEEE T-ITS 2016, which fits quantile regression on an existing predictor's
errors. [PDF](https://discovery-pp.ucl.ac.uk/1502220/1/QRbus.pdf) Not yet read in full.
