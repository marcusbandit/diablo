import queue as queue_mod
import threading
import unittest
from pathlib import Path

import numpy as np

from voice.capture import Capture, CHUNK


class FakeVAD:
    def __init__(self, probs):
        self.probs = list(probs)
        self.resets = 0

    def prob(self, chunk):
        return self.probs.pop(0) if self.probs else 0.1

    def reset(self):
        self.resets += 1


def voiced_chunk(level=0.004):    return np.full(CHUNK, level, dtype=np.float32)


def silent_chunk():
    return np.zeros(CHUNK, dtype=np.float32)


def make_capture(items, probs, engaged=True, playing=False):
    cap = Capture.__new__(Capture)
    cap.cfg = {"sample_rate": 16000, "silence_tail_s": 1.2, "max_utterance_s": 20, "idle_yield_s": 0.001}
    cap.vad = FakeVAD(probs)
    cap.pause_fn = None
    cap.engaged_fn = lambda: engaged
    cap.sample_rate = 16000
    cap.voice_floor = 0.0018
    cap.env = 0.0
    cap.release_floor = 0.004
    cap.idle_s = 0.001
    cap.queue = queue_mod.SimpleQueue()
    cap.monitor_active = True
    cap.deaf = False
    cap.playing = playing
    cap.interrupted = threading.Event()
    cap._paused = False
    cap._was_paused = False
    cap._reset = threading.Event()
    cap._running = True
    for item in items:
        cap.queue.put(item)
    return cap


def drain(cap, limit=300):
    gen = cap.utterances()
    for _ in range(limit):
        item = next(gen)
        if item is not None:
            return item
    return None


class TestUtteranceState(unittest.TestCase):
    def test_speech_becomes_one_utterance_while_engaged(self):
        items = [(voiced_chunk(), 0.0)] * 8 + [(silent_chunk(), 0.0)] * 45
        probs = [0.9] * 8 + [0.1] * 45
        cap = make_capture(items, probs)
        kind, utterance = drain(cap)
        self.assertEqual(kind, "utt")
        self.assertIsInstance(utterance, np.ndarray)
        seconds = len(utterance) / cap.sample_rate
        self.assertGreater(seconds, 0.5)
        self.assertLess(seconds, 3.0)

    def test_device_audio_explained_by_monitor_never_engages(self):
        items = [(voiced_chunk(), 0.95)] * 8 + [(silent_chunk(), 0.95)] * 45
        probs = [0.9] * 8 + [0.1] * 45
        cap = make_capture(items, probs)
        gen = cap.utterances()
        results = []
        for _ in range(300):
            item = next(gen)
            if item is not None:
                results.append(item)
                break
        self.assertEqual(results, [])

    def test_quiet_speech_below_voice_floor_is_ignored(self):
        items = [(voiced_chunk(level=0.0009), 0.0)] * 8 + [(silent_chunk(), 0.0)] * 45
        probs = [0.9] * 8 + [0.1] * 45
        cap = make_capture(items, probs)
        gen = cap.utterances()
        results = []
        for _ in range(300):
            item = next(gen)
            if item is not None:
                results.append(item)
                break
        self.assertEqual(results, [])

    def test_barge_in_engaged_stops_playback(self):
        items = [(voiced_chunk(), 0.0)] * 20
        probs = [0.9] * 20
        cap = make_capture(items, probs, playing=True)
        kind, _utterance = drain(cap)
        self.assertEqual(kind, "barge")
        self.assertFalse(cap.playing)
        self.assertTrue(cap.interrupted.is_set())

    def test_barge_in_disengaged_never_fires(self):
        items = [(voiced_chunk(), 0.0)] * 20
        probs = [0.9] * 20
        cap = make_capture(items, probs, engaged=False, playing=True)
        gen = cap.utterances()
        results = []
        for _ in range(300):
            item = next(gen)
            if item is not None:
                results.append(item)
                break
        self.assertEqual(results, [])
        self.assertTrue(cap.playing)
        self.assertFalse(cap.interrupted.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
