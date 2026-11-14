"""Other sound turned down while he talks to Diablo (voice/duck.py).

A Desk stands in for pactl: sink inputs with volumes that the ducker reads and sets. The
clock is a number the test moves, and the ticks run inline, so nothing here sleeps or
touches real audio.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import duck, speakers

FULL = 65536


class Desk:
    def __init__(self):
        self.streams = {}
        self.down = False

    def add(self, index, app, volume=FULL, node=""):
        self.streams[index] = {"index": index, "app": app, "binary": app.lower(), "node": node, "title": "",
                               "volume": [volume, volume]}

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


class DuckTest(unittest.TestCase):
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

    def test_it_holds_through_the_linger_and_lets_go_after(self):
        self.desk.add(7, "Zen")
        self.talk()
        self.duck.update(False)
        self.tick(3.0)
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))
        self.duck.update(True)
        self.tick(3.0)
        self.duck.update(False)
        self.tick(4.0)
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))
        self.tick(2.0)
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
        self.assertEqual(self.duck.claim(), {7})
        self.assertEqual(self.desk.vol(7), round(FULL * 0.2))
        self.duck.suspend(True)
        self.assertEqual(self.duck.claim(), duck.ALL)

    def test_claim_before_start_does_nothing(self):
        self.desk.add(7, "Zen")
        self.duck.started = False
        self.assertEqual(self.duck.claim(), set())
        self.assertEqual(self.desk.vol(7), FULL)


class HoldLeavesTheDuckTest(unittest.TestCase):
    OPS = [{"op": "dim", "stream": 7}, {"op": "dim", "stream": 8}, {"op": "pause", "player": "spotify"}]

    def test_dims_of_ducked_streams_are_dropped(self):
        self.assertEqual(speakers.leave_to_duck(self.OPS, {7}), self.OPS[1:])

    def test_keep_up_drops_every_dim_but_keeps_pauses(self):
        self.assertEqual(speakers.leave_to_duck(self.OPS, duck.ALL), self.OPS[2:])

    def test_no_duck_keeps_the_hold_as_it_was(self):
        self.assertEqual(speakers.leave_to_duck(self.OPS, set()), self.OPS)


class KeepUpWordsTest(unittest.TestCase):
    def test_he_means_it(self):
        for text in ("Don't turn it down", "do not turn the music down", "Diablo, stop ducking",
                     "don't turn down the video", "keep the volume up", "leave the sound alone",
                     "stop lowering the music", "dont duck the audio"):
            self.assertTrue(duck.wants_sound_up(text), text)

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
