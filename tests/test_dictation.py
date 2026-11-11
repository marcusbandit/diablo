import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from voice import dictation, ears, wake


class Transcripts(unittest.TestCase):
    def test_each_clip_owns_the_lines_until_the_next_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            a, b = tmp / "20261109-120000.wav", tmp / "20261109-120100.wav"
            start_a = dictation._start(a)
            lines = [
                {"at": start_a + 5, "text": "hello there"},
                {"at": start_a + 5.1, "text": "hello there"},  # gvoice writes some lines twice
                {"at": start_a + 30, "text": "second line"},
                {"at": start_a + 65, "text": "the next clip"},
                {"at": start_a - 10, "text": "before any clip"},
            ]
            texts = tmp / "confidence.jsonl"
            texts.write_text("\n".join(json.dumps(line) for line in lines))
            with mock.patch.object(dictation, "TEXTS", texts):
                out = dictation.transcripts([a, b])
        self.assertEqual(out[a.name], "hello there second line")
        self.assertEqual(out[b.name], "the next clip")


class Usable(unittest.TestCase):
    good = {"text": "open the hub settings", "seconds": 4.0, "voiced": 3.1, "voice": 0.9, "name": False}

    def test_a_clean_clip_of_him_is_used(self):
        self.assertTrue(dictation.usable(self.good))

    def test_the_name_said_in_a_dictation_is_never_a_negative(self):
        entry = dict(self.good, text="tell Diablo to stop", name=bool(dictation.NAME.search("tell Diablo to stop")))
        self.assertFalse(dictation.usable(entry))

    def test_spellings_of_the_name(self):
        for text in ("Diablo", "diablo's page", "Diabolo", "dia blo", "Diaablo"):
            self.assertTrue(dictation.NAME.search(text), text)
        for text in ("Diplo", "a diagram", "the blow"):
            self.assertFalse(dictation.NAME.search(text), text)

    def test_little_speech_no_text_or_another_voice_is_left_out(self):
        self.assertFalse(dictation.usable(dict(self.good, voiced=0.5)))
        self.assertFalse(dictation.usable(dict(self.good, text="  ")))
        self.assertFalse(dictation.usable(dict(self.good, voice=0.6)))
        self.assertFalse(dictation.usable(dict(self.good, error="bad file")))

    def test_held_out_is_stable(self):
        names = [f"2026110{i}-1200{j:02d}.wav" for i in range(1, 10) for j in range(60)]
        first = [dictation.held(n) for n in names]
        self.assertEqual(first, [dictation.held(n) for n in names])
        self.assertTrue(0.1 < sum(first) / len(first) < 0.3)


class Peaks(unittest.TestCase):
    def test_highest_separate_moments_over_the_floor(self):
        scores = np.zeros(200)
        scores[[10, 11, 12]] = [0.5, 0.9, 0.6]   # one moment, three frames
        scores[100] = 0.4
        scores[150] = 0.1                        # under the floor
        self.assertEqual(dictation._peaks(scores, 0.3, 3), [11, 100])
        self.assertEqual(dictation._peaks(scores, 0.3, 1), [11])


class TalkCheck(unittest.TestCase):
    audio = np.zeros(16000, dtype=np.float32)

    def check(self, meta, score, cfg=None):
        with mock.patch.object(wake, "meta", return_value=meta), mock.patch.object(wake, "talk_score", return_value=score):
            return ears.talk_ok(self.audio, cfg or {})

    def test_his_request_passes_and_another_voice_does_not(self):
        self.assertEqual(self.check({"talk_min": 0.62}, 0.8), (True, 0.8))
        self.assertEqual(self.check({"talk_min": 0.62}, 0.5), (False, 0.5))

    def test_a_one_word_request_is_not_judged(self):
        with tempfile.TemporaryDirectory() as tmp:
            talk = Path(tmp) / "talk.npy"
            np.save(talk, np.ones(256, dtype=np.float32) / 16)
            rng = np.random.default_rng(0)
            word = (0.3 * rng.standard_normal(int(0.6 * 16000))).astype(np.float32)
            with mock.patch.object(wake, "TALK_PRINT", talk), mock.patch.object(wake, "_voice_embed") as embed:
                self.assertIsNone(wake.talk_score(word))
                embed.assert_not_called()

    def test_no_print_or_check_off_lets_it_through(self):
        self.assertEqual(self.check({}, 0.1), (True, None))
        self.assertEqual(self.check({"talk_min": 0.62}, None), (True, None))
        self.assertEqual(self.check({"talk_min": 0.62}, 0.1, {"ears_speaker_check": "off"}), (True, None))


if __name__ == "__main__":
    unittest.main()
