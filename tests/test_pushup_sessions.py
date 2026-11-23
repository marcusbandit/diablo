import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_pushup_rename import load_pushup


class TestDeskSessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = load_pushup(Path(self.tmp.name))
        self.cfg = {**self.m.load_config(), "sound": False}
        self.playing = []
        self.media = []

        def pause():
            out, self.playing[:] = list(self.playing), []
            self.media.append(("pause", [p["name"] for p in out]))
            return out

        def resume(ps):
            self.playing.extend(ps)
            self.media.append(("play", [p["name"] for p in ps]))

        for name, fn in (("pause_videos", pause), ("resume", resume)):
            patch = mock.patch.object(self.m, name, side_effect=fn)
            patch.start()
            self.addCleanup(patch.stop)

    def answers(self, *texts):
        replies = iter(json.dumps(t) for t in texts)
        calls = []

        def fake(args, env=None, stop=None):
            calls.append(args)
            return next(replies)

        return mock.patch.object(self.m, "overlay", side_effect=fake), calls

    def test_a_card_is_three_sets_with_a_rest_between(self):
        self.assertEqual(self.m.session_sets(self.cfg, "push-ups"), 3)
        self.m.log_answer({"result": "done", "did": 12, "exercise": "push-ups", "goal": 12}, None,
                          {"session_id": "desk-1", "set_no": 1})
        patch, calls = self.answers({"result": "rested"}, {"result": "done", "did": 10, "exercise": "push-ups"},
                                    {"result": "rested"}, {"result": "done", "did": 9, "exercise": "push-ups"})
        with patch:
            said, _ = self.m.run_session(self.cfg, None, "push-ups", "desk-1", None)
        self.assertEqual(said, "all 3 sets")
        sets = [e for e in self.m.read_local() if e.get("session_id") == "desk-1"]
        self.assertEqual([(e["set_no"], e["did"]) for e in sets], [(1, 12), (2, 10), (3, 9)])
        rest, card = calls[0], calls[1]
        self.assertEqual(rest[rest.index("--rest-for") + 1], "90")
        promised = rest[rest.index("--next") + 1]
        self.assertIn("Next: set 2 of 3", promised)
        self.assertIn(f", {json.loads(card[card.index('--options') + 1])[0]['goal']} push-ups", promised)
        self.assertEqual((card[card.index("--set-no") + 1], card[card.index("--set-of") + 1]), ("2", "3"))
        self.assertEqual(json.loads(card[card.index("--options") + 1])[0]["exercise"], "push-ups")

    def test_escape_on_the_rest_ends_the_session(self):
        self.m.log_answer({"result": "done", "did": 12, "exercise": "squats", "goal": 12}, None, {"session_id": "desk-2", "set_no": 1})
        patch, calls = self.answers({"result": "end"})
        with patch:
            said, _ = self.m.run_session(self.cfg, None, "squats", "desk-2", None)
        self.assertEqual(said, "ended after set 1 of 3")
        self.assertEqual(len(calls), 1)

    def test_later_cards_keep_the_paused_pill_and_receipts_come_from_the_last_card(self):
        self.m.log_answer({"result": "done", "did": 12, "exercise": "squats", "goal": 12}, None, {"session_id": "desk-4", "set_no": 1})
        patch, calls = self.answers({"result": "rested"}, {"result": "done", "did": 11, "exercise": "squats", "receipts": True},
                                    {"result": "end"})
        with patch, mock.patch.object(self.m, "pretty_player", side_effect=lambda p: p["name"]):
            said, wants = self.m.run_session(self.cfg, None, "squats", "desk-4", None, [{"name": "a video"}])
        self.assertEqual(said, "ended after set 2 of 3")
        self.assertTrue(wants)
        self.assertEqual(calls[1][calls[1].index("--paused") + 1], "a video")

    def test_the_video_plays_through_the_rest_and_pauses_for_the_next_set(self):
        self.m.log_answer({"result": "done", "did": 12, "exercise": "squats", "goal": 12}, None, {"session_id": "desk-5", "set_no": 1})
        paused = [{"name": "mpv", "url": ""}]
        patch, _ = self.answers({"result": "rested"}, {"result": "done", "did": 11, "exercise": "squats"},
                                {"result": "rested"}, {"result": "done", "did": 10, "exercise": "squats"})
        with patch:
            self.m.run_session(self.cfg, None, "squats", "desk-5", None, paused)
        self.assertEqual(self.media, [("play", ["mpv"]), ("pause", ["mpv"]), ("play", ["mpv"]), ("pause", ["mpv"])])
        self.assertEqual(paused, [{"name": "mpv", "url": ""}])

    def test_ending_on_the_rest_leaves_the_video_playing(self):
        self.m.log_answer({"result": "done", "did": 12, "exercise": "squats", "goal": 12}, None, {"session_id": "desk-6", "set_no": 1})
        paused = [{"name": "mpv", "url": ""}]
        patch, _ = self.answers({"result": "end"})
        with patch:
            self.m.run_session(self.cfg, None, "squats", "desk-6", None, paused)
        self.assertEqual(self.playing, [{"name": "mpv", "url": ""}])
        self.assertEqual(paused, [])

    def test_ending_on_the_rest_before_the_receipts_pauses_the_video(self):
        self.m.log_answer({"result": "done", "did": 12, "exercise": "squats", "goal": 12}, None, {"session_id": "desk-8", "set_no": 1})
        paused = [{"name": "mpv", "url": ""}]
        patch, _ = self.answers({"result": "rested"}, {"result": "done", "did": 11, "exercise": "squats", "receipts": True},
                                {"result": "end"})
        with patch:
            _, wants = self.m.run_session(self.cfg, None, "squats", "desk-8", None, paused)
        self.assertTrue(wants)
        self.assertEqual((self.playing, paused), ([], [{"name": "mpv", "url": ""}]))

    def test_a_lock_on_the_rest_pauses_the_video_until_unlock(self):
        self.m.log_answer({"result": "done", "did": 12, "exercise": "squats", "goal": 12}, None, {"session_id": "desk-7", "set_no": 1})
        paused = [{"name": "mpv", "url": ""}]
        with mock.patch.object(self.m, "overlay", return_value="stopped"):
            said, _ = self.m.run_session(self.cfg, None, "squats", "desk-7", None, paused)
        self.assertEqual(said, "locked")
        self.assertEqual((self.playing, paused), ([], [{"name": "mpv", "url": ""}]))

    def test_a_short_last_set_is_a_pass_not_a_skip(self):
        self.m.log_answer({"result": "done", "did": 12, "exercise": "squats", "goal": 12}, None, {"session_id": "desk-3", "set_no": 1})
        patch, _ = self.answers({"result": "rested"}, {"result": "skip", "exercise": "squats"})
        with patch:
            self.m.run_session(self.cfg, None, "squats", "desk-3", None)
        last = self.m.read_local()[-1]
        self.assertEqual((last["result"], last["set_no"]), ("pass", 2))

    def test_one_set_a_card_keeps_the_old_rhythm_and_sessions_come_further_apart(self):
        self.assertEqual(self.m.session_sets({**self.cfg, "sets": 1}, "push-ups"), 1)
        gaps = [self.m.next_interval({**self.cfg, "mode": "mixed"}, session=True) for _ in range(50)]
        self.assertTrue(all(120 * 60 <= g <= 180 * 60 for g in gaps))
        self.assertGreaterEqual(self.m.longest_gap({**self.cfg, "mode": "mixed"}), 180 * 60)
        single = [self.m.next_interval({**self.cfg, "mode": "mixed"}) for _ in range(50)]
        self.assertTrue(all(g <= self.cfg["interval_max"] * 60 for g in single))
        gaps = [self.m.next_interval({**self.cfg, "mode": "mixed", "sets": 1}) for _ in range(50)]
        self.assertTrue(all(self.cfg["interval_min"] * 60 <= g <= self.cfg["interval_max"] * 60 for g in gaps))


class TestHubReadsDeskSessions(unittest.TestCase):
    def test_the_desktop_cards_sets_show_as_one_session(self):
        from hub.modules import workout
        now = time.time()
        tail = [{"ts": now - 300 + 120 * i, "exercise": "push-ups", "reps": 12, "did": did, "result": "done",
                 "session_id": "desk-9", "set_no": i + 1} for i, did in enumerate((12, 10, 9))]
        tail.append({"ts": now - 4000, "exercise": "squats", "reps": 15, "did": 15, "result": "done"})
        [session] = workout.sessions([], tail)
        self.assertEqual((session["name"], session["sets"], session["source"]), ("Push-ups", [12, 10, 9], "desktop"))
        self.assertTrue(session["closed"])

    def test_a_desktop_session_is_never_the_open_one(self):
        from hub.modules import workout
        now = time.time()
        tail = [{"ts": now - 60, "exercise": "push-ups", "reps": 12, "did": 12, "result": "done", "session_id": "desk-8", "set_no": 1}]
        with mock.patch.object(workout, "log_tail", return_value=tail):
            self.assertIsNone(workout.current([], now))


if __name__ == "__main__":
    unittest.main()
