import pytest

from hub import context


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(context, "STATE", tmp_path / "context.json")
    monkeypatch.setattr(context.settings, "get", lambda key: "")
    context._events.clear()


def test_recent_events_count_without_a_reference():
    context.record("c", "turn on the corner lamp", "turned corner lamp on", device="left", now=1000)
    assert [e["said"] for e in context.related("c", "what time is it", now=1060)] == ["turn on the corner lamp"]


def test_older_events_need_a_reference_and_expire():
    context.record("c", "turn on the corner lamp", "turned corner lamp on", device="left", now=1000)
    assert context.related("c", "how is the weather", now=1300) == []
    assert context.last_device("c", "dim it a bit", now=1300)["device"] == "left"
    assert context.last_device("c", "dim it a bit", now=1000 + 601) is None


def test_danish_references_count():
    context.record("c", "tænd lampen", "turned all lights on", device="all", now=1000)
    assert context.last_device("c", "lidt mørkere", now=1200)["device"] == "all"


def test_card_stays_small_and_says_how_long_ago():
    for i in range(10):
        context.record("c", "x" * 300, "did " + "y" * 300, device="left", now=1000 + i)
    lamps = [{"id": "left", "name": "corner lamp", "on": True, "bri": 127}, {"id": "up", "name": "ceiling panel", "on": False, "bri": 1}]
    card, labels = context.card(context.recent("c", now=1030), lamps)
    assert len(card) <= context.CARD_LIMIT
    assert "corner lamp on 50%" in card and "ceiling panel off" in card and "s ago" in card
    assert labels and labels[0] == "e1" and all(label in card for label in labels)


def test_jev_sees_hours_old_events_with_their_age():
    context.record("c", "find the tax document", "used brain_search: tax", now=1000)
    context.record("c", "lights off", "turned all lights off", device="all", now=1000 + 3 * 3600)
    card, labels = context.card(context.recent("c", now=1000 + 3 * 3600 + 30), None)
    assert labels == ["e1", "e2"] and "30 s ago" in card and "3 h ago" in card


def test_events_past_the_horizon_are_left_out():
    context.record("c", "old thing", "answered", now=1000)
    card, labels = context.card(context.recent("c", now=1000 + 7 * 3600), None)
    assert labels == [] and "No recent events" in card


def test_events_survive_a_restart():
    context.record("c", "lights off", "turned all lights off", device="all", now=1000)
    context._events.clear()
    context._load()
    assert context._events["c"][0]["device"] == "all"


def test_age_words():
    assert context.age_words(20) == "20 s ago"
    assert context.age_words(150) == "2 min ago"
    assert context.age_words(7300) == "2 h ago"
    assert context.age_words(3 * 86400) == "3 d ago"


def test_dim_leaves_dark_lamps_dark_while_one_is_lit(monkeypatch):
    from hub import brains
    calls = []
    monkeypatch.setattr(brains.hue, "hue_light", lambda *a, **k: calls.append((a, k)) or "ok")
    lamps = [{"id": "left", "name": "corner lamp", "on": False, "bri": 200}, {"id": "up", "name": "ceiling panel", "on": True, "bri": 127}]
    result, did = brains.apply_light({"action": "dim", "amount": "normal"}, "all", lamps)
    assert calls == [(("on", "up"), {"brightness": 25})] and did == "dimmed the ceiling panel to 25%"
    calls.clear()
    result, did = brains.apply_light({"action": "dim", "amount": "small"}, "left", lamps)
    assert calls == [(("on", "left"), {"brightness": 35})] and did == "turned the corner lamp on dimmed to 35%"


def test_pick_event_trusts_jev_and_falls_back_to_the_time_rule():
    from hub import brains
    context.record("c", "turn on the corner lamp", "turned the corner lamp on", device="left", now=1000)
    context.record("c", "hi", "answered: hello", now=1010)
    events = context.recent("c", now=1020)
    labels = ["e1", "e2"]
    assert brains.pick_event("c", "dim it", events, labels, {"refers_to": "e2", "refers_to_confidence": 0.9})[0]["device"] == "left"
    assert brains.pick_event("c", "find that doc", events, labels, {"refers_to": "earlier", "refers_to_confidence": 0.9}) == (None, True)
    assert brains.pick_event("c", "hello", events, labels, {"refers_to": "none", "refers_to_confidence": 0.9}) == (None, False)
    assert brains.pick_event("c", "that doc from before", events, labels, {"refers_to": "earlier", "refers_to_confidence": 0.33}) == (None, True)
