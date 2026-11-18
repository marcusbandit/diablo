"""The ears speaking a streamed answer: sentence by sentence, each fetched while the one before
plays, cut off by his name, and no sentence twice."""

import threading
import time
import unittest
from unittest import mock

import numpy as np

from voice import burst, ears, phrases, timeline, tts, wake


def asked(say_lines, done):
    def ask(cfg, text, context, shot, desktop, on_say=None):
        for line in say_lines:
            on_say(line)
        return done

    return ask


class TestNothingTwice(unittest.TestCase):
    def words(self, say_lines, done):
        heard = []
        with mock.patch.object(ears, "ask", side_effect=asked(say_lines, done)):
            words = ears.answer_words({}, "lights", [], None, {}, timeline.Timeline(), heard.append)
        return heard, words

    def test_every_streamed_sentence_is_handed_over_and_the_rest_is_what_is_left(self):
        heard, words = self.words(
            ["On it.", "The lamp is off."],
            {"reply": "On it.\n\nThe lamp is off. The panel is dimmed.", "spoken_first": "On it.",
             "rest": "The lamp is off. The panel is dimmed.", "backend": "claude"})
        self.assertEqual(heard, ["On it.", "The lamp is off."])
        self.assertEqual(words, "The panel is dimmed.")

    def test_an_answer_wholly_streamed_says_nothing_more(self):
        heard, words = self.words(
            ["Checking.", "It is sunny."],
            {"reply": "Checking.\n\nIt is sunny.", "spoken_first": "Checking.", "rest": "It is sunny.",
             "backend": "claude"})
        self.assertEqual(words, "")

    def test_a_sentence_with_a_link_streamed_is_not_said_again(self):
        heard, words = self.words(
            ["See https://example.com/a for it."],
            {"reply": "See https://example.com/a for it.", "spoken_first": "See https://example.com/a for it.",
             "rest": "See https://example.com/a for it.", "backend": "claude"})
        self.assertEqual(words, "")

    def test_without_streamed_lines_the_reply_is_said_as_before(self):
        heard, words = self.words([], {"reply": "Here:\n- one\n- two", "backend": "claude"})
        self.assertEqual(heard, [])
        self.assertEqual(words, ears.spoken("Here:\n- one\n- two"))



class Capture:
    interrupted = threading.Event()

    def begin_playback(self):
        pass

    def end_playback(self):
        pass


class PipeSpeaker(tts.Speaker):
    """A real Speaker whose fetch and playback only take time and write down when they ran."""

    def __init__(self, fetch_s=0.05, play_s=0.3):
        super().__init__({}, Capture(), gap=lambda: 0.0, spawn=lambda path: None)
        self.pause = 0.0
        self.fetch_s, self.play_s = fetch_s, play_s
        self.log, self.waits = [], []
        self.lock = threading.Lock()

    def note(self, what, text):
        with self.lock:
            self.log.append((what, text, time.monotonic()))

    def at(self, what, text):
        return next(t for w, x, t in self.log if (w, x) == (what, text))

    def did(self, what):
        return [x for w, x, _ in self.log if w == what]

    def prime(self):
        pass

    def _enter_hold(self, cfg):
        return None

    def fetch_face(self, text, cfg, eyes_wait=tts.EYES_WAIT):
        self.note("fetch", text)
        self.waits.append(eyes_wait)
        time.sleep(self.fetch_s)
        return text.encode(), {"mouth": [], "eyes": []}

    def _play(self, wav, interruptible, duration, track=None, on_heard=None):
        text = wav.decode()
        self.note("play", text)
        if on_heard is not None:
            on_heard()
        end = time.monotonic() + self.play_s
        while time.monotonic() < end:
            if self._killed.is_set():
                self.note("cut", text)
                return "interrupted", 0.0, duration
            time.sleep(0.005)
        self.note("end", text)
        return "done", self.play_s, duration


class Quiet(unittest.TestCase):
    def setUp(self):
        for target in ("voice.state.is_deaf", "voice.state.is_muted"):
            patcher = mock.patch(target, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("stopped", "playing"):
            patcher = mock.patch.object(tts.mouth, name)
            patcher.start()
            self.addCleanup(patcher.stop)


class TestPipelined(Quiet):
    def test_lines_play_in_order_and_the_next_is_fetched_while_one_plays(self):
        speaker = PipeSpeaker()
        heard = []
        state, _, _ = speaker.say_stream(iter(["One.", "Two.", "Three."]), {}, on_heard=lambda: heard.append(1))
        self.assertEqual(state, "done")
        self.assertEqual(speaker.did("play"), ["One.", "Two.", "Three."])
        self.assertEqual(speaker.did("fetch"), ["One.", "Two.", "Three."])
        self.assertLess(speaker.at("fetch", "Two."), speaker.at("end", "One."))
        self.assertLess(speaker.at("fetch", "Three."), speaker.at("end", "Two."))
        self.assertGreaterEqual(speaker.at("fetch", "Three."), speaker.at("play", "Two."))
        self.assertLess(speaker.at("play", "Two.") - speaker.at("end", "One."), 0.05)
        self.assertEqual(heard, [1])

    def test_the_first_line_never_waits_for_its_eyes(self):
        speaker = PipeSpeaker(play_s=0.05)
        speaker.say_stream(iter(["One.", "Two."]), {})
        self.assertEqual(speaker.waits, [0.0, tts.EYES_WAIT])

    def test_a_line_still_being_written_joins_the_reply(self):
        speaker = PipeSpeaker(play_s=0.2)

        def lines():
            yield "One."
            time.sleep(0.1)
            yield "Two."

        speaker.say_stream(lines(), {})
        self.assertEqual(speaker.did("play"), ["One.", "Two."])
        self.assertLess(speaker.at("play", "Two.") - speaker.at("end", "One."), 0.05)


class NameWhilePlaying:
    """Says his name once the speaker has started the given line."""

    threshold = 0.8

    def __init__(self, speaker, line):
        self.speaker, self.line, self.last = speaker, line, 0.0

    def feed(self, chunk):
        fire = self.line in self.speaker.did("play") and not self.last
        self.last = 0.95 if fire else self.last
        return fire

    def reset(self):
        pass


class Mic:
    def read(self, timeout=1.0):
        time.sleep(0.005)
        return time.monotonic(), np.zeros(wake.CHUNK, dtype=np.float32)

    def drain(self):
        pass


class TestBargeIn(Quiet):
    def test_his_name_stops_the_clip_and_drops_every_line_behind_it(self):
        speaker = PipeSpeaker(play_s=0.5)
        pulled = []

        def lines():
            for line in ("One.", "Two.", "Three.", "Four."):
                pulled.append(line)
                yield line

        with mock.patch.object(ears, "speaker_ok", return_value=(True, 0.9)):
            cut = ears.speak_until_named(Mic(), speaker, lines(), {}, NameWhilePlaying(speaker, "Two."), None)
        self.assertTrue(cut)
        self.assertEqual(speaker.did("play"), ["One.", "Two."])
        self.assertEqual(speaker.did("cut"), ["Two."])
        self.assertNotIn("Four.", pulled)
        time.sleep(0.2)
        self.assertNotIn("Three.", speaker.did("play"))

    def test_without_the_name_every_line_is_said(self):
        speaker = PipeSpeaker(play_s=0.05)
        detector = NameWhilePlaying(speaker, "never")
        self.assertFalse(ears.speak_until_named(Mic(), speaker, iter(["One.", "Two."]), {}, detector, None))
        self.assertEqual(speaker.did("play"), ["One.", "Two."])


class TestFollowing(unittest.TestCase):
    def setUp(self):
        self.group = burst.Burst()
        patcher = mock.patch.object(ears, "PENDING", self.group)
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, work):
        return self.group.start(work, timeline.Timeline(), "lights")

    def test_lines_keep_coming_while_the_next_is_close_and_wait_after_a_pause(self):
        gate = threading.Event()

        def work(say):
            say("One.")
            time.sleep(0.05)
            say("Two.")
            time.sleep(0.5)
            say("Three.")
            gate.wait(2)
            return ""

        job = self.job(work)
        time.sleep(0.01)
        [(found, early)] = self.group.take_early()
        with mock.patch.object(ears, "FOLLOW_S", 0.2):
            self.assertEqual(list(ears.following(found, early)), ["One.", "Two."])
        time.sleep(0.5)
        self.assertEqual(job["early"], ["Three."])
        gate.set()

    def test_a_first_line_is_said_without_waiting_out_the_inline_wait(self):
        gate = threading.Event()

        def work(say):
            say("On it.")
            gate.wait(2)
            return "Done."

        started = time.monotonic()
        with mock.patch.object(ears.feedback, "thinking") as chime, mock.patch.object(ears, "thinking_changed"):
            self.assertIsNone(ears.answer_or_background(work, timeline.Timeline(), "do it"))
        self.assertLess(time.monotonic() - started, 0.6)
        chime.assert_not_called()
        gate.set()

    def test_a_cut_drops_the_lines_waiting_but_not_those_written_after(self):
        gate = threading.Event()

        def work(say):
            say("One.")
            say("Two.")
            gate.wait(2)
            say("Later.")
            return ""

        job = self.job(work)
        time.sleep(0.01)
        self.group.take_early()
        job["early"].append("Queued.")
        self.group.drop_early(job)
        self.assertEqual(job["early"], [])
        gate.set()
        job["done"].wait(2)
        self.assertEqual(job["early"], ["Later."])


class TestLongReply(unittest.TestCase):
    def test_a_long_streamed_reply_stops_with_the_rest_in_the_chat(self):
        line = "This is one sentence of a long answer that goes on for quite a while indeed."
        lines = [line] * 12
        heard = []
        with mock.patch.object(ears, "ask", side_effect=asked(lines, {"reply": " ".join(lines), "spoken_first": line,
                                                                      "rest": "", "backend": "claude"})):
            words = ears.answer_words({}, "tell me", [], None, {}, timeline.Timeline(), heard.append)
        self.assertEqual(heard[-1], "The rest is in the chat.")
        self.assertLessEqual(sum(len(h) for h in heard[:-1]), ears.SPOKEN_CHARS)
        self.assertEqual(words, "")



class TestBeat(unittest.TestCase):
    def words(self, say_lines, done, beat="Very well."):
        heard, beats = [], []

        def ask(cfg, text, context, shot, desktop, on_say=None, on_brain=None):
            on_brain()
            for line in say_lines:
                on_say(line)
            return done

        def on_brain():
            beats.append(beat)
            return beat

        tl = timeline.Timeline()
        with mock.patch.object(ears, "ask", side_effect=ask):
            words = ears.answer_words({}, "do it", [], None, {}, tl, heard.append, on_brain)
        return heard, words, beats, tl

    def test_a_first_line_that_only_acknowledges_is_not_said_after_the_beat(self):
        heard, words, beats, tl = self.words(["On it, sir.", "The lamp is off."],
                                             {"reply": "On it, sir.\n\nThe lamp is off.", "spoken_first": "On it, sir.",
                                              "rest": "", "backend": "claude"})
        self.assertEqual(beats, ["Very well."])
        self.assertEqual(heard, ["The lamp is off."])
        self.assertEqual(tl.info["beat"], "Very well.")
        self.assertEqual(words, "")

    def test_a_first_line_keeps_what_follows_its_acknowledgement(self):
        heard, _, _, _ = self.words(["On it, checking the weather now.", "Right, sunny."],
                                    {"reply": "x", "spoken_first": "x", "rest": "", "backend": "claude"})
        self.assertEqual(heard, ["Checking the weather now.", "Right, sunny."])

    def test_without_a_beat_the_first_line_is_said_whole(self):
        heard, _, _, _ = self.words(["On it, sir."], {"reply": "On it, sir.", "spoken_first": "On it, sir.",
                                                      "rest": "", "backend": "claude"}, beat="")
        self.assertEqual(heard, ["On it, sir."])

    def test_read_lines_hands_the_brain_line_over(self):
        import json

        seen = []
        lines = [json.dumps({"brain": "claude"}), json.dumps({"say": "Hi."}), json.dumps({"done": {"reply": "Hi."}})]
        self.assertEqual(ears.read_lines(iter(lines), seen.append, lambda: seen.append("brain")), {"reply": "Hi."})
        self.assertEqual(seen, ["brain", "Hi."])

    def test_after_beat(self):
        self.assertEqual(phrases.after_beat("On it."), "")
        self.assertEqual(phrases.after_beat("Right away, sir."), "")
        self.assertEqual(phrases.after_beat("One moment. Checking."), "Checking.")
        self.assertEqual(phrases.after_beat("On it, launching that now."), "Launching that now.")
        self.assertEqual(phrases.after_beat("Right now the lamp is on."), "Right now the lamp is on.")
        self.assertEqual(phrases.after_beat("On iteration two it failed."), "On iteration two it failed.")


class BeatSpeaker:
    def __init__(self):
        self.speaking = False
        self.played = []
        self.done = threading.Event()

    def play_wav(self, wav, interruptible=False, on_heard=None):
        if on_heard:
            on_heard()
        self.played.append(wav)
        self.done.set()


class TestBeatFor(unittest.TestCase):
    def setUp(self):
        self.group = burst.Burst()
        patcher = mock.patch.object(ears, "PENDING", self.group)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("voice.state.is_muted", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_beat_plays_a_cached_line_at_once(self):
        speaker, tl = BeatSpeaker(), timeline.Timeline()
        self.group.start(lambda say: "", tl, "x", run=False)
        with mock.patch.object(ears.phrases, "pick", return_value=("At once.", b"wav")) as pick:
            self.assertEqual(ears.beat_for(speaker, {}, tl)(), "At once.")
            self.assertTrue(speaker.done.wait(2))
        pick.assert_called_with({}, "beat")
        self.assertEqual(speaker.played, [b"wav"])
        self.assertIn("beat_audio", tl.points)

    def test_no_beat_in_a_burst_while_speaking_or_switched_off(self):
        speaker, tl = BeatSpeaker(), timeline.Timeline()
        with mock.patch.object(ears.phrases, "pick", return_value=("At once.", b"wav")):
            self.group.start(lambda say: "", tl, "x", run=False)
            self.assertEqual(ears.beat_for(speaker, {"ears.beat": "0"}, tl)(), "")
            speaker.speaking = True
            self.assertEqual(ears.beat_for(speaker, {}, tl)(), "")
            speaker.speaking = False
            self.group.start(lambda say: "", tl, "y", run=False)
            self.assertEqual(ears.beat_for(speaker, {}, tl)(), "")
        self.assertEqual(speaker.played, [])


class TestBeatBank(unittest.TestCase):
    def test_the_beat_lines_are_short_and_fetched_for_the_voice(self):
        import tempfile
        from pathlib import Path

        self.assertTrue(4 <= len(phrases.BEAT) <= 6)
        self.assertTrue(all(len(line.split()) <= 4 for line in phrases.BEAT))
        fetched = []
        speaker = mock.Mock(_cloud_wav=lambda text, cfg: fetched.append(text) or b"RIFF")
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(phrases, "ROOT", Path(folder)):
            phrases.ensure({"tts_voice_id": "voice-a", "tts_provider": "cartesia"}, speaker)
            self.assertTrue(set(phrases.BEAT) <= set(fetched))
            text, wav = phrases.pick({"tts_voice_id": "voice-a", "tts_provider": "cartesia"}, "beat")
        self.assertIn(text, phrases.BEAT)


if __name__ == "__main__":
    unittest.main()
