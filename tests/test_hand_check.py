from hand_check import match, vs_predictions


def obs(t, route="MERC", bus="", dep=None):
    return {"t": t, "t_dep": dep if dep is not None else t, "route": route, "bus_number": bus, "stopped": "y"}


def lab(lo, hi, route="MERC", vehicle="1234", trip="t1", seq="5"):
    return {"lo": lo, "hi": hi, "route": route, "vehicle": vehicle, "kind": "observed", "trip": trip, "seq": seq}


def test_inside_and_outside():
    pairs, unmatched = match([obs(1000), obs(2000)], [lab(990, 1020), lab(2030, 2060, seq="6")])
    by_arrive = {p["arrive"]: p for p in pairs}
    assert by_arrive[1000]["inside"]
    assert not by_arrive[2000]["inside"]
    assert by_arrive[2000]["outside_by_s"] == -30
    assert unmatched == []


def test_route_must_match():
    pairs, unmatched = match([obs(1000, route="EART")], [lab(990, 1020, route="MERC")])
    assert pairs == []
    assert len(unmatched) == 1


def test_each_label_used_once():
    # two buses of the same route close together; the closer one wins the label
    pairs, unmatched = match([obs(1000), obs(1010)], [lab(995, 1005)])
    assert len(pairs) == 1
    assert len(unmatched) == 1


def test_far_apart_is_not_a_match():
    pairs, _ = match([obs(1000)], [lab(2000, 2030)])
    assert pairs == []


def test_vehicle_number():
    pairs, _ = match([obs(1000, bus="1234"), obs(5000, bus="9999")], [lab(990, 1020), lab(4990, 5020, seq="6")])
    assert sorted(p["vehicle_matches"] for p in pairs) == [False, True]


def test_prediction_closer_to_departure():
    # bus pulls in at 1000 and waits until 1120; the feed kept predicting 1110
    pairs, _ = match([obs(1000, dep=1120)], [lab(990, 1020)])
    preds = {("t1", 5): [(1000 - 420, 1110, "s5"), (1000 - 360, 1110, "s5")]}
    got = vs_predictions(pairs, preds)["5-10 min"]
    assert got["n"] == 2
    assert got["median_pred_minus_arrive_s"] == 110
    assert got["median_pred_minus_depart_s"] == -10
    assert got["share_arrived_over_60s_early"] == 1.0
    assert got["share_departed_over_60s_early"] == 0.0
