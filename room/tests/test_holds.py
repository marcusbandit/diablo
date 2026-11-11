from room import color
from room.holds import Holds, agrees
from room.lamps import OFF, Target

WARM = Target(True, 70, mirek=color.mirek(3000))


def test_echo_of_own_command_is_not_a_hold():
    h = Holds()
    h.note_sent("a", WARM, 100.0)
    assert h.on_update("a", {"dimming": {"brightness": 70.06}}, 100.4, "everyday") is None
    assert not h.held


def test_late_update_that_matches_is_not_a_hold():
    h = Holds()
    h.note_sent("a", WARM, 100.0)
    assert h.on_update("a", {"dimming": {"brightness": 70.2}}, 110.0, "everyday") is None


def test_manual_dim_becomes_a_hold():
    h = Holds()
    h.note_sent("a", WARM, 100.0)
    hold = h.on_update("a", {"dimming": {"brightness": 20.0}}, 200.0, "everyday")
    assert hold and hold.what == "set to 20 %" and "a" in h.held


def test_manual_off_becomes_a_hold():
    h = Holds()
    h.note_sent("a", WARM, 100.0)
    assert h.on_update("a", {"on": {"on": False}}, 200.0, "focus").what == "turned off"


def test_untouched_lamp_cannot_be_held():
    assert Holds().on_update("a", {"on": {"on": False}}, 200.0, "focus") is None


def test_hold_lasts_until_activity_changes_and_time_passes():
    h = Holds()
    h.note_sent("a", WARM, 0.0)
    h.on_update("a", {"on": {"on": False}}, 100.0, "focus")
    assert h.release("focus", 10_000.0, 10) == []  # same activity: stays yours
    assert h.release("cinema", 200.0, 10) == []  # changed, but only 100 s in
    assert h.release("cinema", 701.0, 10) == ["a"]
    assert not h.held


def test_agrees_fields():
    assert agrees(OFF, {"on": {"on": False}})
    assert not agrees(OFF, {"on": {"on": True}})
    assert agrees(WARM, {"dynamics": {"status": "none"}})  # status noise is no takeover
    assert not agrees(WARM, {"color_temperature": {"mirek": 200, "mirek_valid": True}})
    assert agrees(WARM, {"color_temperature": {"mirek": WARM.mirek + 3, "mirek_valid": True}})
    amber = Target(True, 9, xy=color.hex_to_xy("#ff7a2e"))
    assert agrees(amber, {"color": {"xy": {"x": amber.xy[0] + 0.01, "y": amber.xy[1]}}})
    assert not agrees(amber, {"color": {"xy": {"x": 0.17, "y": 0.05}}})
    assert not agrees(WARM, {"color": {"xy": {"x": 0.17, "y": 0.05}}})  # someone picked blue


def test_off_fade_noise_is_not_a_hold():
    h = Holds()
    h.note_sent("p", OFF, 100.0)
    assert h.on_update("p", {"on": {"on": False}}, 100.3, "cinema") is None
    assert h.on_update("p", {"dimming": {"brightness": 46.64}}, 101.5, "cinema") is None
    assert h.on_update("p", {"dimming": {"brightness": 100.0}}, 107.8, "cinema") is None
    assert h.on_update("p", {"on": {"on": True}}, 130.0, "cinema").what == "turned on"


def test_white_light_reports_xy_too():
    cool = Target(True, 100, mirek=153)
    # setting 6500 K, the bridge reports the white point's xy with mirek_valid
    assert agrees(cool, {"color": {"xy": {"x": 0.3131, "y": 0.3232}}, "color_temperature": {"mirek": 153, "mirek_valid": True}})
    assert not agrees(cool, {"color": {"xy": {"x": 0.6, "y": 0.35}}, "color_temperature": {"mirek": None, "mirek_valid": False}})
    amber = Target(True, 9, xy=(0.6176, 0.3471))
    assert not agrees(amber, {"color_temperature": {"mirek": 300, "mirek_valid": True}})


def test_long_fade_updates_still_match_the_last_command():
    # review: bedtime fades over 60 s; updates late in the fade must not read as a person
    h = Holds()
    h.note_sent("a", OFF, 0.0)
    assert h.on_update("a", {"dimming": {"brightness": 40.0}}, 45.0, "bedtime") is None
    assert h.on_update("a", {"on": {"on": False}}, 60.0, "bedtime") is None
    night = Target(True, 55, mirek=455)
    h.note_sent("b", night, 0.0)
    assert h.on_update("b", {"dimming": {"brightness": 55.1}}, 19.0, "focus") is None


def test_hold_ends_after_switching_away_and_back():
    # review: a hold must not live forever when the activity comes back before manual_min
    h = Holds()
    h.note_sent("a", WARM, 0.0)
    h.on_update("a", {"dimming": {"brightness": 20.0}}, 10.0, "focus")
    assert h.release("everyday", 300.0, 10) == []
    assert h.release("focus", 700.0, 10) == ["a"]
