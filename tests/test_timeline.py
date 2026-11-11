"""The voice turn's timeline: points, the hub's split, the saved log and the waterfall."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voice import ears, timeline


_quiet = []


def setUpModule():
    """No sound and no toast on his desktop from a test run."""
    from voice import feedback

    for name in ("done", "failed", "back", "mode", "dictation", "piece", "thinking"):
        patch = mock.patch.object(feedback, name)
        patch.start()
        _quiet.append(patch)


def tearDownModule():
    for patch in _quiet:
        patch.stop()


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TestTimeline(unittest.TestCase):
    def test_points_count_from_the_name_and_the_first_mark_wins(self):
        clock = Clock()
        tl = timeline.Timeline("wake", start=99.5, clock=clock)
        tl.mark("name", at=99.5)
        tl.mark("chime")
        clock.now = 101.2
        tl.mark("chime")
        tl.mark("text")
        self.assertEqual(tl.points, {"name": 0, "chime": 500, "text": 1700})

    def test_rebase_moves_zero_to_his_first_word(self):
        clock = Clock()
        tl = timeline.Timeline("loop", clock=clock)
        tl.mark("speech_start", at=104.0)
        tl.mark("text", at=106.5)
        tl.rebase("speech_start")
        self.assertEqual(tl.points, {"speech_start": 0, "text": 2500})

    def test_saved_turns_load_and_draw(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "turns.jsonl"
            tl = timeline.Timeline("wake", start=0.0, clock=lambda: 0.0)
            for name, at in [("name", 0), ("fired", 0.3), ("chime", 0.35), ("speech_start", 0.4),
                             ("speech_end", 1.4), ("turn_end", 3.4), ("voice_ok", 3.5), ("text", 3.5),
                             ("hub_sent", 3.5), ("hub_back", 3.9), ("act_done", 3.91)]:
                tl.mark(name, at=at)
            tl.note(text="Louder.", hub={"jev_ms": 300, "brain_ms": 360, "backend": "direct", "model": "desktop"},
                    outcome="did: turned the sound up")
            tl.save(path)
            rows = timeline.load(path)
        self.assertEqual(len(rows), 1)
        labels = [label for label, _, _ in timeline.steps(rows[0])]
        self.assertEqual(labels, ["wake word", "wake checks", "waiting for you", "you talking", "quiet wait",
                                  "voice check", "speech to text", "hub", "doing it"])
        report = timeline.text_report(rows)
        self.assertIn("quiet wait", report)
        self.assertIn("jev 300 ms", report)
        page = timeline.html_report(rows)
        self.assertIn("Louder.", page)
        self.assertIn("3.91 s", page)

    def test_the_cli_writes_a_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "t.html"
            with mock.patch.object(timeline, "load", return_value=[]):
                self.assertEqual(ears.timings_cli(["--html", str(out)]), 0)
            self.assertIn("No turns timed yet", out.read_text())
            self.assertEqual(ears.timings_cli(["--bogus"]), 1)


class TestAnswerMarks(unittest.TestCase):
    def test_hub_and_act_points_and_the_hub_split(self):
        tl = timeline.Timeline()
        found = {"reply": "x", "backend": "direct", "act": {"action": "mute", "did": "muted the sound"},
                 "timing": {"jev_ms": 280}}
        with mock.patch.object(ears, "ask", return_value=found), \
                mock.patch.object(ears.commands, "run", return_value=(True, "")):
            ears.answer_words({}, "mute", [], None, {}, tl)
        self.assertTrue({"hub_sent", "hub_back", "act_done"} <= set(tl.points))
        self.assertEqual(tl.info["hub"], {"jev_ms": 280})
        self.assertEqual(tl.info["outcome"], "did: muted the sound")


if __name__ == "__main__":
    unittest.main()


class TestWakeChecks(unittest.TestCase):
    def test_each_wake_check_draws_as_its_own_step(self):
        points = {"name": 0, "fired": 320, "read": 330, "clip": 332, "dictation": 333,
                  "echo": 335, "voiceprint": 360, "chime": 375}
        drawn = {label: end - start for label, start, end in timeline.steps({"points": points})}
        self.assertEqual(drawn["wake word"], 320)
        self.assertEqual(drawn["wake checks"], 55)
        self.assertEqual(drawn["mic queue"], 10)
        self.assertEqual(drawn["voiceprint"], 25)
        self.assertEqual(drawn["turn start"], 15)


class TestSettingsOffTheWakePath(unittest.TestCase):
    def test_only_the_first_read_waits_for_the_hub(self):
        import threading

        gate, calls = threading.Event(), []

        def fetch(cfg):
            calls.append(1)
            if len(calls) > 1:
                gate.wait(5)
            return {"voice": f"v{len(calls)}"}

        with mock.patch.object(ears.hubclient, "get_settings", side_effect=fetch):
            settings = ears.Settings({"hub_url": "http://hub"})
            self.assertEqual(settings.get()["voice"], "v1")
            settings.at -= 120
            self.assertEqual(settings.get()["voice"], "v1")
            self.assertEqual(settings.get()["voice"], "v1")
            gate.set()
            for _ in range(100):
                if settings.get()["voice"] == "v2":
                    break
                threading.Event().wait(0.01)
        self.assertEqual(settings.get()["voice"], "v2")
        self.assertEqual(len(calls), 2)


class TestVoiceprintThread(unittest.TestCase):
    def test_every_embed_runs_on_the_one_voiceprint_thread(self):
        import threading

        from voice import voiceid

        seen = []

        class Encoder:
            def embed_utterance(self, wav, **kwargs):
                seen.append(threading.current_thread().name)
                return wav

        with mock.patch.object(voiceid, "_encoder", Encoder()), mock.patch.object(voiceid, "_one_thread"), \
                mock.patch.object(voiceid, "_pool", None):
            voiceid.embed(1)
            other = threading.Thread(target=voiceid.embed, args=(2,))
            other.start()
            other.join(5)
            voiceid.embed(3)
            voiceid._worker().shutdown(wait=True)
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 1)
        self.assertTrue(seen[0].startswith("voiceprint"))


class TestLiveVoiceprintTimeout(unittest.TestCase):
    def test_a_held_up_voiceprint_thread_passes_the_wake_check(self):
        import numpy as np

        from voice import voiceid, wake

        import sys
        import types

        fake = types.SimpleNamespace(preprocess_wav=lambda a, source_sr: a)
        with mock.patch.dict(sys.modules, {"resemblyzer": fake}), \
                mock.patch.object(voiceid, "_load_encoder", return_value=object()), \
                mock.patch.object(voiceid, "embed", side_effect=TimeoutError) as embed:
            self.assertIsNone(wake._voice_embed(np.ones(16000, dtype=np.float32), timeout=0.1))
        self.assertEqual(embed.call_args.kwargs["timeout"], 0.1)

    def test_a_settings_refresh_that_raises_still_waits_a_minute(self):
        with mock.patch.object(ears.hubclient, "get_settings", side_effect=KeyError("hub_url")) as fetch:
            settings = ears.Settings({})
            settings.get()
            settings.get()
        self.assertEqual(fetch.call_count, 1)
        self.assertIsNotNone(settings.at)
