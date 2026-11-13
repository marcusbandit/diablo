import io
import sys
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import tts


def make_wav(seconds, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeClock:
    """Monotonic time that only moves when someone sleeps."""

    def __init__(self):
        self.now = 0.0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def sleep(self, seconds):
        with self._lock:
            self.now += seconds


class FakeProc:
    """A player that finishes the instant it starts."""

    def __init__(self):
        self.killed = False

    def poll(self):
        return 0

    def kill(self):
        self.killed = True

    def wait(self):
        return 0


class FakeCapture:
    def __init__(self):
        self.interrupted = threading.Event()
        self.playing = False

    def begin_playback(self):
        self.playing = True

    def end_playback(self):
        self.playing = False


class FakeHolder:
    """Stands in for speakers.SoundHold so a test never touches the real sound."""

    def __init__(self):
        self.users = 0
        self.log = []

    def acquire(self, mode="gvoice", dim_to=None):
        self.users += 1
        self.log.append(("acquire", mode, dim_to))

    def release(self, gap=None):
        self.users -= 1
        self.log.append(("release", gap))


def make_speaker(clock, wavs, gap=2.0, spawn=None, sleep=None, hold=None):
    speaker = tts.Speaker(
        {},
        FakeCapture(),
        hold=hold or FakeHolder(),
        clock=clock,
        sleep=sleep or clock.sleep,
        gap=lambda: gap,
        spawn=spawn or (lambda path: FakeProc()),
    )
    speaker._cloud_wav = lambda text, cfg: wavs[text]
    return speaker


class TestWavLength(unittest.TestCase):
    def test_reads_length_from_a_well_formed_header(self):
        self.assertAlmostEqual(tts._wav_seconds_bytes(make_wav(2.0)), 2.0, places=2)

    def test_streamed_placeholder_header_is_bounded_by_the_bytes(self):
        wav = bytearray(make_wav(1.5))
        # Cartesia's streaming header: RIFF and data sizes both 0xFFFFFFFF.
        wav[4:8] = b"\xff\xff\xff\xff"
        wav[40:44] = b"\xff\xff\xff\xff"
        seconds = tts._wav_seconds_bytes(bytes(wav))
        self.assertGreater(seconds, 1.49)
        self.assertLess(seconds, 1.51)

    def test_garbage_reads_as_zero(self):
        self.assertEqual(tts._wav_seconds_bytes(b"not a wav"), 0.0)


class TestSpeakerTurns(unittest.TestCase):
    def setUp(self):
        for target in ("voice.state.is_deaf", "voice.state.is_muted"):
            patcher = mock.patch(target, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_holds_for_the_clip_length_even_if_the_player_exits_early(self):
        clock = FakeClock()
        speaker = make_speaker(clock, {"hello sir": make_wav(3.0)})
        state, elapsed, duration = speaker.say("hello sir", {})
        self.assertEqual(state, "done")
        self.assertAlmostEqual(duration, 3.0)
        self.assertGreaterEqual(elapsed, 3.0)
        self.assertLess(elapsed, 3.2)

    def test_next_utterance_waits_clip_length_plus_gap(self):
        clock = FakeClock()
        starts = []

        def spawn(path):
            starts.append(clock())
            return FakeProc()

        speaker = make_speaker(
            clock, {"one": make_wav(2.0), "two": make_wav(1.0)}, gap=1.8, spawn=spawn
        )
        speaker.say("one", {})
        speaker.say("two", {})
        self.assertEqual(len(starts), 2)
        # Second clip starts no sooner than first start + its length + the gap.
        self.assertGreaterEqual(starts[1] - starts[0], 2.0 + 1.8)
        self.assertLess(starts[1] - starts[0], 2.0 + 1.8 + 0.2)

    def test_gap_is_drawn_from_the_natural_range(self):
        gaps = {tts.Speaker({}, FakeCapture())._gap() for _ in range(200)}
        self.assertTrue(all(1.5 <= g <= 2.5 for g in gaps))
        self.assertGreater(len(gaps), 1)

    def test_concurrent_callers_never_overlap(self):
        intervals = []
        lock = threading.Lock()

        def spawn(path):
            with lock:
                intervals.append([time.monotonic(), None])
            return FakeProc()

        wavs = {f"line {i}": make_wav(0.05) for i in range(6)}
        speaker = tts.Speaker(
            {},
            FakeCapture(),
            gap=lambda: 0.1,
            spawn=spawn,
            hold=FakeHolder(),
        )
        speaker._cloud_wav = lambda text, cfg: wavs[text]

        def run(text):
            speaker.say(text, {})
            with lock:
                for entry in intervals:
                    if entry[1] is None:
                        entry[1] = time.monotonic()
                        break

        threads = [threading.Thread(target=run, args=(t,)) for t in wavs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len(intervals), 6)
        intervals.sort()
        for (_, end_a), (start_b, _) in zip(intervals, intervals[1:]):
            self.assertIsNotNone(end_a)
            self.assertGreaterEqual(start_b, end_a + 0.1 - 0.02)


class TestFetchFace(unittest.TestCase):
    def test_a_line_comes_with_its_measured_mouth_and_eyes(self):
        speaker = make_speaker(FakeClock(), {"hello sir": make_wav(1.0)})
        track = {"mouth": [(0.0, "oo"), (0.2, "ee")], "eyes": [(0.0, "happy")]}
        with mock.patch("voice.eyes.tag_soon", return_value=lambda wait: ["happy"]), \
                mock.patch("voice.mouth.face_for", return_value=track) as face_for:
            wav, got = speaker.fetch_face("hello sir", {})
        self.assertEqual(got, track)
        self.assertEqual(face_for.call_args.args[0], "hello sir")
        self.assertEqual(face_for.call_args.args[1], wav)
        self.assertEqual(face_for.call_args.args[2], ["happy"])

    def test_nothing_fetched_is_none(self):
        speaker = make_speaker(FakeClock(), {})
        speaker._fetch = lambda text, cfg: None
        with mock.patch("voice.eyes.tag_soon", return_value=lambda wait: []):
            self.assertIsNone(speaker.fetch_face("hello sir", {}))


class TestSayStream(unittest.TestCase):
    def setUp(self):
        for target in ("voice.state.is_deaf", "voice.state.is_muted"):
            patcher = mock.patch(target, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_sentences_play_in_order_with_a_breath_not_a_gap(self):
        clock = FakeClock()
        starts = []

        def spawn(path):
            starts.append(clock())
            return FakeProc()

        wavs = {"One.": make_wav(1.0), "Two.": make_wav(1.0)}
        speaker = make_speaker(clock, wavs, gap=2.0, spawn=spawn)
        state, elapsed, duration = speaker.say_stream(iter(["One.", "Two."]), {})
        self.assertEqual(state, "done")
        self.assertEqual(len(starts), 2)
        self.assertAlmostEqual(duration, 2.0)
        between = starts[1] - starts[0]
        self.assertGreaterEqual(between, 1.0 + speaker.pause)
        self.assertLess(between, 1.0 + speaker.pause + 0.2)

    def test_gap_is_charged_once_after_the_whole_reply(self):
        clock = FakeClock()
        starts = []

        def spawn(path):
            starts.append(clock())
            return FakeProc()

        wavs = {"One.": make_wav(1.0), "Two.": make_wav(1.0), "Next reply.": make_wav(0.5)}
        speaker = make_speaker(clock, wavs, gap=2.0, spawn=spawn)
        speaker.say_stream(iter(["One.", "Two."]), {})
        end_of_reply = clock()
        speaker.say("Next reply.", {})
        self.assertGreaterEqual(starts[2] - end_of_reply, 2.0)

    def test_empty_stream_speaks_nothing_and_charges_no_gap(self):
        clock = FakeClock()
        speaker = make_speaker(clock, {})
        state, _, _ = speaker.say_stream(iter([]), {})
        self.assertEqual(state, "unspoken")
        self.assertEqual(speaker._clear_at, 0.0)
        self.assertFalse(speaker.capture.playing)


class TestStopNow(unittest.TestCase):
    def setUp(self):
        for target, value in (("voice.state.is_deaf", False), ("voice.state.is_muted", False)):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stop_now_kills_the_clip_and_skips_the_rest(self):
        clock = FakeClock()
        procs = []

        class SlowProc(FakeProc):
            def poll(self):
                return 0 if self.killed else None

        def spawn(path):
            procs.append(SlowProc())
            return procs[-1]

        wavs = {"One.": make_wav(5.0), "Two.": make_wav(1.0)}
        speaker = make_speaker(clock, wavs, spawn=spawn)
        ticks = {"n": 0}

        def sleep(seconds):
            clock.sleep(seconds)
            ticks["n"] += 1
            if ticks["n"] == 5:
                self.assertTrue(speaker.stop_now())
        speaker._sleep = sleep
        state, elapsed, _ = speaker.say_stream(iter(["One.", "Two."]), {})
        self.assertEqual(state, "interrupted")
        self.assertEqual(len(procs), 1)
        self.assertTrue(procs[0].killed)
        self.assertLess(elapsed, 1.0)

    def test_stop_now_is_a_no_op_when_silent(self):
        speaker = make_speaker(FakeClock(), {})
        self.assertFalse(speaker.stop_now())
        self.assertFalse(speaker._killed.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
