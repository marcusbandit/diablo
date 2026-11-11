import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import media

WPCTL = """PipeWire 'pipewire-0' [1.4.2, bandit@banditbox, cookie:1]
 └─ Clients:
Audio
 ├─ Devices:
 │      41. Built-in Audio                      [alsa]
 │
 ├─ Sinks:
 │      31. HDMI Output                         [vol: 1.00]
 │      70. Scarlett 2i2 3rd Gen Headphones / Line 1-2 [vol: 0.26]
 │  *   72. Built-in Audio Analog Stereo        [vol: 1.00]
 │      77. GA102 High Definition Audio Controller Digital Stereo (HDMI) [Odyssey G91SD] [vol: 0.60]
 │
 ├─ Sources:
 │  *   55. Some Mic                            [vol: 1.00]
"""


def done(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(["x"], rc, stdout, stderr)


class TestPlayback(unittest.TestCase):
    def test_toggle_becomes_play_pause_for_the_named_player(self):
        with mock.patch("voice.media.subprocess.run", return_value=done()) as run:
            ok, text = media.media_control({"action": "toggle", "player": "spotify"}, {})
        self.assertTrue(ok)
        self.assertEqual(run.call_args[0][0], ["playerctl", "-p", "spotify", "play-pause"])
        self.assertEqual(text, "toggle sent to spotify")

    def test_no_players_fails_plainly(self):
        with mock.patch("voice.media.subprocess.run", return_value=done(stderr="No players found", rc=1)):
            self.assertEqual(media.media_control({"action": "play"}, {}), (False, "nothing is playing anywhere"))

    def test_unknown_action_fails(self):
        self.assertFalse(media.media_control({"action": "louder"}, {})[0])

    def test_now_playing_prefers_playing_and_drops_empty_artist(self):
        out = "firefox|Paused|Someone|Some Video\nspotify|Playing||Untitled Track\n"
        with mock.patch("voice.media.subprocess.run", return_value=done(stdout=out)):
            self.assertEqual(media.now_playing({}, {}), (True, "Spotify, playing: Untitled Track"))

    def test_now_playing_falls_back_to_paused(self):
        out = "spotify|Paused|milet|Anytime Anywhere\n"
        with mock.patch("voice.media.subprocess.run", return_value=done(stdout=out)):
            self.assertEqual(media.now_playing({}, {}), (True, "Spotify, paused: milet, Anytime Anywhere"))

    def test_now_playing_with_nothing(self):
        with mock.patch("voice.media.subprocess.run", return_value=done(stderr="No players found", rc=1)):
            self.assertEqual(media.now_playing({}, {}), (True, "nothing is playing"))


class TestOutputs(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("voice.media.subprocess.run", side_effect=self.fake_run)
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def fake_run(self, cmd, **kwargs):
        if cmd[:2] == ["wpctl", "status"]:
            return done(stdout=WPCTL)
        return done()

    def test_sinks_parses_the_sample(self):
        rows = media.sinks()
        self.assertEqual([r["id"] for r in rows], [31, 70, 72, 77])
        self.assertEqual([r["id"] for r in rows if r["current"]], [72])
        self.assertEqual(rows[1]["name"], "Scarlett 2i2 3rd Gen Headphones / Line 1-2")
        self.assertEqual(rows[3]["name"], "GA102 High Definition Audio Controller Digital Stereo (HDMI) [Odyssey G91SD]")

    def test_headphones_alias_sets_default_70(self):
        ok, text = media.audio_output({"name": "headphones"}, {})
        self.assertTrue(ok)
        self.assertEqual(self.run.call_args[0][0], ["wpctl", "set-default", "70"])
        self.assertIn("Scarlett", text)

    def test_hdmi_with_two_matches_fails_and_lists_them(self):
        ok, text = media.audio_output({"name": "hdmi"}, {})
        self.assertFalse(ok)
        self.assertIn("HDMI Output", text)
        self.assertIn("Odyssey", text)
        self.assertNotIn("Scarlett", text)
        self.assertNotIn(["wpctl", "set-default", "31"], [c[0][0] for c in self.run.call_args_list])

    def test_empty_name_lists_and_marks_current(self):
        ok, text = media.audio_output({"name": ""}, {})
        self.assertTrue(ok)
        self.assertIn("Built-in Audio Analog Stereo (in use)", text)
        self.assertEqual(media.list_outputs({}, {}), (True, text))


class TestMemo(unittest.TestCase):
    def test_records_terminates_and_names_the_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proc = mock.Mock()

        def popen(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"RIFF")
            return proc

        with mock.patch.object(media, "MEMOS", Path(tmp.name)), mock.patch(
            "voice.media.subprocess.Popen", side_effect=popen
        ) as started, mock.patch("voice.media.time.sleep") as sleep:
            ok, text = media.record_memo({"seconds": 3}, {})
        self.assertTrue(ok, text)
        cmd = started.call_args[0][0]
        self.assertEqual(cmd[:5], ["pw-record", "--rate", "48000", "--channels", "1"])
        self.assertRegex(Path(cmd[-1]).name, r"^memo-\d{8}-\d{6}\.wav$")
        sleep.assert_called_once_with(3)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=3)
        self.assertTrue(text.startswith("3 second memo saved to "))

    def test_clamps_and_fails_on_empty_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(media, "MEMOS", Path(tmp.name)), mock.patch(
            "voice.media.subprocess.Popen", return_value=mock.Mock()
        ), mock.patch("voice.media.time.sleep") as sleep:
            ok, text = media.record_memo({"seconds": 900}, {})
        self.assertFalse(ok)
        sleep.assert_called_once_with(120)


class TestMachine(unittest.TestCase):
    def test_status_mentions_uptime_and_memory(self):
        ok, text = media.system_status({}, {})
        self.assertTrue(ok)
        self.assertIn("up ", text)
        self.assertIn("GB", text)

    def test_one_broken_piece_does_not_lose_the_rest(self):
        with mock.patch.object(media, "_uptime", side_effect=OSError("gone")):
            ok, text = media.system_status({}, {})
        self.assertTrue(ok)
        self.assertIn("GB", text)


class TestTable(unittest.TestCase):
    def test_every_entry_has_the_six_keys(self):
        for entry in media.SKILLS:
            self.assertEqual(set(entry), {"name", "description", "parameters", "run", "kind", "bank"}, entry["name"])
            self.assertIn(entry["kind"], ("act", "tell"))
            self.assertTrue(callable(entry["run"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
