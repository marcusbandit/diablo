from datetime import datetime

import pytest

from room import color, config, lamps
from room.lamps import Lamp, Target

CFG = config.DEFAULTS
PANEL = Lamp("p", "Hue Surimu panel", "ceiling_square", True, 153, 500, 0.1)
CANDLE = Lamp("c", "Wood lamp", "flexible_lamp", True, 153, 500, 0.2)
FILAMENT = Lamp("f", "The corner", "vintage_bulb", False, 222, 454, 2.0)
ROOM = [PANEL, CANDLE, FILAMENT]


def at(h, m=0):
    return datetime(2026, 11, 5 if h >= 12 else 6, h, m)


@pytest.mark.parametrize("when,expected", [
    (at(18), 0.0), (at(22, 59), 0.0), (at(23), 0.0), (at(0, 30), 0.5), (at(2), 1.0),
    (at(4), 1.0), (at(6, 59), 1.0), (at(7), 0.0), (at(12), 0.0),
])
def test_night_factor(when, expected):
    assert lamps.night_factor(when, CFG["night"]) == pytest.approx(expected, abs=0.01)


def test_cinema_turns_the_ceiling_off_and_colours_the_candle():
    t = lamps.targets("cinema", CFG, ROOM)
    assert t["p"] == lamps.OFF
    assert t["c"].xy == color.hex_to_xy("#ff7a2e") and t["c"].bri == 9
    assert t["f"].on and t["f"].bri == 4 and t["f"].mirek == color.mirek(2200)


def test_focus_is_bright_and_cool():
    t = lamps.targets("focus", CFG, ROOM)
    assert all(x.on and x.bri == 100 for x in t.values())
    assert t["p"].mirek == color.mirek(4300)


def test_night_warms_and_caps_white():
    day = lamps.targets("focus", CFG, ROOM, 0.0)["p"]
    night = lamps.targets("focus", CFG, ROOM, 1.0)["p"]
    half = lamps.targets("focus", CFG, ROOM, 0.5)["p"]
    assert night.mirek == color.mirek(2200) and night.bri == 55
    assert day.mirek < half.mirek < night.mirek
    assert half.bri == pytest.approx(77.5)


def test_night_leaves_colours_but_caps_them():
    cfg = config.merge(CFG, {"scenes": {"cinema": {"color_bri": 80}}})
    t = lamps.targets("cinema", cfg, ROOM, 1.0)["c"]
    assert t.xy == color.hex_to_xy("#ff7a2e") and t.bri == 55


def test_per_lamp_override_beats_the_off_list():
    cfg = config.merge(CFG, {"scenes": {"cinema": {"lamps": {"Hue Surimu panel": {"on": True, "bri": 2, "k": 2000}}}}})
    t = lamps.targets("cinema", cfg, ROOM)["p"]
    assert t.on and t.bri == 2


def test_away_touches_nothing_and_bedtime_turns_off():
    assert lamps.targets("away", CFG, ROOM) == {}
    assert all(t == lamps.OFF for t in lamps.targets("bedtime", CFG, ROOM).values())


def test_body_clamps_to_the_lamp():
    b = lamps.body(Target(True, 1, mirek=500), FILAMENT, 800)
    assert b["dimming"]["brightness"] == 2.0
    assert b["color_temperature"]["mirek"] == 454
    assert b["dynamics"]["duration"] == 800
    assert "color" not in lamps.body(Target(True, 50, xy=(0.5, 0.4)), FILAMENT)
    assert lamps.body(lamps.OFF, PANEL) == {"on": {"on": False}}


def test_pick_lamps():
    assert lamps.pick(ROOM, {"lamps": ["Wood lamp"]}) == [CANDLE]
    assert lamps.pick(ROOM, {"lamps": []}, {"p", "f"}) == [PANEL, FILAMENT]
    assert lamps.pick(ROOM, {}) == ROOM


def test_bedtime_window():
    assert lamps.in_window(at(1, 30), "01:00", "09:00")
    assert not lamps.in_window(at(23), "01:00", "09:00")
    assert not lamps.in_window(at(10), "01:00", "09:00")


def test_target_key_ignores_tiny_drift():
    assert Target(True, 50.2, 300).key() == Target(True, 49.8, 301).key()
    assert Target(True, 50, 300).key() != Target(True, 50, 310).key()


def test_from_v2():
    d = {"id": "x", "metadata": {"name": "Lamp", "archetype": "ceiling_round"}, "color": {},
         "color_temperature": {"mirek_schema": {"mirek_minimum": 153, "mirek_maximum": 500}},
         "dimming": {"brightness": 10, "min_dim_level": 0.5}}
    lamp = Lamp.from_v2(d)
    assert lamp.has_color and lamp.mirek_min == 153 and lamp.min_dim == 0.5 and lamp.matches(["ceiling_*"])


def test_clamp_matches_what_the_bridge_echoes():
    # review: a lamp whose range starts above the target echoes the clamped value
    narrow = Lamp("n", "Narrow", "classic_bulb", False, 250, 454, 1.0)
    t = lamps.clamp(Target(True, 0.5, mirek=233), narrow)
    assert t.mirek == 250 and t.bri == 1.0
    assert lamps.clamp(Target(True, 50, xy=(0.5, 0.4)), narrow).xy is None
    assert lamps.body(Target(True, 0.5, mirek=233), narrow)["color_temperature"]["mirek"] == 250


def test_choose_takes_the_named_room():
    raw = [{"id": i, "metadata": {"name": n, "archetype": "classic_bulb"}, "dimming": {}} for i, n in
           (("1", "A"), ("2", "B"), ("3", "C"))]
    rooms = [("Kitchen", {"3"}), ("Bedroom", {"1", "2"})]
    everyone, chosen = lamps.choose(raw, rooms, {"room": "Bedroom", "lamps": []})
    assert len(everyone) == 3 and {lamp.id for lamp in chosen} == {"1", "2"}
    assert lamps.choose(raw, rooms, {"room": "Nowhere", "lamps": []})[1] == []
