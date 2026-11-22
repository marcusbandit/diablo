"""Other sound turned down while he talks to Diablo (voice/duck.py).

A Desk stands in for pactl: sink inputs with volumes that the ducker reads and sets. The
clock is a number the test moves, and the ticks run inline, so nothing here sleeps or
touches real audio.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import duck, playback, speakers

FULL = 65536


class Desk:
    def __init__(self):
        self.streams = {}
        self.down = False

    def add(self, index, app, volume=FULL, node="", pid=None):
        self.streams[index] = {"index": index, "app": app, "binary": app.lower(), "node": node, "title": "",
                               "pid": pid, "volume": [volume, volume]}

    def vol(self, index):
        return self.streams[index]["volume"][0]

    def streams_list(self):
        return None if self.down else [dict(s, volume=list(s["volume"])) for s in self.streams.values()]

    def set_volume(self, index, values):
        if index in self.streams:
            self.streams[index]["volume"] = [int(round(v)) for v in values]


class Backend:
    def __init__(self, desk):
        self.desk = desk

    def streams(self):
        return self.desk.streams_list()

    def set_volume(self, index, values):
        self.desk.set_volume(index, values)


class DuckCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.config = self.dir / "duck.json"
        self.state = self.dir / "ducked.json"
        self.now = [100.0]
        self.desk = Desk()
        self.patch = mock.patch.object(duck, "DUCK_SECONDS", 0), mock.patch.object(duck, "RESTORE_SECONDS", 0)
        for p in self.patch:
            p.start()
        self.duck = self.make()

    def tearDown(self):
        for p in self.patch:
            p.stop()

    def make(self):
        d = duck.Ducker(backend=Backend(self.desk), config_file=self.config, state_file=self.state,
                        clock=lambda: self.now[0], wall=lambda: 1_000_000.0 + self.now[0])
        d.started = d.running = True
        return d

    def tick(self, seconds=0.0):
        self.now[0] += seconds
        with self.duck.lock:
            self.duck._tick()

    def talk(self):
        self.duck.update(True)
        self.tick()

    def end(self, after=10.0):
        self.duck.update(False)
        self.tick(after)


class DuckTest(DuckCase):
    def test_a_video_is_turned_down_and_comes_back_exactly(self):
        self.desk.add(7, "Zen", 50000)
        self.talk()
        self.assertEqual(self.desk.vol(7), 10000)
        self.end()
        self.assertEqual(self.desk.vol(7), 50000)
        self.assertFalse(self.state.exists())

    def test_a_stream_started_mid_conversation_is_turned_down_too(self):
        self.talk()
        self.desk.add(9, "Spotify")
        self.tick(0.25)
        self.assertEqual(self.desk.vol(9), round(FULL * 0.2))
        self.end()
        self.assertEqual(self.desk.vol(9), FULL)

    def test_his_own_voice_and_chimes_are_never_touched(self):
        self.desk.add(1, "Diablo")
        self.desk.add(2, "pw-play")
        self.desk.add(3, "python3", node="diablo-voice")
        self.desk.add(4, "Some video", node="pw-play")
        self.desk.streams[4]["binary"] = "pw-play"
        self.talk()
        self.assertEqual([self.desk.vol(i) for i in (1, 2, 3)], [FULL] * 3)
        self.assertEqual(self.desk.vol(4), round(FULL * 0.2))

    def test_his_voice_through_the_pulse_plugin_is_known_by_the_ears_pid(self):
        self.desk.add(5, "ALSA plug-in [python3.12]", pid=os.getpid())
        self.desk.add(6, "ALSA plug-in [python3.12]", pid=os.getpid() + 100000)
        self.talk()
        self.assertEqual(self.desk.vol(5), FULL)
        self.assertEqual(self.desk.vol(6), round(FULL * 0.2))
        self.assertNotIn(5, self.duck.held)

    def test_an_app_the_ears_started_is_turned_down_like_any_other(self):
        child = subprocess.Popen(["sleep", "5"])
        try:
            self.desk.add(5, "Spotify", pid=child.pid)
            self.talk()
            self.assertEqual(self.desk.vol(5), round(FULL * 0.2))
        finally:
            child.kill()
            child.wait()

    def test_an_orphan_his_stream_does_not_match_stays_for_its_app(self):
        self.duck.orphans["alsa plug-in [python3.12]"] = {"from": [40000, 40000], "to": [8000, 8000], "at": 1_000_000.0}
        self.desk.add(5, "ALSA plug-in [python3.12]", pid=os.getpid())
        self.tick(2.0)
        self.assertEqual(self.desk.vol(5), FULL)
        self.assertIn("alsa plug-in [python3.12]", self.duck.orphans)
        self.desk.add(6, "ALSA plug-in [python3.12]", 8000, pid=os.getpid() + 100000)
        self.tick(2.0)
        self.assertEqual(self.desk.vol(6), 40000)

    def test_the_sound_hold_leaves_a_source_only_the_ears_play(self):
        ours = {"names": ["alsa plug-in [python3.12]"], "streams": [{"pid": os.getpid()}]}
        theirs = {"names": ["alsa plug-in [python3.12]"], "streams": [{"pid": os.getpid() + 100000}]}
        self.assertTrue(speakers.own(ours))
        self.assertFalse(speakers.own(theirs))

    def test_an_app_launched_while_the_voice_stream_opens_never_inherits_its_names(self):
        launched, during = {}, {}

        def launch():
            launched["env"] = playback.launch_env()

        def output_stream(*args, **kwargs):
            during["env"] = os.environ.get("PULSE_PROP_OVERRIDE")
            launched["thread"] = threading.Thread(target=launch)
            launched["thread"].start()
            return mock.Mock()

        fake = mock.Mock(OutputStream=output_stream)
        with mock.patch.dict(sys.modules, {"sounddevice": fake}):
            playback._open_stream(24000, None)
        launched["thread"].join(2)
        self.assertIn("application.name='Diablo'", during["env"])
        self.assertNotIn("PULSE_PROP_OVERRIDE", launched["env"])
        self.assertNotIn("PULSE_PROP_OVERRIDE", os.environ)

    def test_his_own_stream_closing_leaves_no_orphan(self):
        self.desk.add(5, "ALSA plug-in [python3.12]", pid=os.getpid())
        self.desk.add(7, "Zen")
        self.talk()
        del self.desk.streams[5]
        self.end()
        self.assertEqual(self.duck.orphans, {})
        self.assertFalse(self.state.exists())

    def test_his_stream_an_earlier_run_left_turned_down_gets_its_volume_back(self):
        low = round(FULL * 0.0075)
        self.state.write_text(json.dumps({"at": 1.0, "held": [
            {"index": 3, "app": "alsa plug-in [python3.12]", "name": "ALSA plug-in [python3.12]",
             "from": [FULL, FULL], "to": [low, low]}], "orphans": {}}))
        fresh = self.make()
        fresh.recover()
        self.assertIn("alsa plug-in [python3.12]", fresh.orphans)
        self.desk.add(5, "ALSA plug-in [python3.12]", low, pid=os.getpid())
        self.duck = fresh
        self.tick(2.0)
        self.assertEqual(self.desk.vol(5), FULL)
        self.assertEqual(fresh.orphans, {})
        self.assertFalse(self.state.exists())

    def test_his_left_down_stream_is_mended_mid_conversation_too(self):
        low = round(FULL * 0.0075)
        self.duck.orphans["alsa plug-in [python3.12]"] = {"from": [FULL, FULL], "to": [low, low], "at": 1_000_000.0}
        self.talk()
        self.desk.add(5, "ALSA plug-in [python3.12]", low, pid=os.getpid())
        self.tick(0.25)
        self.assertEqual(self.desk.vol(5), FULL)
        self.assertEqual(self.duck.orphans, {})

    def test_pactl_reads_the_process_id(self):
        raw = [{"index": 5, "properties": {"application.name": "ALSA plug-in [python3.12]",
                                            "application.process.id": str(os.getpid())},
                "volume": {"front-left": {"value": FULL}}}]
        done = mock.Mock(returncode=0, stdout=json.dumps(raw))
        with mock.patch.object(duck.subprocess, "run", return_value=done):
            stream = duck.Pactl().streams()[0]
        self.assertEqual(stream["pid"], os.getpid())
        self.assertTrue(duck.own(stream))

    def test_the_voice_stream_is_named_on_both_alsa_paths(self):
        env = playback.stream_env(pid=4242)
        self.assertIn('application.name = "Diablo"', env["PIPEWIRE_PROPS"])
        self.assertIn("application.process.id = 4242", env["PIPEWIRE_PROPS"])
        self.assertIn("application.name='Diablo'", env["PULSE_PROP_OVERRIDE"])
        self.assertIn("node.name='diablo-voice'", env["PULSE_PROP_OVERRIDE"])

    def test_the_sound_comes_back_the_moment_the_conversation_ends(self):
        self.desk.add(7, "Zen")
        self.talk()
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))
        self.duck.update(False)
        self.tick(0.0)
        self.assertEqual(self.desk.vol(7), FULL)
        self.assertFalse(self.duck.engaged)

    def test_a_claim_between_conversations_turns_nothing_down(self):
        self.desk.add(7, "Zen")
        self.assertEqual(self.duck.claim(), set())
        self.assertEqual(self.desk.vol(7), FULL)

    def test_a_volume_he_moved_stays_where_he_put_it(self):
        self.desk.add(7, "Zen")
        self.talk()
        self.desk.set_volume(7, [40000, 40000])
        self.end()
        self.assertEqual(self.desk.vol(7), 40000)

    def test_off_in_settings_leaves_everything_alone(self):
        duck.save_settings({"on": False}, self.config)
        self.duck = self.make()
        self.desk.add(7, "Zen")
        self.talk()
        self.assertEqual(self.desk.vol(7), FULL)

    def test_the_level_comes_from_settings_and_moves_live(self):
        duck.save_settings({"level": 10}, self.config)
        self.duck = self.make()
        self.desk.add(7, "Zen", 60000)
        self.talk()
        self.assertEqual(self.desk.vol(7), 6000)
        self.duck.configure({"level": 30})
        self.assertEqual(self.desk.vol(7), 18000)
        self.end()
        self.assertEqual(self.desk.vol(7), 60000)
        self.assertEqual(json.loads(self.config.read_text()), {"on": True, "level": 30})

    def test_switching_it_off_mid_conversation_gives_back_at_once(self):
        self.desk.add(7, "Zen")
        self.talk()
        self.duck.configure({"on": False})
        self.assertEqual(self.desk.vol(7), FULL)

    def test_keep_up_gives_back_now_and_holds_for_the_rest_of_the_conversation(self):
        self.desk.add(7, "Zen")
        self.talk()
        self.duck.suspend(True)
        self.assertEqual(self.desk.vol(7), FULL)
        self.desk.add(8, "Spotify")
        self.tick(0.25)
        self.assertEqual(self.desk.vol(8), FULL)
        self.end()
        self.talk()
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))

    def test_keep_up_asked_between_conversations_holds_for_the_next(self):
        self.desk.add(7, "Zen")
        self.duck.suspend(True)
        self.tick(30)
        self.talk()
        self.assertEqual(self.desk.vol(7), FULL)
        self.end()
        self.talk()
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))

    def test_a_stale_keep_up_runs_out(self):
        self.desk.add(7, "Zen")
        self.duck.suspend(True)
        self.tick(duck.SUSPEND_IDLE_S + 1)
        self.talk()
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))

    def test_keep_up_false_turns_it_down_again(self):
        self.desk.add(7, "Zen")
        self.talk()
        self.duck.suspend(True)
        self.duck.suspend(False)
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))

    def test_a_new_stream_starting_at_the_app_saved_low_level_comes_back_to_the_real_one(self):
        self.desk.add(7, "Zen", 50000)
        self.talk()
        self.desk.add(8, "Zen", 10000)
        self.tick(0.25)
        self.assertEqual(self.desk.vol(8), 10000)
        self.end()
        self.assertEqual((self.desk.vol(7), self.desk.vol(8)), (50000, 50000))

    def test_a_stream_that_closed_while_down_mends_the_app_next_time(self):
        self.desk.add(7, "Zen", 50000)
        self.talk()
        del self.desk.streams[7]
        self.end()
        self.assertIn("zen", json.loads(self.state.read_text())["orphans"])
        self.desk.add(9, "Zen", 10000)
        self.tick(2.0)
        self.assertEqual(self.desk.vol(9), 50000)
        self.assertFalse(self.state.exists())

    def test_an_orphan_app_back_at_its_own_volume_is_left_alone(self):
        self.desk.add(7, "Zen", 50000)
        self.talk()
        del self.desk.streams[7]
        self.end()
        self.desk.add(9, "Zen", 30000)
        self.tick(2.0)
        self.assertEqual(self.desk.vol(9), 30000)
        self.assertFalse(self.duck.orphans)

    def test_a_crash_gives_everything_back_on_the_next_start(self):
        self.desk.add(7, "Zen", 50000)
        self.talk()
        self.assertTrue(self.state.exists())
        fresh = self.make()
        fresh.started = False
        fresh.recover()
        self.assertEqual(self.desk.vol(7), 50000)
        self.assertFalse(self.state.exists())

    def test_a_stop_gives_back_without_waiting(self):
        self.desk.add(7, "Zen", 50000)
        self.talk()
        self.duck.stop()
        self.assertEqual(self.desk.vol(7), 50000)

    def test_pactl_not_answering_keeps_the_list_to_try_again(self):
        self.desk.add(7, "Zen", 50000)
        self.talk()
        self.desk.down = True
        self.end()
        self.assertTrue(self.state.exists())
        self.desk.down = False
        self.tick(0.25)
        self.assertEqual(self.desk.vol(7), 50000)

    def test_claim_turns_down_now_and_names_what_the_hold_must_leave(self):
        self.desk.add(7, "Zen")
        self.duck.update(True)
        self.assertEqual(self.duck.claim(), {7})
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))
        self.duck.suspend(True)
        self.assertEqual(self.duck.claim(), duck.ALL)

    def test_two_streams_of_one_app_each_keep_their_own_volume(self):
        self.desk.add(7, "Firefox", FULL)
        self.desk.add(8, "Firefox", round(FULL * 0.2))
        self.talk()
        self.assertEqual((self.desk.vol(7), self.desk.vol(8)), (round(FULL * 0.2), round(FULL * 0.04)))
        self.end()
        self.assertEqual((self.desk.vol(7), self.desk.vol(8)), (FULL, round(FULL * 0.2)))

    def test_an_app_his_gvoice_rules_keep_playing_is_left_alone(self):
        self.duck.exempt = lambda: ["vesktop"]
        self.desk.add(7, "vesktop")
        self.desk.add(8, "Zen")
        self.talk()
        self.assertEqual((self.desk.vol(7), self.desk.vol(8)), (FULL, round(FULL * 0.2)))

    def test_a_new_level_leaves_a_volume_he_moved(self):
        self.desk.add(7, "Zen")
        self.desk.add(8, "Spotify")
        self.talk()
        self.desk.set_volume(7, [40000, 40000])
        self.duck.configure({"level": 10})
        self.assertEqual((self.desk.vol(7), self.desk.vol(8)), (40000, round(FULL * 0.1)))
        self.end()
        self.assertEqual((self.desk.vol(7), self.desk.vol(8)), (40000, FULL))

    def test_keep_up_counts_with_the_switch_off_too(self):
        duck.save_settings({"on": False}, self.config)
        self.duck = self.make()
        self.duck.suspend(True)
        self.assertEqual(self.duck.claim(), duck.ALL)

    def test_an_old_linger_in_the_settings_holds_nothing(self):
        self.config.write_text('{"on": true, "level": 20, "linger_s": 15}')
        self.duck = self.make()
        self.assertNotIn("linger_s", duck.settings(self.config))
        self.desk.add(7, "Zen")
        self.talk()
        self.duck.update(False)
        self.tick(0.0)
        self.assertEqual(self.desk.vol(7), FULL)

    def test_pactl_failing_reads_as_unknown_not_as_no_streams(self):
        failed = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(duck.subprocess, "run", return_value=failed):
            self.assertIsNone(duck.Pactl().streams())
        with mock.patch.object(duck.subprocess, "run", side_effect=OSError):
            self.assertIsNone(duck.Pactl().streams())

    def test_claim_before_start_does_nothing(self):
        self.desk.add(7, "Zen")
        self.duck.started = False
        self.assertEqual(self.duck.claim(), set())
        self.assertEqual(self.desk.vol(7), FULL)


class Media:
    """gvoice's media module as the hold uses it: one Zen player on sink input 7, paused by the
    rules and played again on undo, each step written to the same log as the volumes."""

    def __init__(self, desk, log):
        self.desk, self.log = desk, log
        self.state = "Playing"

    def load_settings(self):
        return {"media": {"players": "pause", "others": "dim", "dim_to": 35, "apps": []}}

    def discover(self, names=True):
        streams = [{"index": i, "pid": None} for i, s in self.desk.streams.items() if s["app"] == "Zen"]
        return [{"kind": "player", "id": "zen.instance1", "name": "Zen", "names": ["zen", "zen.instance1"],
                 "player": "zen.instance1", "playing": self.state == "Playing", "streams": streams}]

    def plan(self, sources, rules):
        return [{"op": "pause", "player": s["player"], "name": s["name"], "id": s["id"]}
                for s in sources if s["playing"]]

    def apply(self, ops):
        for op in ops:
            if op["op"] == "pause":
                self.state = "Paused"
                self.log.append(("pause", op["player"]))

    def undo(self, ops):
        for op in ops:
            if op["op"] == "pause" and self.state == "Paused":
                self.state = "Playing"
                self.log.append(("play", op["player"]))


class Timers:
    """threading.Timer that only fires when the test says so."""

    def __init__(self):
        self.made = []

    def __call__(self, wait, fn):
        timer = mock.Mock(wait=wait, fn=fn)
        self.made.append(timer)
        return timer


class SoundBeforeVideoTest(DuckCase):
    """The duck and the hold together: a paused video never plays while its sound is down."""

    def setUp(self):
        super().setUp()
        self.log = []
        self.desk.add(7, "Zen", 50000)
        set_volume = self.desk.set_volume

        def logged(index, values):
            set_volume(index, values)
            self.log.append(("volume", index, self.desk.vol(index)))

        self.desk.set_volume = logged
        self.media = Media(self.desk, self.log)
        self.timers = Timers()
        self.hold = speakers.SoundHold(media=self.media, gap=1.5, timer=self.timers)
        self.duck.on_release = self.hold.flush
        self.duck.foreign = lambda: {op.get("stream") for op in self.hold.ops if op.get("op") == "dim"}
        held = self.dir / "media-held.json"
        self.extra = [mock.patch.object(duck, "DUCK", self.duck), mock.patch.object(speakers, "HELD_FILE", held)]
        for p in self.extra:
            p.start()

    def tearDown(self):
        for p in self.extra:
            p.stop()
        super().tearDown()

    def step(self):
        self.duck.wake.clear()
        return self.duck._step()

    def played_with(self):
        """The volume of sink input 7 at the moment Zen was played again."""
        at = self.log.index(("play", "zen.instance1"))
        volumes = [e[2] for e in self.log[:at] if e[0] == "volume" and e[1] == 7]
        return volumes[-1] if volumes else None

    def test_the_end_of_speech_brings_sound_then_video_in_one_step(self):
        self.duck.update(True)
        self.step()
        self.hold.acquire()
        self.assertEqual(self.desk.vol(7), 10000)
        self.assertEqual(self.media.state, "Paused")
        self.duck.update(False)
        self.hold.release()
        self.assertEqual(self.timers.made, [])
        self.assertEqual(self.media.state, "Playing")
        self.assertEqual(self.played_with(), 50000)
        self.assertEqual(self.desk.vol(7), 50000)

    def test_a_hold_waiting_out_its_gap_plays_right_after_the_duck_lets_go(self):
        self.duck.update(True)
        self.step()
        self.hold.acquire()
        self.hold.release()
        self.assertEqual(len(self.timers.made), 1)
        self.assertEqual(self.media.state, "Paused")
        self.duck.update(False)
        self.step()
        self.assertEqual(self.media.state, "Playing")
        self.assertEqual(self.played_with(), 50000)
        self.timers.made[0].cancel.assert_called()

    def test_a_late_gap_still_waits_for_the_sound(self):
        self.duck.update(True)
        self.step()
        self.hold.acquire()
        self.hold.release()
        self.duck.update(False)
        self.hold._expire(self.timers.made[0])
        self.assertEqual(self.played_with(), 50000)

    def test_a_video_resumed_mid_conversation_plays_with_its_sound(self):
        self.duck.update(True)
        self.step()
        self.hold.acquire()
        self.hold.release(gap=0)
        self.assertEqual(self.media.state, "Playing")
        self.assertEqual(self.played_with(), 50000)
        self.desk.add(9, "Zen", 40000)
        self.desk.add(8, "Spotify")
        self.step()
        self.assertEqual((self.desk.vol(7), self.desk.vol(9), self.desk.vol(8)), (50000, 40000, round(FULL * 0.2)))
        self.duck.update(False)
        self.step()
        self.duck.update(True)
        self.step()
        self.assertEqual(self.desk.vol(7), 10000)

    def test_keep_up_still_leaves_the_video_playing(self):
        self.duck.update(True)
        self.step()
        self.duck.suspend(True)
        self.hold.acquire()
        self.assertEqual(self.media.state, "Playing")
        self.assertEqual(self.desk.vol(7), 50000)
        self.hold.release()
        self.duck.update(False)
        self.step()
        self.assertNotIn(("pause", "zen.instance1"), self.log)


class HoldLeavesTheDuckTest(unittest.TestCase):
    OPS = [{"op": "dim", "stream": 7}, {"op": "dim", "stream": 8}, {"op": "pause", "player": "spotify"}]

    def test_dims_of_ducked_streams_are_dropped(self):
        self.assertEqual(speakers.leave_to_duck(self.OPS, {7}), self.OPS[1:])

    def test_keep_up_leaves_the_hold_nothing_to_take(self):
        self.assertEqual(speakers.leave_to_duck(self.OPS, duck.ALL), [])

    def test_no_duck_keeps_the_hold_as_it_was(self):
        self.assertEqual(speakers.leave_to_duck(self.OPS, set()), self.OPS)


class KeepUpWordsTest(unittest.TestCase):
    def test_he_means_it(self):
        for text in ("Don't turn it down", "do not turn the music down", "Diablo, stop ducking",
                     "don't turn down the video", "keep the volume up", "leave the sound alone",
                     "stop lowering the music", "dont duck the audio"):
            self.assertTrue(duck.wants_sound_up(text), text)

    def test_a_longer_request_is_not_only_that(self):
        self.assertTrue(duck.only_sound_up("Diablo, don't turn it down please."))
        self.assertTrue(duck.only_sound_up("Okay, just keep the music up for now"))
        self.assertFalse(duck.only_sound_up("keep the music on and set a timer for ten minutes"))
        self.assertFalse(duck.only_sound_up("leave the music alone and add milk to my list"))

    def test_other_lines_are_not_it(self):
        for text in ("turn it down", "turn the music down", "keep it up", "don't dim the lights",
                     "don't turn the lights down", "turn down the lights", "leave it", "what's the volume"):
            self.assertFalse(duck.wants_sound_up(text), text)


class SettingsFileTest(unittest.TestCase):
    def test_defaults_and_bad_values(self):
        path = Path(tempfile.mkdtemp()) / "duck.json"
        self.assertEqual(duck.settings(path)["on"], True)
        self.assertEqual(duck.settings(path)["level"], 20)
        path.write_text('{"on": "yes", "level": 400}')
        self.assertEqual((duck.settings(path)["on"], duck.settings(path)["level"]), (True, 100))


if __name__ == "__main__":
    unittest.main()
