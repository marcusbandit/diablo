import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import config, engage, gate, voiceid
from voice.engage import decide


class TestEngage(unittest.TestCase):
    def test_wake_word_always_wakes(self):
        route = decide("Diablo, dim the light")
        self.assertEqual(route, "wake")

    def test_wake_word_case_insensitive(self):
        route = decide("hey DIABLO what is this")
        self.assertEqual(route, "wake")

    def test_follow_up_needs_the_name_again(self):
        route = decide("actually, do this instead")
        self.assertEqual(route, "ignore")

    def test_after_his_last_answer_still_needs_the_name(self):
        route = decide("turn off the video")
        self.assertEqual(route, "ignore")

    def test_media_talk_without_wake_stays_ignored(self):
        route = decide("Does the stick have to go through the watermelon?")
        self.assertEqual(route, "ignore")


class TestGateParsing(unittest.TestCase):
    def test_clean_json(self):
        data = gate._parse('{"addressed": true, "mode": "instant", "language": "ja", "line": "hai"}')
        self.assertEqual(data["language"], "ja")

    def test_json_in_prose(self):
        data = gate._parse('blah blah {"addressed": false, "mode": "task", "language": "en", "line": ""} blah')
        self.assertFalse(data["addressed"])

    def test_garbage_returns_none(self):
        self.assertIsNone(gate._parse("no json here"))
        self.assertIsNone(gate._parse('{"broken": '))


class TestVoiceGateToggle(unittest.TestCase):
    def test_off_by_string(self):
        for value in ("0", "false", "no", "off", ""):
            self.assertFalse(voiceid.gate_on({"voiceprint": value}), value)

    def test_on_by_string(self):
        for value in ("1", "true", "yes", "on"):
            self.assertTrue(voiceid.gate_on({"voiceprint": value}), value)

    def test_bool_and_default(self):
        self.assertTrue(voiceid.gate_on({}))
        self.assertTrue(voiceid.gate_on({"voiceprint": True}))
        self.assertFalse(voiceid.gate_on({"voiceprint": False}))

    def test_verify_off_passes_everyone(self):
        passed, similarity, speaker = voiceid.verify(None, 16000, {"voiceprint": "0"})
        self.assertTrue(passed)
        self.assertEqual(similarity, 1.0)
        self.assertIn("gate off", speaker)


class TestConfig(unittest.TestCase):
    def test_required_defaults_exist(self):
        for key in (
            "voice_rms_floor", "engage_window_s",
            "rms_floor", "min_speech_s", "silence_tail_s", "join_window_s",
            "env_decay", "release_floor", "meter_ceiling",
        ):
            self.assertIn(key, config.DEFAULTS, f"missing default: {key}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
