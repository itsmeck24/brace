# Label logic on hand-made vehicle reports. No network, no mocks: these call the
# real functions with small inputs.

from check_feed import build_labels, seg_dist


def rep(ts, seq, status, lat=33.4, lon=-112.0):
    return {"vehicle": "v1", "ts": ts, "trip": "t1", "seq": seq, "stop": f"s{seq}",
            "status": status, "lat": lat, "lon": lon, "fetched": ts}


def run(rows):
    labels, skipped = build_labels(rows, stops={}, trip_ids={"t1"})
    return {(l["seq"], l["kind"]): (l["lo"], l["hi"]) for l in labels}, skipped


def test_stopped_at_gives_observed_bracket():
    got, _ = run([rep(100, 5, "INCOMING_AT"), rep(130, 5, "STOPPED_AT")])
    assert got == {(5, "observed"): (100, 130)}


def test_staying_stopped_does_not_relabel():
    got, _ = run([rep(100, 5, "INCOMING_AT"), rep(130, 5, "STOPPED_AT"), rep(160, 5, "STOPPED_AT")])
    assert list(got) == [(5, "observed")]
    assert got[(5, "observed")] == (100, 130)


def test_passing_stops_without_stopping():
    # was heading to 5, now heading to 7: it passed 5 and 6 in between
    got, _ = run([rep(100, 5, "INCOMING_AT"), rep(140, 7, "IN_TRANSIT_TO")])
    assert got == {(5, "inferred_seq"): (100, 140), (6, "inferred_seq"): (100, 140)}


def test_leaving_a_stop_it_was_stopped_at_is_not_a_new_arrival():
    got, _ = run([rep(100, 5, "INCOMING_AT"), rep(130, 5, "STOPPED_AT"), rep(160, 6, "IN_TRANSIT_TO")])
    assert list(got) == [(5, "observed")]


def test_big_jump_is_skipped():
    got, skipped = run([rep(100, 1, "INCOMING_AT"), rep(130, 110, "IN_TRANSIT_TO")])
    assert got == {}
    assert skipped == 1


def test_reports_out_of_order_are_sorted():
    got, _ = run([rep(130, 5, "STOPPED_AT"), rep(100, 5, "INCOMING_AT")])
    assert got == {(5, "observed"): (100, 130)}


def test_seg_dist():
    stop = (33.4, -112.0)
    # stop sits on the line between the two reports
    assert seg_dist(stop, (33.399, -112.0), (33.401, -112.0)) < 1
    # ~100 m east of a north-south line
    d = seg_dist((33.4, -111.99893), (33.399, -112.0), (33.401, -112.0))
    assert 90 < d < 110
    # past the end of the segment: distance to the nearest end, not the infinite line
    d = seg_dist((33.403, -112.0), (33.399, -112.0), (33.401, -112.0))
    assert 200 < d < 250
