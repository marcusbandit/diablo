import queue as queue_mod
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import trainer
from voice.capture import Capture, CHUNK


class FakeSpeaker:
    def __init__(self):
        self.lines = []
        self.played = []

    def say(self, text, cfg, interruptible=False):
        self.lines.append(text)

    def play_wav(self, wav, interruptible=False):
        self.played.append(wav)


class FakeCapture:
    def __init__(self, rate=16000):
        self.sample_rate = rate
        self.taps = []


def loud(seconds=8, rate=16000, level=0.05):
    return np.full(seconds * rate, level, dtype=np.float32)


def quiet(seconds=8, rate=16000):
    return np.zeros(seconds * rate, dtype=np.float32)


def make_recorder(rounds):
    calls = []

    def record(capture, seconds):
        calls.append(seconds)
        return rounds.pop(0)

    record.calls = calls
    return record


def run(cfg=None, rounds=None, enroll=None):
    speaker = FakeSpeaker()
    capture = FakeCapture()
    progress = trainer.blank()
    enrolled = trainer.run_rounds(
        cfg or {},
        speaker,
        capture,
        progress,
        enroll_fn=enroll or (lambda audio, rate: np.zeros(256, dtype=np.float32)),
        record_fn=make_recorder(rounds or []),
        sleep=lambda seconds: None,
    )
    return speaker, progress, enrolled


class TestTraining(unittest.TestCase):
    def setUp(self):
        use_tmp_voices(self)

    def test_three_loud_rounds_enroll_three_times(self):
        speaker, progress, enrolled = run(rounds=[loud()] * 3)
        self.assertEqual(enrolled, 3)
        self.assertEqual(progress["enrolled"], 3)
        self.assertFalse(progress["active"])
        # He never speaks while Marcus trains; the countdown is the only sound.
        self.assertEqual(speaker.lines, [])
        self.assertEqual(progress["last"], "3 of 3 rounds added to default")

    def test_cached_countdown_is_played_before_every_round(self):
        wav = b"RIFFfake"
        with mock.patch("voice.trainer.phrases.get", return_value=("Three, two, one.", wav)):
            speaker, progress, enrolled = run(rounds=[loud()] * 3)
        self.assertEqual(speaker.played, [wav, wav, wav])
        self.assertEqual(speaker.lines, [])

    def test_without_a_cached_countdown_he_stays_silent(self):
        with mock.patch("voice.trainer.phrases.get", return_value=None):
            speaker, progress, enrolled = run(rounds=[loud()] * 3)
        self.assertEqual(speaker.played, [])
        self.assertEqual(speaker.lines, [])
        self.assertEqual(enrolled, 3)

    def test_quiet_round_is_retried_once_then_skipped(self):
        seen = []
        original = trainer.cue

        def spy(cfg, speaker, progress, sleep):
            seen.append(progress["last"])
            original(cfg, speaker, progress, sleep=sleep)

        with mock.patch("voice.trainer.cue", side_effect=spy):
            speaker, progress, enrolled = run(rounds=[quiet(), quiet(), loud(), loud()])
        self.assertEqual(enrolled, 2)
        self.assertIn("round 1: too quiet, once more a little louder", seen)
        self.assertEqual(speaker.lines, [])
        self.assertEqual(progress["last"], "2 of 3 rounds added to default")

    def test_enroll_failure_does_not_halt_the_session(self):
        attempts = {"n": 0}

        def flaky(audio, rate):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("encoder unavailable")
            return np.zeros(256, dtype=np.float32)

        speaker, progress, enrolled = run(rounds=[loud(), loud(), loud(), loud()], enroll=flaky)
        self.assertEqual(enrolled, 3)
        self.assertEqual(attempts["n"], 4)

    def test_record_collects_exactly_the_asked_seconds(self):
        tap = queue_mod.SimpleQueue()
        for _ in range(400):
            tap.put((np.ones(CHUNK, dtype=np.float32), 0.0))
        audio = trainer.collect(tap, 8, 16000)
        self.assertEqual(len(audio), 8 * 16000)

    def test_record_detaches_its_tap_when_the_stream_dies(self):
        capture = FakeCapture()
        with self.assertRaises(queue_mod.Empty):
            trainer.record(capture, 8, timeout=0.01)
        self.assertEqual(capture.taps, [])


class TestCaptureTap(unittest.TestCase):
    def test_callback_feeds_taps_alongside_the_main_queue(self):
        cap = Capture.__new__(Capture)
        cap.deaf = False
        cap.playing = False
        cap.monitor_active = False
        cap.env_decay = 0.97
        cap.env = 0.0
        cap.level = 0.0
        cap.raw = 0.0
        cap.queue = queue_mod.SimpleQueue()
        cap.taps = []
        block = np.ones((CHUNK, 1), dtype=np.float32)
        Capture._callback(cap, block, CHUNK, None, None)
        fed, _explained = cap.queue.get_nowait()
        self.assertEqual(len(fed), CHUNK)
        tap = queue_mod.SimpleQueue()
        cap.taps.append(tap)
        Capture._callback(cap, block, CHUNK, None, None)
        fed_tap, _explained = tap.get_nowait()
        self.assertEqual(len(fed_tap), CHUNK)


def use_tmp_voices(case):
    """Point voiceid at a fresh temp folder for the length of one test."""
    import voice.voiceid as voiceid

    tmp = Path(tempfile.mkdtemp())
    voices = tmp / "voices"
    voices.mkdir()
    for attr, value in (
        ("PROFILES", voices),
        ("ACTIVE", tmp / "voice-active"),
        ("LEGACY", tmp / "voice-profile.npz"),
    ):
        patcher = mock.patch.object(voiceid, attr, value)
        patcher.start()
        case.addCleanup(patcher.stop)
    return voices


def write_profile(path, n=2):
    np.savez(path, emb=np.ones(4, dtype=np.float32), n=n)


class TestDelete(unittest.TestCase):
    def setUp(self):
        self.voices = use_tmp_voices(self)
        self.profile = self.voices / "default.npz"

    def write_profile(self):
        write_profile(self.profile)

    def test_delete_requires_confirmation_first(self):
        self.write_profile()
        result = trainer.delete_profile(False, trainer.blank())
        self.assertTrue(result["needs_confirm"])
        self.assertFalse(result["ok"])
        self.assertTrue(self.profile.exists())

    def test_delete_removes_profile_only_on_confirm(self):
        self.write_profile()
        result = trainer.delete_profile(True, trainer.blank())
        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        self.assertFalse(self.profile.exists())
        again = trainer.delete_profile(True, trainer.blank())
        self.assertTrue(again["ok"])
        self.assertFalse(again["deleted"])

    def test_delete_refused_while_training(self):
        self.write_profile()
        progress = trainer.blank()
        progress["active"] = True
        result = trainer.delete_profile(True, progress)
        self.assertFalse(result["ok"])
        self.assertTrue(self.profile.exists())

    def test_state_reports_profile_samples(self):
        self.write_profile()
        data = trainer.state(trainer.blank())
        self.assertTrue(data["profile"]["exists"])
        self.assertEqual(data["profile"]["samples"], 2)
        self.assertFalse(data["active"])


class TestHistory(unittest.TestCase):
    def setUp(self):
        import voice.voiceid as voiceid

        self.voiceid = voiceid
        self.voices = use_tmp_voices(self)

    def test_session_is_recorded_with_rounds_and_seconds(self):
        speaker, progress, enrolled = run(rounds=[loud()] * 3)
        self.assertEqual(enrolled, 3)
        doc = self.voiceid.history("default")
        self.assertEqual(len(doc["sessions"]), 1)
        session = doc["sessions"][0]
        self.assertEqual(session["rounds"], 3)
        self.assertEqual(session["seconds"], 3 * trainer.SECONDS)
        self.assertRegex(session["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_session_with_nothing_added_records_nothing(self):
        run(rounds=[quiet()] * 6)
        self.assertFalse(self.voiceid.history_path("default").exists())
        self.assertEqual(self.voiceid.history("default"), {"sessions": []})

    def test_stats_aggregate_across_sessions(self):
        write_profile(self.voices / "Marcus.npz", n=5)
        self.voiceid.record_session("Marcus", 3, 8)
        self.voiceid.record_session("Marcus", 2, 8)
        stats = self.voiceid.stats("Marcus")
        self.assertEqual(stats["samples"], 5)
        self.assertEqual(stats["recordings"], 5)
        self.assertEqual(stats["sessions"], 2)
        self.assertEqual(stats["last"], self.voiceid.history("Marcus")["sessions"][-1]["at"])

    def test_stats_for_an_untrained_voice_are_zero(self):
        write_profile(self.voices / "captain.npz", n=1)
        stats = self.voiceid.stats("captain")
        self.assertEqual(stats, {"samples": 1, "recordings": 0, "sessions": 0, "last": None})

    def test_corrupt_history_reads_as_empty(self):
        self.voiceid.history_path("Marcus").write_text("{not json")
        self.assertEqual(self.voiceid.history("Marcus"), {"sessions": []})

    def test_delete_removes_the_history_too(self):
        write_profile(self.voices / "Marcus.npz")
        self.voiceid.record_session("Marcus", 3, 8)
        result = self.voiceid.delete("Marcus")
        self.assertTrue(result["deleted"])
        self.assertFalse(self.voiceid.history_path("Marcus").exists())

    def test_rename_moves_both_files_and_the_active_pointer(self):
        write_profile(self.voices / "old.npz")
        self.voiceid.record_session("old", 3, 8)
        self.voiceid.set_active("old")
        self.assertTrue(self.voiceid.rename("old", "new"))
        self.assertTrue((self.voices / "new.npz").exists())
        self.assertTrue((self.voices / "new.json").exists())
        self.assertFalse((self.voices / "old.npz").exists())
        self.assertFalse((self.voices / "old.json").exists())
        self.assertEqual(self.voiceid.active_name(), "new")
        self.assertEqual(self.voiceid.stats("new")["recordings"], 3)

    def test_rename_without_history_and_missing_profile(self):
        write_profile(self.voices / "lone.npz")
        self.voiceid.set_active("other")
        self.assertTrue(self.voiceid.rename("lone", "solo"))
        self.assertFalse((self.voices / "solo.json").exists())
        self.assertEqual(self.voiceid.active_name(), "other")
        self.assertFalse(self.voiceid.rename("ghost", "anything"))

    def test_migrate_backfills_one_session_from_the_blend(self):
        import voice.voiceid as voiceid

        write_profile(self.voices / "old.npz", n=5)
        voiceid.migrate()
        stats = voiceid.stats("old")
        self.assertEqual((stats["recordings"], stats["sessions"]), (5, 1))
        self.assertTrue(voiceid.history("old")["sessions"][0]["backfill"])
        voiceid.record_session("old", 3, 8)
        voiceid.migrate()
        self.assertEqual(voiceid.stats("old")["recordings"], 8)

    def test_idle_state_names_the_active_voice_not_the_last_trained(self):
        import voice.voiceid as voiceid

        write_profile(self.voices / "Marcus.npz")
        voiceid.set_active("Marcus")
        progress = trainer.blank()
        progress["name"] = "captain"
        self.assertEqual(trainer.state(progress)["name"], "Marcus")
        progress["active"] = True
        self.assertEqual(trainer.state(progress)["name"], "captain")

    def test_state_carries_voices_and_totals(self):
        write_profile(self.voices / "Marcus.npz", n=8)
        write_profile(self.voices / "Diablo.npz", n=3)
        self.voiceid.record_session("Marcus", 3, 8)
        self.voiceid.record_session("Marcus", 3, 8)
        self.voiceid.record_session("Diablo", 2, 8)
        data = trainer.state(trainer.blank())
        self.assertEqual(set(data["voices"]), {"Marcus", "Diablo"})
        self.assertEqual(data["voices"]["Marcus"]["samples"], 8)
        self.assertEqual(data["voices"]["Marcus"]["recordings"], 6)
        self.assertEqual(data["voices"]["Marcus"]["sessions"], 2)
        self.assertEqual(data["voices"]["Diablo"]["recordings"], 2)
        self.assertEqual(data["totals"], {"voices": 2, "recordings": 8, "sessions": 3})


if __name__ == "__main__":
    unittest.main(verbosity=2)
