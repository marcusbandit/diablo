import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from voice import speakers, surroundings


class TestSurroundings(unittest.TestCase):
    def test_recent_dictations_only(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text(json.dumps([
                {"text": "old one", "at": str(now - 900)},
                {"text": "the logo is not there yet", "at": str(now - 20)},
                {"text": "", "at": str(now - 5)},
                {"bad": True},
            ]))
            self.assertEqual(surroundings.dictations(now, path), [(20, "the logo is not there yet")])

    def test_points_at_screen(self):
        self.assertTrue(surroundings.points_at_screen("can you fix this button over here"))
        self.assertTrue(surroundings.points_at_screen("update the chat UI a little"))
        self.assertFalse(surroundings.points_at_screen("turn this light off"))
        self.assertFalse(surroundings.points_at_screen("what's the weather tomorrow"))

    def test_lines_name_the_diablo_app(self):
        active = {"address": "0x1", "class": "com.marcusrosado.Diablo", "title": "Diablo",
                  "workspace": {"id": 1, "name": "1"}}
        clients = [active, {"address": "0x2", "class": "zen", "title": "Docs", "workspace": {"id": 1}},
                   {"address": "0x3", "class": "kitty", "title": "far", "workspace": {"id": 7}}]
        monitors = [{"activeWorkspace": {"id": 1}}]
        answers = {"activewindow": active, "clients": clients, "monitors": monitors}
        with mock.patch.object(surroundings, "_json", side_effect=lambda cmd: answers[cmd[-1]]), \
                mock.patch.object(surroundings, "dictations", return_value=[]):
            out = surroundings.lines()
        self.assertEqual(out[0], "Focused window: the Diablo desktop app: Diablo (workspace 1)")
        self.assertEqual(out[1], "Also on screen: zen: Docs")

    def test_dictations_never_ride_along(self):
        with mock.patch.object(surroundings, "_json", return_value=None), \
                mock.patch.object(surroundings, "dictations", return_value=[(20, "give it an 8.6 on AniList")]):
            self.assertEqual(surroundings.lines(), [])

    def test_nothing_running_gives_no_lines(self):
        with mock.patch.object(surroundings, "_run", return_value=None), \
                mock.patch.object(surroundings, "dictations", return_value=[]):
            self.assertEqual(surroundings.lines(), [])
            self.assertIsNone(surroundings.screenshot())


class FakeMedia:
    def __init__(self, ops, fail=False):
        self.ops, self.fail, self.applied, self.undone = ops, fail, None, None

    def load_settings(self):
        return {"media": {"players": "pause"}}

    def discover(self, names=True):
        if self.fail:
            raise RuntimeError("pactl gone")
        return []

    def plan(self, sources, media):
        return list(self.ops)

    def apply(self, ops):
        self.applied = ops

    def undo(self, ops):
        self.undone = ops


class TestHold(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(speakers, "HELD_FILE", Path(tmp.name) / "held.json")
        patch.start()
        self.addCleanup(patch.stop)

    def test_follows_gvoice_and_gives_back_what_it_took(self):
        ops = [{"op": "dim", "name": "Spotify", "stream": 4, "from": [65536], "to": [22937]},
               {"op": "pause", "name": "Zen", "player": "zen"}]
        media = FakeMedia(ops)
        with speakers.Hold(holder=speakers.SoundHold(media, gap=0)) as hold:
            self.assertEqual(media.applied, ops)
            self.assertEqual(hold.paused, ["Zen"])
            self.assertTrue(speakers.HELD_FILE.exists())
        self.assertEqual(media.undone, ops)
        self.assertFalse(speakers.HELD_FILE.exists())

    def test_a_failing_gvoice_falls_back_to_pausing(self):
        with mock.patch.object(speakers, "_playerctl", return_value="") as playerctl:
            with speakers.Hold(holder=speakers.SoundHold(FakeMedia([], fail=True), gap=0)) as hold:
                self.assertEqual(hold.paused, [])
        playerctl.assert_called()

    def test_recover_gives_back_a_crashed_hold(self):
        ops = [{"op": "mute", "name": "Steam", "stream": 9}]
        speakers.HELD_FILE.write_text(json.dumps({"at": time.time(), "ops": ops}))
        media = FakeMedia([])
        speakers.Hold.recover(media)
        self.assertEqual(media.undone, ops)
        self.assertFalse(speakers.HELD_FILE.exists())

    def test_an_old_crash_does_not_start_the_music(self):
        ops = [{"op": "pause", "name": "Zen", "player": "zen"}, {"op": "mute", "name": "Steam", "stream": 9}]
        speakers.HELD_FILE.write_text(json.dumps({"at": time.time() - 7200, "ops": ops}))
        media = FakeMedia([])
        speakers.Hold.recover(media)
        self.assertEqual(media.undone, ops[1:])

    def test_a_failing_apply_gives_back_what_it_took(self):
        ops = [{"op": "dim", "name": "Spotify", "stream": 4, "from": [1], "to": [0]}]
        media = FakeMedia(ops)
        media.apply = mock.Mock(side_effect=RuntimeError("pactl died"))
        with mock.patch.object(speakers, "_playerctl", return_value=""):
            with speakers.Hold(holder=speakers.SoundHold(media, gap=0)):
                pass
        self.assertEqual(media.undone, ops)
        self.assertFalse(speakers.HELD_FILE.exists())

    def test_real_gvoice_module_loads(self):
        if not speakers.GVOICE_MEDIA.exists():
            self.skipTest("no gvoice checkout")
        module = speakers.gvoice_media()
        self.assertTrue(hasattr(module, "plan") and hasattr(module, "undo"))
