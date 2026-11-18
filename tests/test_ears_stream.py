"""The ears speaking a streamed answer: sentence by sentence, each fetched while the one before
plays, cut off by his name, and no sentence twice."""

import json
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

    def test_list_items_left_unstreamed_are_said(self):
        heard, words = self.words(
            ["You have three reminders:"],
            {"reply": "You have three reminders:\n- dentist at 10\n- call mum\n- gym",
             "spoken_first": "You have three reminders:", "rest": "- dentist at 10\n- call mum\n- gym",
             "backend": "claude"})
        self.assertEqual(words, "dentist at 10. call mum. gym.")

    def test_a_closing_line_inside_a_streamed_one_is_said(self):
        heard, words = self.words(
            ["Checking whether the build is done."],
            {"reply": "Checking whether the build is done.\n\nDone.", "spoken_first": "Checking whether the build is done.",
             "rest": "Done.", "backend": "claude"})
        self.assertEqual(words, "Done.")

    def test_the_rest_shares_the_spoken_length_with_the_streamed_lines(self):
        line = "This is one sentence of a long answer that goes on for quite a while indeed."
        streamed = [line] * 8
        rest = " ".join(f"Item number {n} is on the list." for n in range(20))
        heard, words = self.words(streamed, {"reply": " ".join(streamed) + " " + rest, "spoken_first": line,
                                             "rest": rest, "backend": "claude"})
        self.assertEqual(heard, streamed)
        self.assertTrue(words.endswith("The rest is in the chat."))
        spoken_words = words[: -len("The rest is in the chat.")]
        self.assertLessEqual(sum(len(h) + 1 for h in heard) + len(spoken_words), ears.SPOKEN_CHARS)

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

    def test_the_rest_of_a_finished_answer_joins_the_same_reply(self):
        def work(say):
            say("I run your lights.")
            time.sleep(0.1)
            return "Shall I show you? It takes half a minute."

        job = self.job(work)
        time.sleep(0.02)
        [(found, early)] = self.group.take_early()
        with mock.patch.object(ears, "FOLLOW_S", 1.0):
            lines = list(ears.following(found, early))
        self.assertEqual(lines, ["I run your lights.", "Shall I show you?", "It takes half a minute."])
        self.assertEqual(job["words"], "")

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



class TestTimings(unittest.TestCase):
    def test_the_first_sentence_and_the_first_sound_are_marked(self):
        tl = timeline.Timeline()
        with mock.patch.object(ears, "ask", side_effect=asked(["One."], {"reply": "One.", "spoken_first": "One.",
                                                                         "rest": "", "backend": "claude"})):
            ears.answer_words({}, "x", [], None, {}, tl, lambda w: None)
        self.assertIn("first_sentence", tl.points)

        class Speaker:
            def say_stream(self, lines, cfg, interruptible=False, on_heard=None, on_done=None, on_ready=None):
                list(lines)
                on_heard()

        with mock.patch.object(ears, "set_state"), mock.patch.object(ears.presence, "show_reply"):
            ears.say_answer(None, Speaker(), ["One.", "Two."], {}, None, None, tl)
        self.assertIn("first_audio", tl.points)
        self.assertIn("spoken", tl.points)

    def test_medians_from_the_end_of_his_speech(self):
        rows = [
            {"points": {"speech_end": 1000, "first_sentence": 3000, "first_audio": 3500, "spoken": 6000}},
            {"points": {"speech_end": 1000, "first_sentence": 5000, "first_audio": 5600, "beat_audio": 1900}},
            {"points": {"speech_end": 2000, "first_audio": 2400}},
            {"points": {"first_audio": 100}},
        ]
        got = {label: (count, median) for label, count, median in timeline.latency(rows)}
        self.assertEqual(got["first sentence back"], (2, 3000))
        self.assertEqual(got["first audio"], (3, 2500))
        self.assertEqual(got["beat heard"], (1, 900))
        self.assertEqual(got["answer spoken"], (1, 5000))
        self.assertIn("not measured yet", timeline.latency_report([{"points": {}}]))

    def test_the_cli_prints_the_medians(self):
        import io
        from contextlib import redirect_stdout

        rows = [{"points": {"speech_end": 0, "first_audio": 1200}}]
        out = io.StringIO()
        with mock.patch.object(timeline, "load", return_value=rows) as load, redirect_stdout(out):
            self.assertEqual(ears.timings_cli(["--medians"]), 0)
        load.assert_called_with(last=200)
        self.assertIn("first audio", out.getvalue())
        self.assertIn("1.20 s", out.getvalue())


if __name__ == "__main__":
    unittest.main()


class FollowUp(unittest.TestCase):
    def setUp(self):
        ears.REPLIED.clear()
        self.addCleanup(ears.REPLIED.clear)

    def test_an_answer_said_to_the_end_opens_the_window_once(self):
        ears.REPLIED.set()
        with mock.patch.object(ears.PENDING, "thinking", return_value=0), \
                mock.patch.object(ears.PENDING, "has_speech", return_value=False):
            self.assertEqual(ears.follow_up({}), ears.FOLLOW_UP_S)
            self.assertEqual(ears.follow_up({}), 0.0)

    def test_no_window_while_more_is_on_its_way_or_when_turned_off(self):
        ears.REPLIED.set()
        with mock.patch.object(ears.PENDING, "thinking", return_value=1), \
                mock.patch.object(ears.PENDING, "has_speech", return_value=False):
            self.assertEqual(ears.follow_up({}), 0.0)
        ears.REPLIED.set()
        with mock.patch.object(ears.PENDING, "thinking", return_value=0), \
                mock.patch.object(ears.PENDING, "has_speech", return_value=False):
            self.assertEqual(ears.follow_up({"ears.follow_s": "0"}), 0.0)

    def test_say_answer_marks_a_reply_heard_to_the_end(self):
        def spoken(mic, speaker, words, cfg, detector, tap, tl=None, first=False):
            ears._spoke["any"] = True
            return False

        with mock.patch.object(ears, "_say_answer", side_effect=spoken):
            self.assertFalse(ears.say_answer(None, None, "Done.", {}, None, None))
        self.assertTrue(ears.REPLIED.is_set())
        ears.REPLIED.clear()
        with mock.patch.object(ears, "_say_answer", return_value=True):
            ears._spoke["any"] = True
            self.assertTrue(ears.say_answer(None, None, "Done.", {}, None, None))
        self.assertFalse(ears.REPLIED.is_set())


class BeatFlag(unittest.TestCase):
    def test_the_hub_can_hold_the_beat_back(self):
        called = []
        lines = [json.dumps({"brain": "claude", "beat": False}), json.dumps({"done": {"reply": "Hi."}})]
        ears.read_lines(lines, None, lambda: called.append(1))
        self.assertEqual(called, [])
        lines = [json.dumps({"brain": "claude", "beat": True}), json.dumps({"done": {"reply": "Hi."}})]
        ears.read_lines(lines, None, lambda: called.append(1))
        self.assertEqual(called, [1])


class SaidLog(unittest.TestCase):
    def test_spoken_and_written_are_compared_sentence_by_sentence(self):
        rows = [{"at": 1.0, "text": "Nah, it's fine.", "beat": "But of course.",
                 "spoken": ["The offer stands whenever you want it."],
                 "written": "Very well. The offer stands whenever you want it."}]
        [row] = ears.said_rows(rows)
        self.assertEqual(row["spoken"], ["But of course.", "The offer stands whenever you want it."])
        self.assertEqual(row["only_said"], ["But of course."])
        self.assertEqual(row["only_wrote"], ["Very well."])

    def test_every_line_handed_to_his_voice_is_kept_in_order(self):
        tl = timeline.Timeline()
        list(ears.told(iter(["One.", "Two."]), tl, True, False))
        ears.spoke(tl, "Three.")
        self.assertEqual(tl.info["spoken"], ["One.", "Two.", "Three."])


class Dismiss(unittest.TestCase):
    def test_plain_goodbyes_need_no_one_to_read_them(self):
        with mock.patch.object(ears.jevfast, "ask") as asked:
            for line in ("Dismiss.", "That's all.", "That's all, thanks.", "Thanks, Diablo.", "go away", "nah I'm good",
                         "Shut up. Dismiss.", "Okay stop talking", "That's enough, thanks"):
                with self.subTest(line=line):
                    self.assertTrue(ears.dismissed(line))
        asked.assert_not_called()

    def test_jev_reads_every_other_way_of_saying_it(self):
        def read(state, questions, timeout=None, origin=""):
            away = "go away" in state or "be gone" in state.lower()
            return {"away": "away" if away and "how would" not in state else "other", "away_confidence": 0.95}

        with mock.patch.object(ears.jevfast, "ask", side_effect=read):
            self.assertTrue(ears.dismissed("I said go away."))
            self.assertTrue(ears.dismissed("Be gone."))
            self.assertFalse(ears.dismissed("If I wanted someone to go away, how would I make them do that?"))

    def test_an_unsure_or_failed_read_goes_on_to_the_brain(self):
        with mock.patch.object(ears.jevfast, "ask", return_value={"away": "away", "away_confidence": 0.5}):
            self.assertFalse(ears.dismissed("I said go away."))
        with mock.patch.object(ears.jevfast, "ask", return_value=None):
            self.assertFalse(ears.dismissed("I said go away."))

    def test_ordinary_lines_never_ask_jev(self):
        with mock.patch.object(ears.jevfast, "ask") as asked:
            for line in ("What's the weather tomorrow?", "Stop the music", "Stop.", "No.", "Close the window"):
                self.assertFalse(ears.dismissed(line))
        asked.assert_not_called()

    def test_dismissing_closes_the_window_and_clears_his_cards(self):
        ears.REPLIED.set()
        tl = timeline.Timeline()
        with mock.patch.object(ears.presence, "clear") as clear, mock.patch.object(ears, "set_state"), \
                mock.patch.object(ears.PENDING, "size", return_value=0), \
                mock.patch.object(ears.phrases, "pick", return_value=None), mock.patch.object(ears.chime, "play"):
            self.assertFalse(ears.dismiss({}, None, tl))
        clear.assert_called_once_with("reply")
        self.assertFalse(ears.REPLIED.is_set())
        self.assertEqual(tl.info["outcome"], "dismissed")


class CutDrops(unittest.TestCase):
    def test_a_cut_reply_says_nothing_more_of_that_job(self):
        group = burst.Burst()
        job = group.start(lambda say: "", timeline.Timeline(), "count the words", run=False)
        job["early"] = ["Queued."]
        group.drop(job)
        self.assertTrue(job["dropped"])
        self.assertEqual(job["early"], [])
