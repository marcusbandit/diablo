import datetime as dt
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_modules import HEADERS, ModuleCase, overview

from hub import db, modules
from hub.modules import exercises, workout

NOW = dt.datetime(2026, 3, 4, 15, 0).timestamp()


class TestNames(unittest.TestCase):
    def test_every_spelling_of_a_push_up_is_one_exercise(self):
        for text in ("pushup", "pushups", "Push-up", "Push-Ups", "push ups", "push up", "PUSH-UPS", "press-ups"):
            self.assertEqual(exercises.resolve(text), "push-ups", text)

    def test_every_id_and_name_resolves_to_itself(self):
        for ident, name, *_ in exercises.LIBRARY:
            self.assertEqual(exercises.resolve(ident), ident, ident)
            self.assertEqual(exercises.resolve(name), ident, name)

    def test_no_two_exercises_share_a_name(self):
        keys = [exercises.squash(name) for _ident, name, *_ in exercises.LIBRARY]
        self.assertEqual(len(keys), len(set(keys)))
        ids = [ident for ident, *_ in exercises.LIBRARY]
        self.assertEqual(len(ids), len(set(ids)))

    def test_names_are_sentence_case(self):
        for _ident, name, *_ in exercises.LIBRARY:
            self.assertEqual(name[0], name[0].upper(), name)
            self.assertEqual(name[1:], name[1:].lower() if name not in ("L-sit", "V-ups") else name[1:], name)

    def test_pushup_knows_every_move_under_the_name_we_send(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pushup"))
        import catalog
        for ident, *_ in exercises.LIBRARY:
            name = exercises.stored(ident)
            self.assertEqual(catalog.find(name), name, ident)
            self.assertTrue(catalog.info(name)["muscles"], ident)
        self.assertEqual(catalog.info("chin-ups")["parts"].get("push"), None)

    def test_pushups_own_keys_are_what_lands_in_the_inbox(self):
        self.assertEqual(exercises.stored("l-sit"), "L-sit")
        self.assertEqual(exercises.stored("inverted rows"), "inverted rows")

    def test_old_names_still_show(self):
        self.assertEqual(exercises.name_of("pushups"), "Push-ups")
        self.assertEqual(exercises.name_of("some odd move"), "Some odd move")

    def test_unknown_words_resolve_to_nothing(self):
        for text in ("", "nap", "these", "10"):
            self.assertIsNone(exercises.resolve(text), text)


class TestSearch(unittest.TestCase):
    def names(self, query):
        return [hit["name"] for hit in exercises.search(query)]

    def test_an_exact_name_comes_first(self):
        self.assertEqual(self.names("pull-ups")[0], "Pull-ups")
        self.assertEqual(self.names("rows")[0], "Inverted rows")
        self.assertEqual(exercises.resolve("body rows"), "inverted rows")
        self.assertEqual(exercises.name_of("body rows"), "Inverted rows")
        self.assertEqual(self.names("pul")[0], "Pull-ups")

    def test_a_start_of_a_word_finds_every_move_it_begins(self):
        names = self.names("bulg")
        self.assertEqual(names[0], "Bulgarian split squats")
        self.assertIn("Romanian deadlifts", self.names("dead"))
        self.assertIn("Dead hang", self.names("dead"))

    def test_slips_in_spelling_are_forgiven(self):
        self.assertIn("Push-ups", self.names("pushpus"))
        self.assertIn("Burpees", self.names("burpes"))

    def test_nothing_matches_nonsense(self):
        self.assertEqual(exercises.search("zzqx"), [])
        self.assertEqual(exercises.search(""), [])

    def test_the_tool_lists_recent_and_defaults_without_a_query(self):
        with mock.patch.object(workout, "recent", return_value=[exercises.entry("chin-ups")]):
            text = workout.exercises_text("")
        self.assertIn("recent: Chin-ups", text)
        self.assertIn("defaults: Push-ups", text)


class SetsCase(ModuleCase):
    def lines(self):
        return workout.inbox_lines()

    def write_inbox(self, *entries):
        self.inbox.mkdir(exist_ok=True)
        with open(self.inbox / "diablo-oldhost.jsonl", "a") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")


class TestSetsAndReps(SetsCase):
    def test_three_sets_of_one_exercise_are_one_session(self):
        written = workout.log({"kind": "sets", "exercise": "Push-up", "reps": [12, 10, 8]}, now=NOW)
        self.assertEqual([e["did"] for e in written], [12, 10, 8])
        self.assertEqual([e["set_no"] for e in written], [1, 2, 3])
        self.assertEqual(len({e["session_id"] for e in written}), 1)
        self.assertEqual({e["exercise"] for e in written}, {"push-ups"})
        self.assertEqual([e["ts"] for e in written], sorted(e["ts"] for e in written))
        self.assertLessEqual(written[-1]["ts"], NOW)
        [session] = workout.sessions(self.lines())
        self.assertEqual((session["name"], session["sets"]), ("Push-ups", [12, 10, 8]))

    def test_a_set_soon_after_joins_the_open_session(self):
        workout.log({"kind": "set", "exercise": "squats", "did": 15}, now=NOW)
        workout.log({"kind": "set", "exercise": "squats", "did": 12}, now=NOW + 300)
        [session] = workout.sessions(self.lines())
        self.assertEqual(session["sets"], [15, 12])
        self.assertEqual(workout.current(self.lines(), NOW + 300)["id"], session["id"])

    def test_a_batch_after_a_set_stays_in_order_and_never_in_the_future(self):
        workout.log({"kind": "set", "exercise": "push-ups", "did": 10}, now=NOW)
        workout.log({"kind": "sets", "reps": [8, 8, 8]}, now=NOW + 30)
        [session] = workout.sessions(self.lines())
        self.assertEqual(session["sets"], [10, 8, 8, 8])
        self.assertTrue(all(e["ts"] <= NOW + 30 for e in self.lines()))

    def test_a_backdated_batch_of_another_exercise_becomes_the_open_session(self):
        workout.log({"kind": "set", "exercise": "plank", "did": 40}, now=NOW)
        workout.log({"kind": "sets", "exercise": "squats", "reps": [12, 12, 12]}, now=NOW + 60)
        self.assertEqual(workout.current(self.lines(), NOW + 60)["exercise"], "squats")
        [entry] = workout.log({"kind": "set", "did": 10}, now=NOW + 120)
        self.assertEqual(entry["exercise"], "squats")

    def test_a_long_gap_closes_the_session(self):
        workout.log({"kind": "set", "exercise": "squats", "did": 15}, now=NOW)
        self.assertIsNone(workout.current(self.lines(), NOW + 21 * 60))
        workout.log({"kind": "set", "exercise": "squats", "did": 12}, now=NOW + 21 * 60)
        self.assertEqual([s["sets"] for s in workout.sessions(self.lines())], [[15], [12]])

    def test_the_gap_is_a_setting(self):
        db.set_setting("workout.session_gap_min", "45")
        workout.log({"kind": "set", "exercise": "squats", "did": 15}, now=NOW)
        self.assertIsNotNone(workout.current(self.lines(), NOW + 30 * 60))

    def test_another_exercise_starts_its_own_session(self):
        workout.log({"kind": "set", "exercise": "squats", "did": 15}, now=NOW)
        workout.log({"kind": "set", "exercise": "plank", "did": 40}, now=NOW + 60)
        found = workout.sessions(self.lines())
        self.assertEqual([s["exercise"] for s in found], ["squats", "plank"])
        self.assertTrue(found[1]["timed"])

    def test_saying_done_closes_it(self):
        workout.log({"kind": "set", "exercise": "lunges", "did": 10}, now=NOW)
        [close] = workout.log({"kind": "close"}, now=NOW + 60)
        self.assertEqual(close["result"], "close")
        self.assertIsNone(workout.current(self.lines(), NOW + 61))
        with self.assertRaisesRegex(ValueError, "no session is open"):
            workout.log({"kind": "close"}, now=NOW + 120)

    def test_a_closed_line_is_never_pending_for_pushup(self):
        workout.log({"kind": "set", "exercise": "lunges", "did": 10}, now=NOW)
        workout.log({"kind": "close"}, now=NOW + 60)
        self.assertEqual([e["result"] for e in workout.pending(0)], ["manual"])

    def test_an_unknown_exercise_is_refused_with_the_closest_names(self):
        with self.assertRaisesRegex(ValueError, "closest: Push-ups"):
            workout.log({"kind": "set", "exercise": "pushpus", "did": 10}, now=NOW)
        self.assertEqual(self.lines(), [])

    def test_bad_sets_are_refused(self):
        for args in (
            {"kind": "sets", "exercise": "squats", "reps": []},
            {"kind": "sets", "exercise": "squats", "reps": [10, 0]},
            {"kind": "sets", "exercise": "squats", "reps": [10] * 21},
            {"kind": "sets", "exercise": "squats", "reps": "3x10"},
            {"kind": "sets", "exercise": "squats", "reps": [True]},
        ):
            with self.assertRaises(ValueError, msg=args):
                workout.log(args, now=NOW)
        self.assertEqual(self.lines(), [])

    def test_the_tool_reply_names_the_set_and_the_session(self):
        modules.call("workout_log", {"kind": "set", "exercise": "push-ups", "did": 10})
        said = modules.call("workout_log", {"kind": "set", "did": 8})
        self.assertIn("logged set 2 of push-ups: 8 (this session: 10, 8)", said)
        self.assertIn("closed the push-ups session: 2 sets, 10, 8", modules.call("workout_log", {"kind": "close"}))


class TestTheseOnes(SetsCase):
    def test_i_did_ten_of_these_goes_on_the_open_session(self):
        workout.log({"kind": "set", "exercise": "inverted rows", "did": 8}, now=NOW)
        for exercise in (None, "these", "THESE", "the same"):
            args = {"kind": "set", "did": 10, **({"exercise": exercise} if exercise else {})}
            [entry] = workout.log(args, now=NOW + 60)
            self.assertEqual(entry["exercise"], "inverted rows")
        [session] = workout.sessions(self.lines())
        self.assertEqual(session["sets"], [8, 10, 10, 10, 10])

    def test_with_nothing_open_it_takes_the_last_exercise_from_the_overview(self):
        self.write_overview(last={"ts": NOW - 5000, "exercise": "pushups", "did": 14})
        [entry] = workout.log({"kind": "set", "did": 10}, now=NOW)
        self.assertEqual((entry["exercise"], entry["set_no"]), ("push-ups", 1))

    def test_a_newer_set_in_pushups_own_log_wins_over_the_overview(self):
        self.write_overview(last={"ts": NOW - 5000, "exercise": "push-ups", "did": 14})
        (self.state / "log.jsonl").write_text(
            json.dumps({"ts": NOW - 4000, "exercise": "squats", "reps": 20, "did": 18, "result": "done"}) + "\n")
        [entry] = workout.log({"kind": "set", "did": 10}, now=NOW)
        self.assertEqual(entry["exercise"], "squats")

    def test_an_old_inbox_line_counts_as_the_last_exercise(self):
        self.write_inbox({"id": "old-1", "ts": NOW - 3000, "result": "manual", "exercise": "Decline Push-Ups", "did": 9, "source": "diablo"})
        [entry] = workout.log({"kind": "set", "did": 10}, now=NOW)
        self.assertEqual(entry["exercise"], "decline push-ups")

    def test_with_nothing_logged_anywhere_it_asks_which_exercise(self):
        with self.assertRaisesRegex(ValueError, "say which exercise"):
            workout.log({"kind": "set", "did": 10}, now=NOW)
        self.assertIn("not logged", modules.call("workout_log", {"kind": "set", "did": 10}))


class TestOldEntries(SetsCase):
    def test_old_count_lines_still_read_and_show(self):
        old = {"id": "old-2", "ts": time.time() - 600, "logged": time.time() - 600, "result": "manual",
               "exercise": "pushups", "did": 12, "source": "diablo"}
        self.write_inbox(old)
        [session] = workout.sessions(self.lines())
        self.assertEqual((session["name"], session["sets"], session["closed"]), ("Push-ups", [12], True))
        self.assertIsNone(workout.current(self.lines(), time.time()))
        self.assertEqual(workout.describe(old), "12 push-ups")
        self.write_overview(generated=time.time() - 3600)
        queue = next(b for b in workout.panel({}) if b.get("link") == "queue")
        self.assertEqual(queue["items"][0]["title"], "12 push-ups")
        today = next(b for b in workout.panel({}) if b.get("title") == "Sessions today")
        self.assertEqual(today["items"][0]["title"], "Push-ups")

    def test_the_file_is_only_ever_appended_to(self):
        self.write_inbox({"id": "old-3", "ts": NOW - 600, "result": "manual", "exercise": "squats", "did": 5})
        before = (self.inbox / "diablo-oldhost.jsonl").read_text()
        workout.log({"kind": "sets", "exercise": "squats", "reps": [5, 5]}, now=NOW)
        self.assertEqual((self.inbox / "diablo-oldhost.jsonl").read_text(), before)


class TestApp(SetsCase):
    def test_the_app_logs_sets_through_act(self):
        reply = self.client.post("/v1/act", headers=HEADERS, json={
            "module": "workout", "action": "sets", "args": {"exercise": "squats", "reps": [20, 18]}, "section": "workout"})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertIn("logged 2 sets of squats: 20, 18", reply.json()["message"])
        self.assertEqual({e["source"] for e in self.lines()}, {"app"})
        logger = next(b for b in reply.json()["panel"]["objects"] if b["kind"] == "sets")
        self.assertEqual(logger["current"]["sets"], [20, 18])
        self.assertTrue(logger["current"]["open"])

    def test_a_bad_tap_is_a_400(self):
        reply = self.client.post("/v1/act", headers=HEADERS, json={"module": "workout", "action": "set", "args": {"exercise": "squats", "did": 0}})
        self.assertEqual(reply.status_code, 400)
        reply = self.client.post("/v1/act", headers=HEADERS, json={"module": "workout", "action": "nap", "args": {}})
        self.assertEqual(reply.status_code, 400)

    def test_the_logger_carries_recent_defaults_and_the_full_list(self):
        workout.log({"kind": "set", "exercise": "chin-ups", "did": 6})
        logger = next(b for b in workout.panel({}) if b["kind"] == "sets")
        self.assertEqual([e["name"] for e in logger["recent"]], ["Chin-ups"])
        self.assertIn("Push-ups", [e["name"] for e in logger["defaults"]])
        self.assertEqual(len(logger["library"]), len(exercises.LIBRARY))
        self.assertEqual(logger["last"]["name"], "Chin-ups")
        self.assertEqual(logger["current"]["suggest"], 6)

    def test_an_open_session_offers_another_set_and_done(self):
        workout.log({"kind": "set", "exercise": "plank", "did": 45})
        items = next(b for b in workout.panel({}) if b["kind"] == "actions")["items"]
        self.assertEqual(items[0]["label"], "Add a set of 45 s")
        self.assertEqual(items[0]["act"], {"module": "workout", "action": "set", "args": {"did": 45}})
        self.assertEqual(items[1]["act"]["action"], "close")


class TestPlan(SetsCase):
    MONDAY = dt.datetime(2026, 3, 2, 8, 0).timestamp()

    def test_sessions_sit_inside_the_window_and_skip_the_rest_day(self):
        slots = workout.plan_slots(now=self.MONDAY, days=7)
        self.assertEqual(len(slots), 6 * 3)
        for slot in slots:
            at = dt.datetime.fromisoformat(slot["at"])
            self.assertTrue(dt.time(10) <= at.time() <= dt.time(20), slot)
            self.assertNotEqual(at.weekday(), 6)
        self.assertEqual([s["group"] for s in slots[:4]], ["push", "legs", "pull", "core"])

    def test_nothing_is_planned_in_the_past_or_the_next_half_hour(self):
        slots = workout.plan_slots(now=dt.datetime(2026, 3, 2, 14, 50).timestamp(), days=1)
        self.assertTrue(all(dt.datetime.fromisoformat(s["at"]) > dt.datetime(2026, 3, 2, 15, 20) for s in slots))

    def test_the_window_and_count_are_settings(self):
        db.set_setting("workout.window", "07:00-09:00")
        db.set_setting("workout.per_day", "2")
        db.set_setting("workout.rest_day", "none")
        slots = workout.plan_slots(now=dt.datetime(2026, 3, 1, 6, 0).timestamp(), days=7)
        self.assertEqual(len(slots), 14)
        self.assertTrue(all(dt.time(7) <= dt.datetime.fromisoformat(s["at"]).time() <= dt.time(9) for s in slots))

    def test_reps_follow_his_recent_sets(self):
        workout.log({"kind": "sets", "exercise": "push-ups", "reps": [14, 16, 15]}, now=self.MONDAY - 3600)
        slot = next(s for s in workout.plan_slots(now=self.MONDAY, days=1) if s["group"] == "push")
        self.assertEqual((slot["exercise"], slot["reps"]), ("push-ups", 15))
        plank = next(s for s in workout.plan_slots(now=self.MONDAY, days=2) if s["group"] == "core")
        self.assertEqual((plank["exercise"], plank["reps"], plank["timed"]), ("plank", 30, True))

    def test_a_proposal_sets_nothing_and_schedule_goes_through_add_reminder(self):
        with mock.patch("hub.modules.reminders.call", return_value="set #1") as add:
            said = workout.plan({"days": 2}, now=self.MONDAY)
            add.assert_not_called()
            self.assertIn("proposed 6 sessions", said)
            said = workout.plan({"days": 2, "schedule": True}, now=self.MONDAY)
        names = {c.args[0] for c in add.call_args_list}
        self.assertEqual(names, {"add_reminder"})
        self.assertEqual(len(add.call_args_list), 7)
        self.assertTrue(add.call_args_list[-1].args[1]["task"])
        self.assertIn("Push-ups: 3 sets of 10", [c.args[1]["text"] for c in add.call_args_list])
        self.assertIn("set 6 of 6 reminders", said)

    def test_a_second_plan_is_refused_while_one_is_scheduled(self):
        later = self.MONDAY + 3 * 86400
        rows = [{"id": 7, "text": "Plan Marcus's next 7 days of workouts: call workout_plan", "next_due": later}]
        with mock.patch("hub.modules.reminders.upcoming", return_value=rows), \
                mock.patch("hub.modules.reminders.call", return_value="set #1") as add:
            said = workout.plan({"days": 2, "schedule": True}, now=self.MONDAY)
        add.assert_not_called()
        self.assertIn("already scheduled", said)

    def test_no_next_plan_when_every_slot_failed_and_a_failed_task_is_said(self):
        with mock.patch("hub.modules.reminders.upcoming", return_value=[]), \
                mock.patch("hub.modules.reminders.call", return_value="not set: down") as add:
            said = workout.plan({"days": 1, "schedule": True}, now=self.MONDAY)
        self.assertFalse(any(c.args[1].get("task") for c in add.call_args_list))
        self.assertIn("nothing will plan the next stretch", said)
        replies = iter(["set #1", "set #2", "set #3", "not set: no tasks here"])
        with mock.patch("hub.modules.reminders.upcoming", return_value=[]), \
                mock.patch("hub.modules.reminders.call", side_effect=lambda *_a: next(replies)):
            said = workout.plan({"days": 1, "schedule": True}, now=self.MONDAY)
        self.assertIn("was not set (not set: no tasks here)", said)


if __name__ == "__main__":
    unittest.main()
