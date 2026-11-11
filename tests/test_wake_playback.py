import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from voice import ears_api, wake


class TestFireFrames(unittest.TestCase):
    def test_frames_follow_the_live_rule(self):
        scores = [0, 0.99, 0.99, 0, 0, 0.99, 0.99]
        frames = wake.fire_frames(scores, 0.9, patience=2, refractory=0)
        self.assertEqual(frames, [2, 6])
        self.assertEqual(wake.count_fires(scores, 0.9, patience=2, refractory=0), 2)
        self.assertEqual(wake.fire_end_s(2), round((2 + wake.FRAMES + 8) * 0.08, 2))


class ClipCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name, value in (("WAKE_CLIPS", self.dir), ("WAKE_LOG", self.dir / "log.json")):
            patch = mock.patch.object(ears_api, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def controller(self):
        return ears_api.Controller(ears_api.Live(), None, lambda: True, None, lambda: 0, lambda: 0)


class TestKeepClip(ClipCase):
    def test_keeps_the_newest_only(self):
        with mock.patch.object(ears_api, "CLIPS_KEPT", 3):
            ids = [ears_api.keep_clip(np.zeros(1600, dtype=np.float32), at=1_800_000_000 + i) for i in range(5)]
        self.assertEqual(sorted(p.stem for p in self.dir.glob("*.wav")), ids[-3:])

    def test_wakes_survive_a_restart(self):
        live = ears_api.Live()
        live.wake(0.98, 0.7, "voice", "1800000000000")
        again = ears_api.Live()
        self.assertEqual(again.wakes[-1]["clip"], "1800000000000")
        self.assertEqual(again.wakes[-1]["outcome"], "voice")


class TestPlay(ClipCase):
    def test_plays_a_kept_wake_and_guards_the_mic(self):
        clip = ears_api.keep_clip(np.zeros(wake.RATE, dtype=np.float32))
        control = self.controller()
        with mock.patch.object(ears_api.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            code, body = control.play({"kind": "wake", "id": clip})
        self.assertEqual(code, 200)
        self.assertEqual(popen.call_args.args[0][0], "pw-play")
        self.assertTrue(control.playing())

    def test_refuses_paths_and_unknown_clips(self):
        control = self.controller()
        self.assertEqual(control.play({"kind": "wake", "id": "../../etc/passwd"})[0], 404)
        self.assertEqual(control.play({"kind": "wake", "id": "1800000000000"})[0], 404)
        self.assertEqual(control.play({"kind": "nope"})[0], 400)

    def test_talk_plays_only_what_the_model_named(self):
        control = self.controller()
        meta = {"talk_fire_clips": [{"clip": "a.wav", "end_s": 4.0}]}
        audio = np.zeros(wake.RATE * 10, dtype=np.float32)
        with mock.patch.object(wake, "meta", return_value=meta), \
                mock.patch.object(wake, "read_wav", return_value=audio), \
                mock.patch.object(ears_api.subprocess, "Popen"):
            self.assertEqual(control.play({"kind": "talk", "clip": "../secret.wav", "end_s": 4.0})[0], 404)
            code, body = control.play({"kind": "talk", "clip": "a.wav", "end_s": 4.0})
        self.assertEqual(code, 200)
        self.assertEqual(body["seconds"], ears_api.TALK_BEFORE_S + ears_api.TALK_AFTER_S)


class TestTailTraining(unittest.TestCase):
    def test_some_takes_run_straight_on_into_talk(self):
        rng = np.random.default_rng(1)
        word = 0.5 * np.sin(np.arange(int(0.5 * wake.RATE)) / 3).astype(np.float32)
        lead = 0.3 * np.ones(wake.RATE * 5, dtype=np.float32)
        after = []
        for _ in range(40):
            clip = wake.place(word, rng, None, lead, end_gap=0.3, quiet=True, tail=True)
            after.append(float(np.abs(clip[-int(0.2 * wake.RATE):]).max()))
        talked = sum(a > 0.01 for a in after)
        self.assertGreater(talked, 5)
        self.assertLess(talked, 35)

    def test_without_tail_the_end_stays_quiet(self):
        rng = np.random.default_rng(1)
        word = 0.5 * np.ones(int(0.5 * wake.RATE), dtype=np.float32)
        lead = 0.3 * np.ones(wake.RATE * 5, dtype=np.float32)
        clip = wake.place(word, rng, None, lead, end_gap=0.3, quiet=True)
        self.assertLess(float(np.abs(clip[-int(0.2 * wake.RATE):]).max()), 1e-6)


class TestLeadAudio(unittest.TestCase):
    def test_record_puts_the_lead_in_front(self):
        import queue as q

        from voice import ears

        class Mic:
            def __init__(self):
                self.blocks = [(10.0 + i * 0.08, np.full(wake.CHUNK, 0.1, dtype=np.float32)) for i in range(3)]

            def read(self, timeout=1.0):
                if not self.blocks:
                    raise q.Empty
                return self.blocks.pop(0)

        class Vad:
            def reset(self):
                pass

            def prob(self, chunk):
                return 0.0

        lead = np.full(wake.CHUNK * 4, 0.5, dtype=np.float32)
        with mock.patch.object(ears, "NO_SPEECH_S", 0.05):
            audio, spoke = ears.record(Mic(), {"silence_tail_s": 0.1}, 0.0, Vad(), start_audio=lead)
        self.assertEqual(float(audio[0]), 0.5)
        self.assertGreaterEqual(len(audio), len(lead))
