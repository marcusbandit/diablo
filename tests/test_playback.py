import io
import sys
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import playback, tts


class Clock:
    def __init__(self):
        self.now = 100.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def sleep(self, seconds):
        with self.lock:
            self.now += seconds


class Stream:
    def __init__(self, rate, callback):
        self.rate, self.callback, self.closed = rate, callback, False

    def stop(self):
        pass

    def close(self):
        self.closed = True


def times(lead, current=50.0):
    return SimpleNamespace(outputBufferDacTime=current + lead, currentTime=current)


def pull(output, frames, lead=0.03):
    out = np.ones((frames, 1), dtype=np.float32)
    output._callback(out, frames, times(lead), None)
    return out[:, 0]


class TestOutputClock(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.streams = []

        def opener(rate, callback):
            self.streams.append(Stream(rate, callback))
            return self.streams[-1]

        self.output = playback.Output(opener=opener, clock=self.clock, wall=lambda: 1000.0 + self.clock(),
                                      idle_s=60)
        self.addCleanup(self.output.close)

    def test_the_first_sample_is_heard_one_device_latency_after_its_buffer_is_filled(self):
        clip = self.output.play(np.full(1000, 0.5), 1000)
        self.assertFalse(clip.started.is_set())
        pull(self.output, 400, lead=0.03)
        self.assertTrue(clip.started.is_set())
        self.assertAlmostEqual(clip.heard_at, 100.03)
        self.assertAlmostEqual(clip.heard_wall, 1100.03)

    def test_the_end_is_when_the_last_sample_is_heard_not_when_it_was_handed_over(self):
        clip = self.output.play(np.full(1000, 0.5), 1000)
        pull(self.output, 400)
        self.clock.sleep(0.4)
        pull(self.output, 400)
        self.clock.sleep(0.4)
        out = pull(self.output, 400, lead=0.03)
        self.assertTrue(clip.finished.is_set())
        self.assertAlmostEqual(clip.ended_at, 100.8 + 0.03 + 0.2)
        self.assertTrue(np.all(out[:200] == 0.5) and np.all(out[200:] == 0))

    def test_a_clip_queued_behind_another_starts_where_the_first_ends_inside_the_buffer(self):
        self.output.play(np.full(100, 0.5), 1000)
        second = self.output.play(np.full(100, 0.25), 1000)
        out = pull(self.output, 400, lead=0.02)
        self.assertAlmostEqual(second.heard_at, 100.0 + 0.02 + 0.1)
        self.assertAlmostEqual(second.ended_at, 100.0 + 0.02 + 0.2)
        self.assertTrue(np.all(out[100:200] == 0.25))

    def test_silence_plays_while_nothing_is_queued(self):
        self.output.prime(1000)
        self.assertTrue(np.all(pull(self.output, 64) == 0))

    def test_a_cut_goes_silent_from_the_next_buffer_and_is_over_when_that_buffer_is_heard(self):
        clip = self.output.play(np.full(5000, 0.5), 1000)
        pull(self.output, 400)
        self.clock.sleep(0.4)
        self.output.cut(clip)
        out = pull(self.output, 400, lead=0.03)
        self.assertTrue(np.all(out == 0))
        self.assertTrue(clip.finished.is_set())
        self.assertAlmostEqual(clip.ended_at, 100.43)

    def test_a_nonsense_latency_falls_back_to_the_configured_one(self):
        clip = self.output.play(np.full(100, 0.5), 1000)
        pull(self.output, 400, lead=-5.0)
        self.assertAlmostEqual(clip.heard_at, 100.0 + playback.LATENCY_S)

    def test_a_new_rate_opens_a_new_stream(self):
        self.output.prime(24000)
        self.output.play(np.zeros(10), 22050)
        self.assertEqual([s.rate for s in self.streams], [24000, 22050])
        self.assertTrue(self.streams[0].closed)

    def test_closing_ends_whatever_was_still_queued(self):
        clip = self.output.play(np.zeros(10000), 1000)
        self.output.close()
        self.assertTrue(clip.finished.is_set() and clip.cut)

    def test_the_stream_closes_once_idle(self):
        output = playback.Output(opener=lambda rate, cb: self.streams.append(Stream(rate, cb)) or self.streams[-1],
                                 idle_s=0.05)
        output.prime(1000)
        output._closer.join(1.0)
        self.assertTrue(self.streams[-1].closed)


def make_wav(seconds, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x10\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeOutput:
    """Plays each clip 80 ms after it is queued, for exactly its length, on the test clock."""

    def __init__(self, clock, lead=0.08):
        self.clock, self.lead = clock, lead
        self.cuts, self.primed, self.released = [], 0, 0

    def prime(self):
        self.primed += 1

    def release(self):
        self.released += 1

    def play(self, samples, rate):
        clip = playback.Clip(samples, rate)
        clip.heard_at = self.clock() + self.lead
        clip.heard_wall = 5000.0 + clip.heard_at
        clip.ended_at = clip.heard_at + clip.seconds
        clip.started.set()
        clip.finished.set()
        return clip

    def cut(self, clip):
        self.cuts.append(clip)
        clip.cut = True


class Capture:
    def __init__(self):
        self.interrupted = threading.Event()

    def begin_playback(self):
        pass

    def end_playback(self):
        pass


class TestSpeakerFollowsThePlaybackClock(unittest.TestCase):
    def setUp(self):
        for target in ("voice.state.is_deaf", "voice.state.is_muted"):
            patcher = mock.patch(target, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events = []
        self.clock = Clock()
        for name in ("playing", "stopped"):
            patcher = mock.patch.object(tts.mouth, name, side_effect=lambda *a, _n=name, **k: self.events.append(
                (_n, round(self.clock(), 3), k.get("at"))))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.output = FakeOutput(self.clock)
        self.speaker = tts.Speaker({}, Capture(), clock=self.clock, sleep=self.clock.sleep, gap=lambda: 0.0,
                                   hold=mock.Mock(), output=self.output)

    def test_the_face_comes_on_when_the_first_sample_is_heard_and_goes_off_with_the_last(self):
        heard = lambda: self.events.append(("heard", round(self.clock(), 3), None))
        done = lambda: self.events.append(("done", round(self.clock(), 3), None))
        state, elapsed, duration = self.speaker._speak([make_wav(1.0)], False, on_heard=heard, on_done=done)
        self.assertEqual(state, "done")
        self.assertEqual(self.events[0], ("playing", 100.0, 5100.08))
        self.assertEqual(self.events[1][0], "heard")
        self.assertAlmostEqual(self.events[1][1], 100.08, places=2)
        self.assertEqual(self.events[2][0], "done")
        self.assertAlmostEqual(self.events[2][1], 101.08, places=2)
        self.assertEqual(self.events[3][0], "stopped")
        self.assertAlmostEqual(elapsed, 1.0, places=2)

    def test_two_clips_of_one_reply_turn_the_face_on_once(self):
        calls = []
        self.speaker._speak([make_wav(0.3), make_wav(0.3)], False, on_heard=lambda: calls.append(self.clock()))
        self.assertEqual(len(calls), 1)
        anchors = [at for name, _, at in self.events if name == "playing"]
        self.assertEqual(len(anchors), 2)
        self.assertGreaterEqual(anchors[1] - anchors[0], 0.3 + self.speaker.pause)

    def test_a_stop_while_he_speaks_cuts_the_clip_and_ends_the_turn(self):
        self.output.play = self._slow_play
        stopper = threading.Timer(0.05, self.speaker.stop_now)
        stopper.start()
        self.addCleanup(stopper.cancel)
        state, _, _ = self.speaker._speak([make_wav(3.0)], False)
        self.assertEqual(state, "interrupted")
        self.assertEqual(len(self.output.cuts), 1)
        self.assertLess(self.clock(), 101.0)

    def _slow_play(self, samples, rate):
        clip = playback.Clip(samples, rate)
        clip.heard_at = self.clock() + 0.08
        clip.heard_wall = 5000.0 + clip.heard_at
        clip.started.set()
        original = self.output.cut

        def cut(c):
            original(c)
            c.ended_at = self.clock() + 0.03
            c.finished.set()

        self.output.cut = cut
        return clip

    def test_a_broken_stream_falls_back_to_the_spawned_player(self):
        self.output.play = mock.Mock(side_effect=OSError("no device"))
        self.speaker._play_spawned = mock.Mock(return_value=("done", 0.2, 0.2))
        state, _, _ = self.speaker._speak([make_wav(0.2)], False)
        self.assertEqual(state, "done")
        self.speaker._play_spawned.assert_called_once()

    def test_say_wakes_the_output_before_fetching(self):
        order = []
        self.output.prime = lambda: order.append("prime")
        self.speaker._cloud_wav = lambda text, cfg: order.append("fetch") or make_wav(0.1)
        with mock.patch.object(tts.mouth, "face_for", return_value={"mouth": [], "eyes": []}), \
                mock.patch.object(tts.eyes, "tag_soon", return_value=lambda wait: []):
            self.speaker.say("hello", {})
        self.assertEqual(order[:2], ["prime", "fetch"])


if __name__ == "__main__":
    unittest.main()
