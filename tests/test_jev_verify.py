import asyncio
import subprocess
from unittest import mock

import pytest

from hub import boot, brains, context, route
from voice import commands


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    store = {"boot.host": "deskbox"}
    monkeypatch.setattr(route.settings, "get", lambda key: store.get(key, ""))
    route._last.clear()
    return store


@pytest.fixture(autouse=True)
def quiet_context(monkeypatch):
    monkeypatch.setattr(context, "record", lambda *a, **k: None)
    monkeypatch.setattr(context, "answered", lambda *a, **k: None)


@pytest.mark.parametrize("line", [
    "boot my pc", "Boot my PC.", "wake deskbox", "turn on my computer", "turn my pc on",
    "can you start my desktop please", "hey diablo, boot up the desktop", "power on the machine",
    "tænd min computer", "tænd for pc'en", "start computeren", "kan du starte min pc",
])
def test_boot_lines_are_a_direct_boot(line):
    assert route.boot_command(line), line
    assert route.route(line).direct == {"kind": "boot"}


@pytest.mark.parametrize("line", [
    "start the desktop app", "is my pc on?", "turn off my pc", "start a thread on my computer",
    "boot my pc and open spotify", "wake up", "wake otherbox", "turn on the lights",
])
def test_lines_that_are_not_one_boot_order_stay_off_it(line):
    assert not route.boot_command(line), line
    assert (route.route(line).direct or {}).get("kind") != "boot"


def jev_says(action="boot_pc", confidence=0.95):
    return {"tier": "fast", "tier_confidence": 0.9, "action": action, "action_confidence": confidence,
            "target": "all", "target_confidence": 0.9, "amount": "normal", "color": "none", "color_confidence": 0.9,
            "work": "none", "work_confidence": 0.9}


def test_jev_sure_of_a_boot_on_a_short_line_naming_the_machine_boots():
    assert route.route("get deskbox going for me", jev=jev_says()).direct == {"kind": "boot"}
    assert route.route("get deskbox going for me", jev=jev_says(confidence=0.6)).direct is None
    assert route.route("get it going for me", jev=jev_says()).direct is None
    assert route.route("boot my pc", images=True).direct is None


def test_no_host_set_means_no_host_noun_and_no_boot(fake_settings):
    fake_settings.pop("boot.host")
    with mock.patch.object(boot.localenv, "get", return_value=""):
        assert route.boot_command("boot my pc")
        assert not route.boot_command("wake deskbox")
        assert boot.start() == "no_host"


def test_already_up_sends_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(boot, "_run", lambda argv, timeout: (calls.append(argv) or (True, "pong from deskbox")))
    assert boot.start() == "up"
    assert all(argv[0] == "tailscale" for argv in calls)


def test_down_sends_the_packet_to_the_mac_from_settings(monkeypatch, fake_settings):
    fake_settings["fleet.deskbox.mac"] = "00:00:5e:00:53:01"
    fake_settings["network.home_subnet"] = "192.0.2.0/24"
    calls = []

    def run(argv, timeout):
        calls.append(argv)
        return (False, "no reply") if argv[0] == "tailscale" else (True, "")
    monkeypatch.setattr(boot, "_run", run)
    assert boot.start() == "sent"
    assert ["wakeonlan", "00:00:5e:00:53:01"] in calls
    assert ["wakeonlan", "-i", "192.0.2.255", "00:00:5e:00:53:01"] in calls


def test_no_mac_means_no_packet(monkeypatch):
    monkeypatch.setattr(boot, "_run", lambda argv, timeout: (False, ""))
    with mock.patch.object(boot.localenv, "get", return_value=""):
        assert boot.start() == "no_packet"


def test_a_ping_that_errors_counts_as_down(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("tailscale", 8)
    monkeypatch.setattr(boot.subprocess, "run", boom)
    assert boot.is_up() is False


def test_wait_up_polls_until_it_answers_and_gives_up_at_the_limit(monkeypatch):
    now = [0.0]
    answers = iter([False, False, True])
    monkeypatch.setattr(boot, "is_up", lambda name=None: next(answers))
    took = boot.wait_up(limit=120, poll=5, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    assert took == 10
    now[0] = 0.0
    monkeypatch.setattr(boot, "is_up", lambda name=None: False)
    assert boot.wait_up(limit=20, poll=5, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s)) is None
    assert "did not come up within" in boot.outcome_line(None, 120)
    assert "is up" in boot.outcome_line(42)


def test_run_boot_already_up_says_so_and_checks_nothing_later(monkeypatch):
    monkeypatch.setattr(boot, "start", lambda: "up")
    started = []
    monkeypatch.setattr(brains, "background", lambda coro: started.append(coro))
    turn = asyncio.run(brains.run_boot("main", "boot my pc"))
    assert "already up" in turn.text and turn.backend == "direct" and not started


def test_run_boot_sent_tells_marcus_how_it_went(monkeypatch):
    from hub import deliver

    monkeypatch.setattr(boot, "start", lambda: "sent")
    told = []

    async def fake_deliver(text, **kwargs):
        told.append(text)
    monkeypatch.setattr(deliver, "deliver", fake_deliver)

    async def go(seconds):
        monkeypatch.setattr(boot, "wait_up", lambda: seconds)
        started = []
        monkeypatch.setattr(brains, "background", lambda coro: started.append(coro))
        turn = await brains.run_boot("main", "boot my pc", "voice")
        assert turn.text.startswith("Waking your PC") and len(started) == 1
        await started[0]
    brains._booting.clear()
    asyncio.run(go(30))
    asyncio.run(go(None))
    assert "is up" in told[0] and "did not come up" in told[1]


def test_a_boot_that_cannot_send_is_a_failure_the_ears_speak(monkeypatch):
    monkeypatch.setattr(boot, "start", lambda: "no_packet")
    turn = asyncio.run(brains.run_boot("main", "boot my pc"))
    assert turn.backend == brains.DIRECT_FAILED and "could not send" in turn.text


LAMPS = [
    {"id": "left", "name": "corner lamp", "on": True, "bri": 254, "reachable": True,
     "caps": {"color": True, "ct": [2000, 6500]}, "xy": [0.3227, 0.329], "ct": 4000,
     "gamut": [[0.6915, 0.3083], [0.17, 0.7], [0.1532, 0.0475]]},
    {"id": "right", "name": "wood lamp", "on": True, "bri": 254, "reachable": True,
     "caps": {"color": False, "ct": [2200, 6500]}, "ct": 4000},
]


def lamps(**changes):
    out = [dict(lamp) for lamp in LAMPS]
    for slot, fields in changes.items():
        next(lamp for lamp in out if lamp["id"] == slot).update(fields)
    return out


def test_light_problems_reads_each_lamp_against_what_was_asked():
    assert brains.light_problems([("all", "on", {})], lamps()) == ([], [])
    problems, retry = brains.light_problems([("all", "off", {})], lamps(left={"on": False}))
    assert problems == ["the wood lamp did not turn off"] and retry == [("right", "off", {})]
    problems, _ = brains.light_problems([("left", "on", {"brightness": 50})], lamps())
    assert problems == ["the corner lamp is at 100%, not 50%"]
    assert brains.light_problems([("left", "on", {"brightness": 50})], lamps(left={"bri": 128}))[0] == []
    problems, _ = brains.light_problems([("all", "on", {})], lamps(right={"reachable": False}))
    assert problems == ["the wood lamp is not reachable"]


def test_a_colour_is_checked_within_the_lamps_gamut_and_skipped_without_colour():
    assert brains.light_problems([("left", "on", {"color": "white"})], lamps())[0] == []
    problems, _ = brains.light_problems([("left", "on", {"color": "red"})], lamps())
    assert problems == ["the corner lamp did not take the colour"]
    assert brains.light_problems([("left", "on", {"color": "red"})], lamps(left={"xy": [0.6915, 0.3083]}))[0] == []
    assert brains.light_problems([("right", "on", {"color": "red"})], lamps())[0] == []


def test_a_temperature_is_checked_after_the_lamps_own_range():
    assert brains.light_problems([("right", "on", {"temperature": 2000})], lamps(right={"ct": 2200}))[0] == []
    problems, _ = brains.light_problems([("right", "on", {"temperature": 2600})], lamps())
    assert problems == ["the wood lamp is at 4000 K, not 2600 K"]


def test_checked_lights_retries_once_then_reports_what_still_failed(monkeypatch):
    sent = []
    monkeypatch.setattr(brains.hue, "hue_light", lambda word, target, **o: sent.append((word, target)) or "ok")
    monkeypatch.setattr(brains, "VERIFY_PAUSE_S", 0)
    reads = iter([lamps(right={"on": True}, left={"on": False}), lamps(right={"on": False}, left={"on": False})])
    monkeypatch.setattr(brains, "lamps_now", lambda age=3.0: next(reads))
    result, problems = brains.checked_lights([("all", "off", {})])
    assert problems == [] and sent == [("off", "all"), ("off", "right")]
    sent.clear()
    stuck = lamps(right={"reachable": False}, left={"on": False})
    monkeypatch.setattr(brains, "lamps_now", lambda age=3.0: stuck)
    result, problems = brains.checked_lights([("all", "off", {})])
    assert problems == ["the wood lamp is not reachable"] and sent == [("off", "all"), ("off", "right")]


def test_run_direct_says_plainly_when_a_light_did_not_take(monkeypatch):
    monkeypatch.setattr(brains, "lamps_now", lambda age=3.0: lamps(right={"reachable": False}, left={"reachable": False}))
    monkeypatch.setattr(brains.hue, "hue_light", lambda *a, **k: "ok")
    monkeypatch.setattr(brains, "VERIFY_PAUSE_S", 0)
    decision = route.Decision(subject="home_control", tier="fast", reason="", path=[])
    decision.direct = {"kind": "hue", "action": "lights_on", "target": "all", "amount": "normal", "color": "none"}
    turn = asyncio.run(brains.run_direct("main", "lights on", decision))
    assert turn.backend == brains.DIRECT_FAILED
    assert turn.text == "That did not take, sir: the corner lamp is not reachable; the wood lamp is not reachable."


class Desk:
    def __init__(self):
        self.clients = [{"address": "0xa1", "workspace": {"id": 1}}]
        self.workspace = 1
        self.volume = (0.5, False)
        self.players = {"spotify": "paused"}

    def run(self, cmd, timeout=4.0):
        if cmd[:3] == ["hyprctl", "-j", "clients"]:
            import json
            return True, json.dumps(self.clients)
        if cmd[:3] == ["hyprctl", "-j", "activeworkspace"]:
            return True, '{"id": %d}' % self.workspace
        if cmd[:2] == ["wpctl", "get-volume"]:
            return True, f"Volume: {self.volume[0]:.2f}" + (" [MUTED]" if self.volume[1] else "")
        if cmd[0] == "playerctl":
            return True, "\n".join(f"{k}\t{v.capitalize()}" for k, v in self.players.items())
        return True, ""


@pytest.fixture
def desk(monkeypatch):
    found = Desk()
    monkeypatch.setattr(commands, "_run", found.run)
    monkeypatch.setattr(commands, "VERIFY_WAIT_S", 0)
    monkeypatch.setattr(commands, "_held", lambda: set())
    return found


def test_a_window_that_stays_open_is_reported(desk):
    assert commands.verify({"action": "close_window", "window": "0xa1", "name": "Firefox"}) == \
        (False, "Firefox is still open, it may be asking to save")
    desk.clients = []
    assert commands.verify({"action": "close_window", "window": "0xa1"}) == (True, "")


def test_workspace_moves_are_read_back(desk):
    assert commands.verify({"action": "workspace", "workspace": 3})[0] is False
    desk.workspace = 3
    assert commands.verify({"action": "workspace", "workspace": 3}) == (True, "")
    assert commands.verify({"action": "move_to_workspace", "workspace": 2}, {"address": "0xa1"})[0] is False
    desk.clients[0]["workspace"]["id"] = 2
    assert commands.verify({"action": "move_to_workspace", "workspace": 2}, {"address": "0xa1"}) == (True, "")


def test_media_and_sound_are_read_back(desk):
    assert commands.verify({"action": "play", "player": "spotify"}) == (False, "spotify is not playing")
    desk.players["spotify"] = "playing"
    assert commands.verify({"action": "play"}) == (True, "")
    assert commands.verify({"action": "pause"})[0] is False
    assert commands.verify({"action": "mute"}) == (False, "the sound is not muted")
    desk.volume = (0.5, True)
    assert commands.verify({"action": "mute"}) == (True, "")
    assert commands.verify({"action": "volume_up"}, sound=(0.5, False)) == (False, "the sound stayed at 50 percent")
    desk.volume = (0.6, False)
    assert commands.verify({"action": "volume_up"}, sound=(0.5, False)) == (True, "")
    assert commands.verify({"action": "volume_up"}, sound=(1.0, False)) == (True, "")


def test_keys_and_clicks_pass_unchecked(desk):
    for action in ("enter", "click", "next", "terminal", "copy"):
        assert commands.verify({"action": action}) == (True, ""), action


def test_run_turns_a_read_back_miss_into_a_failure(desk, monkeypatch):
    monkeypatch.setattr(commands, "_active", lambda: {"address": "0xa1", "workspace": 1})
    monkeypatch.setattr(commands, "_closed_info", lambda address: None)
    monkeypatch.setattr(commands, "_save_history", lambda: None)
    monkeypatch.setattr(commands, "HISTORY", commands.collections.deque(maxlen=30))
    monkeypatch.setattr(commands, "_run_act", lambda act: (True, ""))
    ok, why = commands.run({"action": "workspace", "workspace": 4})
    assert not ok and why == "I am not on workspace 4"
    assert commands.HISTORY[-1]["action"] == "workspace" and commands.HISTORY[-1]["did"].startswith("tried")


def test_the_boot_phrase_beats_a_confident_jev_light_action():
    answer = jev_says(action="lights_on", confidence=0.99)
    assert route.route("turn on my computer", jev=answer).direct == {"kind": "boot"}
    assert route.route("turn on the lights", jev=answer).direct["kind"] == "hue"


def test_a_bridge_that_gives_no_read_back_leaves_the_change_unchecked():
    assert brains.light_problems([("left", "on", {})], []) == ([], [])


def test_a_partly_failed_change_names_what_did_change(monkeypatch):
    def gone(age=3.0):
        return lamps(right={"reachable": False})
    monkeypatch.setattr(brains, "lamps_now", gone)
    monkeypatch.setattr(brains.hue, "hue_light", lambda *a, **k: "ok")
    monkeypatch.setattr(brains, "VERIFY_PAUSE_S", 0)
    decision = route.Decision(subject="home_control", tier="fast", reason="", path=[])
    decision.direct = {"kind": "hue", "action": "lights_on", "target": "all", "amount": "normal", "color": "none"}
    turn = asyncio.run(brains.run_direct("main", "lights on", decision))
    assert turn.text == "Turned all lights on, except: the wood lamp is not reachable, sir."


def test_a_second_boot_while_one_is_waking_sends_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(boot, "start", lambda: calls.append(1) or "sent")
    monkeypatch.setattr(brains, "background", lambda coro: coro.close())
    brains._booting.clear()
    asyncio.run(brains.run_boot("main", "boot my pc"))
    turn = asyncio.run(brains.run_boot("main", "boot my pc"))
    brains._booting.clear()
    assert calls == [1] and turn.text.startswith("Already waking")
