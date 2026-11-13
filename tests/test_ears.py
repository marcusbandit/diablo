import queue
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from voice import chime, ears, wake


class TestChime(unittest.TestCase):
    def test_sounds_are_short_soft_and_distinct(self):
        listen, done = chime.render("listen"), chime.render("done")
        for sound in (listen, done, chime.render("never")):
            self.assertLess(len(sound) / chime.RATE, 0.8)
            self.assertLessEqual(np.max(np.abs(sound)), chime.LEVEL + 1e-6)
        # Listening rises and done falls: the first 50 ms carry the lower and higher note.
        head = int(0.05 * chime.RATE)
        def pitch(x):
            spectrum = np.abs(np.fft.rfft(x[:head] * np.hanning(head)))
            return np.argmax(spectrum) * chime.RATE / head
        self.assertLess(pitch(listen), pitch(done))

    def test_no_click_at_the_start(self):
        self.assertLess(abs(chime.render("listen")[0]), 1e-3)


class TestTrim(unittest.TestCase):
    def test_finds_the_word_in_silence(self):
        audio = np.zeros(wake.RATE * 2, dtype=np.float32)
        audio[16000:24000] = 0.3 * np.sin(np.arange(8000) / 5)
        word = wake.trim(audio)
        self.assertGreater(len(word), 8000)
        self.assertLess(len(word), 8000 + 2 * int(0.08 * wake.RATE) + 400)

    def test_silence_is_empty(self):
        self.assertEqual(len(wake.trim(np.zeros(16000, dtype=np.float32))), 0)


class TestPlace(unittest.TestCase):
    def test_clip_is_two_seconds_and_ends_with_the_word(self):
        rng = np.random.default_rng(0)
        word = 0.3 * np.sin(np.arange(9000) / 4).astype(np.float32)
        clip = wake.place(word, rng, end_gap=0.1)
        self.assertEqual(len(clip), int(wake.CLIP_S * wake.RATE))
        tail = clip[-int(0.1 * wake.RATE):]
        body = clip[-int(0.5 * wake.RATE): -int(0.12 * wake.RATE)]
        self.assertGreater(np.std(body), np.std(tail))


class TestFiring(unittest.TestCase):
    def test_one_frame_over_never_fires(self):
        self.assertEqual(wake.count_fires([0.1, 0.9, 0.1, 0.9, 0.1], 0.5), 0)

    def test_two_frames_fire_once_then_refractory(self):
        scores = [0.9] * 10 + [0.0] * 30 + [0.9, 0.9]
        self.assertEqual(wake.count_fires(scores, 0.5), 2)
        self.assertEqual(wake.count_fires([0.9] * 20, 0.5), 1)

    def test_peak_run_is_the_highest_threshold_that_still_fires(self):
        scores = np.array([0.2, 0.7, 0.6, 0.95, 0.1])
        peak = wake._peak_run(scores)
        self.assertAlmostEqual(peak, 0.6)
        self.assertEqual(wake.count_fires(scores, peak), 1)
        self.assertEqual(wake.count_fires(scores, peak + 1e-6), 0)

    def test_stream_windows_slide_one_frame(self):
        frames = np.arange(20 * 96, dtype=np.float32).reshape(20, 96)
        win = wake.stream_windows(frames)
        self.assertEqual(win.shape, (5, 16, 96))
        np.testing.assert_array_equal(win[1], frames[1:17])


class FakeMic:
    def __init__(self, blocks):
        self.blocks = list(blocks)

    def read(self, timeout=1.0):
        if not self.blocks:
            raise AssertionError("record read past the end")
        return self.blocks.pop(0)


class FakeVAD:
    """Speech when the 512 sample piece is loud."""

    def reset(self):
        pass

    def prob(self, chunk):
        return 1.0 if np.max(np.abs(chunk)) > 0.1 else 0.0


def blocks(pattern, start=10.0):
    out, t = [], start
    for loud in pattern:
        out.append((t, np.full(wake.CHUNK, 0.5 if loud else 0.0, dtype=np.float32)))
        t += 0.08
    return out


class TestRecord(unittest.TestCase):
    def test_stops_after_the_silence_tail(self):
        # 1 s of speech then plenty of quiet: stops once 1.2 s of quiet has passed.
        mic = FakeMic(blocks([1] * 12 + [0] * 40))
        audio, spoke = ears.record(mic, {"silence_tail_s": 1.2}, after=0.0, vad=FakeVAD())
        self.assertTrue(spoke)
        seconds = len(audio) / wake.RATE
        self.assertGreater(seconds, 0.96 + 1.1)
        self.assertLess(seconds, 0.96 + 1.5)

    def test_blocks_before_the_chime_ended_are_dropped(self):
        mic = FakeMic(blocks([1] * 5, start=1.0) + blocks([1] * 6 + [0] * 20, start=20.0))
        audio, _ = ears.record(mic, {"silence_tail_s": 1.2}, after=10.0, vad=FakeVAD())
        self.assertLess(len(audio), 26 * wake.CHUNK + 1)

    def test_nothing_said_gives_up(self):
        import time

        real = time.monotonic
        clock = iter(np.arange(0, 100, 0.5))
        ears.time.monotonic = lambda: next(clock)
        try:
            mic = FakeMic(blocks([0] * 200))
            _, spoke = ears.record(mic, {}, after=0.0, vad=FakeVAD())
        finally:
            ears.time.monotonic = real
        self.assertFalse(spoke)


class TestReadyAnswerEndsTheWait(unittest.TestCase):
    """2026-11-13: in speech control a finished answer waited for his next line and then came
    out as the reply to it, so the centering answers kept playing after each new order."""

    def test_a_ready_answer_ends_the_wait_while_he_is_quiet(self):
        ready = iter([False] * 5 + [True] * 100)
        mic = FakeMic(blocks([0] * 200))
        _, spoke = ears.record(mic, {}, after=0.0, vad=FakeVAD(), no_speech_s=1e9, interrupt=lambda: next(ready))
        self.assertFalse(spoke)
        self.assertGreater(len(mic.blocks), 150)

    def test_his_line_is_never_cut_for_a_ready_answer(self):
        mic = FakeMic(blocks([0] * 3 + [1] * 12 + [0] * 40))
        audio, spoke = ears.record(mic, {"silence_tail_s": 1.2, "ears.adaptive": "0"}, after=0.0, vad=FakeVAD(),
                                   interrupt=lambda: True)
        self.assertFalse(spoke)
        mic = FakeMic(blocks([1] * 12 + [0] * 40))
        audio, spoke = ears.record(mic, {"silence_tail_s": 1.2, "ears.adaptive": "0"}, after=0.0, vad=FakeVAD(),
                                   interrupt=lambda: True)
        self.assertTrue(spoke)
        self.assertGreater(len(audio) / wake.RATE, 0.96)

    def test_speech_control_listens_with_the_pending_answers(self):
        from unittest import mock

        seen = {}

        def fake_record(*args, **kwargs):
            seen.update(kwargs)
            return ears.Heard(np.zeros(0, dtype=np.float32), False)

        settings = mock.Mock(get=lambda: {})
        with mock.patch.object(ears, "record", side_effect=fake_record), mock.patch.object(ears, "set_state"):
            self.assertIsNone(ears.loop_turn(None, settings, None, FakeVAD()))
        self.assertIs(seen["interrupt"].__self__, ears.PENDING)
        self.assertIs(seen["interrupt"].__func__, ears.Pending.has_speech)


class LineMic:
    """Two spoken lines on the wall clock, quiet between: line one from 0.2 s, line two from
    LINE_TWO_AT. Blocks are stamped with the real clock, as the capture thread does."""

    LINE_TWO_AT = 3.0

    def __init__(self, events, said):
        import time

        self.events, self.said, self.t0, self.two = events, said, time.monotonic(), False

    def read(self, timeout=1.0):
        import time

        if len(self.said) >= 2:
            raise RuntimeError("both answers are in")
        time.sleep(0.01)
        at = time.monotonic() - self.t0
        loud = 0.2 <= at < 1.0 or self.LINE_TWO_AT <= at < self.LINE_TWO_AT + 0.8
        if loud and at >= self.LINE_TWO_AT and not self.two:
            self.two = True
            self.events.append("line two starts")
        return time.monotonic(), np.full(wake.CHUNK, 0.5 if loud else 0.0, dtype=np.float32)

    def drain(self):
        pass


class TestSpeechControlAnswersInOrder(unittest.TestCase):
    """Two lines back to back in speech control, each answer slower than INLINE_WAIT_S: the
    first answer is spoken before the second line is heard, and each reply is its own line's."""

    def tearDown(self):
        ears.PENDING.jobs.clear()

    def test_each_answer_is_spoken_before_the_next_line_and_matches_it(self):
        import time
        from unittest import mock

        from voice import timeline

        events, said = [], []
        mic = LineMic(events, said)

        def transcribe(audio, cfg):
            return "line one" if time.monotonic() - mic.t0 < LineMic.LINE_TWO_AT else "line two"

        def answer_words(cfg, text, context, shot, desktop, tl=None, on_say=None):
            time.sleep(0.3)
            return f"answer to {text}"

        def say_answer(mic_, speaker, words, cfg, detector, tap, tl=None, first=False):
            said.append(words)
            events.append(f"said {words}")
            return False

        cfg = {"ears.adaptive": "0", "ears.loop_idle_s": 60}
        settings = mock.Mock(get=lambda: cfg)
        with mock.patch.object(ears, "INLINE_WAIT_S", 0.05), \
                mock.patch.object(ears, "listening", side_effect=lambda: len(said) < 2), \
                mock.patch.object(ears, "transcribe", side_effect=transcribe), \
                mock.patch.object(ears, "answer_words", side_effect=answer_words), \
                mock.patch.object(ears, "say_answer", side_effect=say_answer), \
                mock.patch.object(ears, "talk_ok", return_value=(True, 1.0)), \
                mock.patch.object(ears, "early_read", return_value={}), \
                mock.patch.object(ears, "control", return_value={}), \
                mock.patch.object(ears, "local_line", return_value=None), \
                mock.patch.object(ears, "set_state"), mock.patch.object(ears, "thinking_changed"), \
                mock.patch.object(ears, "feedback"), mock.patch.object(ears, "chime"), \
                mock.patch.object(ears, "recordings"), mock.patch.object(ears, "surroundings") as around, \
                mock.patch.object(ears.speakers, "Hold"), \
                mock.patch.object(ears.commands, "desktop_state", return_value={}), \
                mock.patch.object(ears.commands, "loop_intent", return_value=None), \
                mock.patch.object(timeline.Timeline, "save"), mock.patch.object(ears.log, "exception"):
            around.lines.return_value = []
            around.points_at_screen.return_value = False
            ears.voice_loop(mic, settings, None, FakeVAD())
        self.assertEqual(said, ["answer to line one", "answer to line two"])
        self.assertLess(events.index("said answer to line one"), events.index("line two starts"))


class TestSpoken(unittest.TestCase):
    def test_code_and_links_are_not_read_aloud(self):
        reply = "Done. Here:\n```sh\nrm -rf x\n```\nSee [the docs](https://x.y/z) or https://a.b."
        text = ears.spoken(reply)
        self.assertNotIn("rm -rf", text)
        self.assertNotIn("http", text)
        self.assertIn("the docs", text)

    def test_long_replies_stop_at_a_sentence(self):
        reply = " ".join(f"Sentence number {i} is here." for i in range(100))
        text = ears.spoken(reply)
        self.assertLess(len(text), ears.SPOKEN_CHARS + 40)
        self.assertTrue(text.endswith("The rest is in the chat."))


if __name__ == "__main__":
    unittest.main()


class SilentMic:
    def read(self, timeout=1.0):
        raise queue.Empty


class TestDeadMic(unittest.TestCase):
    def test_record_ends_when_no_audio_arrives(self):
        import time

        real = time.monotonic
        clock = iter(np.arange(0, 100, 1.0))
        ears.time.monotonic = lambda: next(clock)
        try:
            _, spoke = ears.record(SilentMic(), {}, after=0.0, vad=FakeVAD())
        finally:
            ears.time.monotonic = real
        self.assertFalse(spoke)


class FakeDetector:
    threshold = 0.8

    def __init__(self, scores):
        self.scores = list(scores)
        self.last = 0.0

    def feed(self, chunk):
        self.last = self.scores.pop(0) if self.scores else 0.0
        return False


class TestSpeakerEcho(unittest.TestCase):
    def tap(self, scores):
        from voice import speakers

        tap = speakers.Speakers.__new__(speakers.Speakers)
        import collections
        import threading

        tap.detector = FakeDetector(scores)
        tap.scores = collections.deque(maxlen=40)
        tap.levels = collections.deque(maxlen=40)
        tap.lock = threading.Lock()
        return tap

    def test_name_from_the_speakers_is_an_echo(self):
        tap = self.tap([0.1, 0.5, 0.2])
        for _ in range(3):
            tap.feed(np.full(wake.CHUNK, 0.2, dtype=np.float32))
        # 0.5 is under the mic threshold but over half of it: refuse, a loose firing costs more.
        self.assertTrue(tap.said_name())
        self.assertAlmostEqual(tap.level(), 0.2, places=5)

    def test_quiet_speakers_let_the_wake_through(self):
        tap = self.tap([0.0, 0.1, 0.0])
        for _ in range(3):
            tap.feed(np.zeros(wake.CHUNK, dtype=np.float32))
        self.assertFalse(tap.said_name())
        self.assertEqual(tap.level(), 0.0)


class TestHush(unittest.TestCase):
    def test_pauses_only_what_played_and_resumes_only_that(self):
        from unittest import mock

        from voice import speakers

        status = {"spotify": "Playing", "firefox": "Paused", "anibeam": "Playing"}
        calls = []

        def fake(*args):
            calls.append(args)
            if args[0] == "-a":
                return "".join(f"{name} {state}\n" for name, state in status.items())
            return ""

        with mock.patch.object(speakers, "_playerctl", side_effect=fake):
            with speakers.Hush() as hush:
                self.assertEqual(hush.paused, ["spotify", "anibeam"])
        self.assertIn(("-p", "spotify", "pause"), calls)
        self.assertIn(("-p", "anibeam", "play"), calls)
        self.assertNotIn(("-p", "firefox", "play"), calls)


class TestWindows(unittest.TestCase):
    def test_echo_lines_up_with_the_mic_block_stamp(self):
        import collections
        import threading

        from voice import speakers

        tap = speakers.Speakers.__new__(speakers.Speakers)
        tap.detector = FakeDetector([])
        tap.lock = threading.Lock()
        tap.scores = collections.deque([(100.0, 0.9)])
        tap.levels = collections.deque([(100.0, 0.3), (110.0, 0.01)])
        # The mic block that fired was stamped at 100.4, though it is checked much later.
        self.assertTrue(tap.said_name(at=100.4))
        self.assertFalse(tap.said_name(at=105.0))
        self.assertEqual(tap.level(since=105.0, until=111.0), 0.01)
        self.assertEqual(tap.level(since=99.0, until=101.0), 0.3)


class FakeSpeaker:
    def __init__(self, play_s=1.0, fetch_s=0.0):
        import threading

        self.play_s, self.fetch_s = play_s, fetch_s
        self.stopped = threading.Event()
        self.played = False

    def _fetch(self, words, cfg):
        import time

        time.sleep(self.fetch_s)
        return b"wav"

    def _speak(self, wavs, interruptible, cfg=None):
        self.played = True
        self.stopped.wait(self.play_s)

    def stop_now(self):
        self.stopped.set()
        return True


class FiringDetector:
    """Fires on the given block index, like Marcus saying the name over the reply."""

    threshold = 0.8

    def __init__(self, fire_at):
        self.fire_at, self.n, self.last = fire_at, 0, 0.0

    def feed(self, chunk):
        self.n += 1
        self.last = 0.95 if self.n == self.fire_at else 0.0
        return self.n == self.fire_at

    def reset(self):
        pass


class EndlessMic:
    def __init__(self):
        self.t = 0.0

    def read(self, timeout=1.0):
        import time

        time.sleep(0.005)
        self.t += 0.08
        return self.t, np.zeros(wake.CHUNK, dtype=np.float32)

    def drain(self):
        pass


class Tap:
    def __init__(self, echo):
        self.echo = echo

    def said_name(self, at=None):
        return self.echo


class TestBargeIn(unittest.TestCase):
    def run_reply(self, speaker, fire_at, echo):
        from unittest import mock

        with mock.patch.object(ears, "speaker_ok", return_value=(True, 0.9)):
            return ears.speak_until_named(EndlessMic(), speaker, "words", {}, FiringDetector(fire_at), Tap(echo))

    def test_saying_the_name_stops_him_and_starts_a_turn(self):
        speaker = FakeSpeaker(play_s=5.0)
        self.assertTrue(self.run_reply(speaker, fire_at=10, echo=False))
        self.assertTrue(speaker.stopped.is_set())

    def test_his_own_name_from_the_speakers_does_not_stop_him(self):
        speaker = FakeSpeaker(play_s=0.5)
        self.assertFalse(self.run_reply(speaker, fire_at=10, echo=True))
        self.assertFalse(speaker.stopped.is_set())

    def test_a_cut_during_the_fetch_means_nothing_plays(self):
        speaker = FakeSpeaker(play_s=5.0, fetch_s=0.4)
        self.assertTrue(self.run_reply(speaker, fire_at=3, echo=False))
        self.assertFalse(speaker.played)


class TestMicBlocks(unittest.TestCase):
    def test_four_20ms_pieces_make_one_80ms_block_and_the_level_is_the_newest(self):
        mic = ears.Mic.__new__(ears.Mic)
        mic.queue = queue.Queue()
        mic.level = 0.0
        mic._parts = []
        mic.denoiser = None
        for i in range(8):
            piece = np.full((ears.Mic.STEP, 1), 0.1 * (i + 1), dtype=np.float32)
            mic._callback(piece, ears.Mic.STEP, None, None)
        self.assertAlmostEqual(mic.level, 0.8, places=5)
        self.assertEqual(mic.queue.qsize(), 2)
        _, first = mic.queue.get()
        self.assertEqual(len(first), wake.CHUNK)
        self.assertAlmostEqual(float(first[0]), 0.1, places=5)
        self.assertAlmostEqual(float(first[-1]), 0.4, places=5)


class TestMicDrain(unittest.TestCase):
    def test_drain_drops_the_part_block_and_take_level_resets(self):
        mic = ears.Mic.__new__(ears.Mic)
        mic.queue = queue.Queue()
        mic.level = 0.0
        mic._parts = []
        mic.denoiser = None
        piece = np.full((ears.Mic.STEP, 1), 0.5, dtype=np.float32)
        mic._callback(piece, ears.Mic.STEP, None, None)
        mic.drain()
        self.assertEqual(mic._parts, [])
        self.assertAlmostEqual(mic.take_level(), 0.5, places=5)
        self.assertEqual(mic.take_level(), 0.0)


class TestHubReport(unittest.TestCase):
    def test_reports_each_change_and_says_off_when_it_stops(self):
        from unittest import mock

        sent = []
        with mock.patch.object(ears.hubclient, "report_ears", lambda cfg, state, detail="": sent.append(state) or True), \
                mock.patch.object(ears, "_last_state", "listening"):
            report = ears.HubReport({"hub_url": "http://hub"})
            report.poke()
            for _ in range(50):
                if sent:
                    break
                time.sleep(0.02)
            report.stop()
            report.stop()
        self.assertEqual(sent[0], "listening")
        self.assertEqual(sent[-1], "off")
        self.assertEqual(sent.count("off"), 1)

    def test_set_state_pokes_the_reporter(self):
        from unittest import mock

        fake = mock.Mock()
        with mock.patch.object(ears, "REPORT", fake), mock.patch.object(ears, "STATE_FILE", Path(tempfile.mkdtemp()) / "ears.json"), \
                mock.patch.object(ears, "_last_state", None):
            ears.set_state("thinking")
        fake.poke.assert_called_once()
