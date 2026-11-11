import io
import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from voice import mouth


def _wav(seconds, loud_from, loud_to, rate=24000):
    frames = []
    for i in range(int(seconds * rate)):
        t = i / rate
        amp = 0.6 if loud_from <= t < loud_to else 0.0
        frames.append(struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * 220 * t))))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


class ShapeTest(unittest.TestCase):
    def test_sounds_map_to_drawn_mouths(self):
        cases = {"m": "rest", "p": "rest", "f": "fv", "v": "fv", "w": "oo", "uː": "oo", "ɹ": "oo",
                 "l": "l", "ð": "l", "i": "ee", "j": "ee", "h": "h", "aɪ": "open", "ə": "open",
                 "oʊ": "open", "s": "mid", "k": "mid", "n": "mid"}
        for phone, want in cases.items():
            self.assertEqual(mouth.shape(phone), want, phone)


class LoudnessTest(unittest.TestCase):
    def test_mouth_opens_only_while_there_is_sound(self):
        track = mouth.by_loudness(_wav(1.0, 0.4, 0.7))
        self.assertEqual(track[0], (0.0, "rest"))
        opened = [at for at, s in track if s == "open"]
        self.assertTrue(opened and abs(opened[0] - 0.4) <= 0.04)
        self.assertEqual(track[-1][1], "rest")
        self.assertTrue(abs(track[-1][0] - 0.7) <= 0.04)

    def test_garbage_gives_no_track(self):
        self.assertEqual(mouth.by_loudness(b"not a wav"), [])


class SecondsTest(unittest.TestCase):
    def test_length_from_the_header(self):
        self.assertAlmostEqual(mouth.seconds(_wav(0.5, 0, 0)), 0.5, places=3)
        self.assertEqual(mouth.seconds(b"junk"), 0.0)


class TrackFileTest(unittest.TestCase):
    def test_playing_then_stopped(self):
        path = Path(tempfile.mkdtemp()) / "mouth.json"
        with mock.patch.object(mouth, "TRACK_FILE", path), mock.patch.object(mouth.time, "time", return_value=100.0):
            mouth.playing([(0.0, "open")], 1.5, latency=0.06)
            data = json.loads(path.read_text())
            self.assertEqual(data["start"], 100.06)
            self.assertEqual(data["cues"], [[0.0, "open"]])
            mouth.stopped()
            self.assertEqual(json.loads(path.read_text())["cues"], [])

    def test_no_text_falls_back_to_loudness(self):
        wav = _wav(0.5, 0.1, 0.3)
        self.assertEqual(mouth.face_for("", wav)["mouth"], mouth.by_loudness(wav))


class ViterbiTest(unittest.TestCase):
    def _aligner(self):
        a = mouth.Aligner()
        a._blank = 0
        return a

    def test_long_sentences_backtrack_past_int8(self):
        import numpy as np
        ids = [1 + i % 5 for i in range(80)]  # 161 states, past what an int8 index holds
        frames = 400
        logp = np.full((frames, 6), -10.0)
        for f in range(frames):
            k = f * len(ids) // frames
            logp[f, ids[k] if f % 5 else 0] = 0.0
        starts = self._aligner()._viterbi(logp, ids)
        self.assertEqual(len(starts), len(ids))
        self.assertTrue(all(s is not None for s in starts))
        self.assertEqual(starts, sorted(starts))

    def test_no_fitting_path_gives_none(self):
        import numpy as np
        logp = np.zeros((3, 3))
        self.assertIsNone(self._aligner()._viterbi(logp, [1, 1, 1]))


if __name__ == "__main__":
    unittest.main()


class PretrainedTest(unittest.TestCase):
    def test_the_local_cache_is_tried_before_the_network(self):
        calls = []

        class Model:
            @staticmethod
            def from_pretrained(name, **kwargs):
                calls.append(kwargs)
                return "model"

        self.assertEqual(mouth._pretrained(Model), "model")
        self.assertEqual(calls, [{"local_files_only": True}])

    def test_a_missing_cache_falls_back_to_the_hub(self):
        calls = []

        class Model:
            @staticmethod
            def from_pretrained(name, **kwargs):
                calls.append(kwargs)
                if kwargs.get("local_files_only"):
                    raise OSError("not cached")
                return "fetched"

        self.assertEqual(mouth._pretrained(Model), "fetched")
        self.assertEqual(calls, [{"local_files_only": True}, {}])


class WarmTest(unittest.TestCase):
    def test_warm_hands_back_a_thread_to_wait_on(self):
        with mock.patch.object(mouth._aligner, "warm"):
            thread = mouth.warm()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())


class NoConversionTest(unittest.TestCase):
    def test_loading_turns_off_the_safetensors_lookup(self):
        class Model:
            @staticmethod
            def from_pretrained(name, **kwargs):
                return "model"

        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("DISABLE_SAFETENSORS_CONVERSION", None)
            mouth._pretrained(Model)
            self.assertEqual(os.environ.get("DISABLE_SAFETENSORS_CONVERSION"), "1")
