from hand_check import match


def obs(t, route="MERC", bus=""):
    return {"t": t, "route": route, "bus_number": bus, "stopped": "y"}


def lab(lo, hi, route="MERC", vehicle="1234"):
    return {"lo": lo, "hi": hi, "route": route, "vehicle": vehicle, "kind": "observed"}


def test_inside_and_outside():
    pairs, unmatched = match([obs(1000), obs(2000)], [lab(990, 1020), lab(2030, 2060)])
    assert [p["inside"] for p in pairs] == [True, False] or [p["inside"] for p in pairs] == [False, True]
    outside = [p for p in pairs if not p["inside"]][0]
    assert outside["outside_by_s"] == -30
    assert unmatched == []


def test_route_must_match():
    pairs, unmatched = match([obs(1000, route="EART")], [lab(990, 1020, route="MERC")])
    assert pairs == []
    assert len(unmatched) == 1


def test_each_label_used_once():
    # two buses of the same route close together; the closer one wins each label
    pairs, unmatched = match([obs(1000), obs(1010)], [lab(995, 1005)])
    assert len(pairs) == 1
    assert len(unmatched) == 1


def test_far_apart_is_not_a_match():
    pairs, _ = match([obs(1000)], [lab(2000, 2030)])
    assert pairs == []


def test_vehicle_number():
    pairs, _ = match([obs(1000, bus="1234"), obs(5000, bus="9999")], [lab(990, 1020), lab(4990, 5020)])
    assert sorted(p["vehicle_matches"] for p in pairs) == [False, True]
