import asyncio
import datetime
import itertools
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import agent, brains, charter, db, devices, events, lockguard, mcp_server, server, t3threads, tasks, watch

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
DIABLO = {"id": "p1", "title": "Diablo", "workspaceRoot": "/home/user/Projects/diablo",
          "repositoryIdentity": {"canonicalKey": "github.com/marcusbandit/diablo"}}
ANIBEAM = {"id": "p2", "title": "AniBeam", "workspaceRoot": "/home/user/Projects/anibeam",
           "repositoryIdentity": {"canonicalKey": "github.com/marcusbandit/anibeam"}}


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        tasks._kinds.clear()
        self.events = []
        publish = mock.patch.object(events, "publish", side_effect=self.events.append)
        publish.start()
        self.addCleanup(publish.stop)

    def task_events(self):
        return [e for e in self.events if e.get("kind") == "task"]


class TestMigration(unittest.TestCase):
    def test_an_old_database_gains_the_tasks_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "diablo.db")
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, source TEXT, role TEXT, text TEXT, meta TEXT)")
            old.execute("INSERT INTO messages (ts, source, role, text, meta) VALUES (1, 'telegram', 'user', 'hi', NULL)")
            old.commit()
            old.close()
            db.init(path)
            self.assertEqual(db.get_messages(5)[0]["text"], "hi")
            tasks.save("t1", {"title": "kept"})
            self.assertEqual(db.get_task("t1")["title"], "kept")


class TestSave(DbCase):
    def test_create_fills_every_field_and_publishes(self):
        task = tasks.save("t1", {"title": "Timer app", "kind": "partner", "machine": "nemurenai"}, now=100)
        self.assertEqual(set(task), {"id", "title", "kind", "machine", "state", "step", "started_at", "finished_at",
                                     "seen_at", "updated_at", "links", "result", "source", "ask"})
        self.assertIsNone(task["ask"])
        self.assertEqual(task["links"], {"pr": "", "pr_number": None, "pr_state": "", "issue": "", "thread": ""})
        self.assertEqual((task["state"], task["started_at"], task["finished_at"]), ("working", 100, None))
        self.assertEqual(self.task_events(), [{"kind": "task", "change": "created", "task": task}])

    def test_no_change_publishes_nothing(self):
        tasks.save("t1", {"title": "Timer app"}, now=100)
        tasks.save("t1", {"title": "Timer app"}, now=200)
        self.assertEqual(len(self.task_events()), 1)
        self.assertEqual(db.get_task("t1")["updated_at"], 100)

    def test_finishing_sets_finished_and_a_new_state_clears_seen(self):
        tasks.save("t1", {"title": "x"}, now=100)
        done = tasks.save("t1", {"state": "done"}, now=150)
        self.assertEqual(done["finished_at"], 150)
        self.assertEqual(tasks.seen("t1", now=160)["seen_at"], 160)
        again = tasks.save("t1", {"state": "working", "step": "Round two"}, now=200)
        self.assertEqual((again["seen_at"], again["finished_at"], again["started_at"]), (None, None, 200))
        self.assertEqual([e["change"] for e in self.task_events()], ["created", "updated", "seen", "updated"])

    def test_bad_values_are_dropped_and_lines_are_cut(self):
        task = tasks.save("t1", {"title": "x", "kind": "evil", "state": "exploded", "step": "a\nb " * 100,
                                 "links": {"pr": "https://github.com/o/r/pull/12", "junk": "y"}})
        self.assertEqual((task["kind"], task["state"]), ("partner", "working"))
        self.assertEqual(len(task["step"]), tasks.STEP_CHARS)
        self.assertNotIn("\n", task["step"])
        self.assertEqual((task["links"]["pr_number"], "junk" in task["links"]), (12, False))

    def test_seen_on_an_unknown_task_is_none(self):
        self.assertIsNone(tasks.seen("nope"))

    def test_seen_bumps_updated_once(self):
        tasks.save("t1", {"title": "x", "state": "failed"}, now=100)
        self.assertEqual(tasks.seen("t1", now=150)["updated_at"], 150)
        self.assertEqual(tasks.seen("t1", now=200)["seen_at"], 150)
        self.assertEqual([e["change"] for e in self.task_events()], ["created", "seen"])


class TestListing(DbCase):
    def test_what_is_shown_and_in_which_order(self):
        now = 1_000_000.0
        tasks.save("working", {"title": "w"}, now=now - 100)
        tasks.save("waiting", {"title": "q", "state": "waiting"}, now=now - 200)
        tasks.save("failed-old", {"title": "f", "state": "failed"}, now=now - 3 * 86400)
        tasks.save("failed-seen", {"title": "f2", "state": "failed"}, now=now - 3 * 86400)
        tasks.seen("failed-seen", now=now - 3 * 86400)
        tasks.save("done-new", {"title": "d", "state": "done"}, now=now - 60)
        tasks.save("done-old", {"title": "d2", "state": "done"}, now=now - 13 * 3600)
        tasks.save("stale", {"title": "s"}, now=now - 3 * 86400)
        tasks.save("ancient", {"title": "a", "state": "done"}, now=now - 40 * 86400)
        ids = [t["id"] for t in tasks.listing(now=now)]
        self.assertEqual(ids, ["waiting", "failed-old", "working", "done-new"])
        self.assertIsNotNone(db.get_task("ancient"))
        tasks._pruned[0] = 0.0
        tasks.prune(now=now)
        self.assertIsNone(db.get_task("ancient"))
        tasks.save("ancient2", {"title": "a", "state": "done"}, now=now - 40 * 86400)
        tasks.prune(now=now + 60)
        self.assertIsNotNone(db.get_task("ancient2"))
        self.assertEqual([t["id"] for t in tasks.listing(["done"], since=now - 14 * 3600, now=now)], ["done-new", "done-old"])


class TestWakeDecision(unittest.TestCase):
    EXPECTED = {
        ("self_improvement", "done"): False,
        ("self_improvement", "waiting"): True,
        ("self_improvement", "failed"): True,
        ("self_improvement", "working"): False,
        ("self_improvement", "queued"): False,
        ("partner", "done"): True,
        ("partner", "waiting"): True,
        ("partner", "failed"): True,
        ("partner", "working"): False,
        ("partner", "queued"): False,
    }

    def test_every_kind_and_state(self):
        self.assertEqual(set(self.EXPECTED), set(itertools.product(tasks.KINDS, tasks.STATES)))
        for (kind, state), turn in self.EXPECTED.items():
            with self.subTest(kind=kind, state=state):
                self.assertIs(watch.wakes(kind, state), turn)


class TestThreadState(unittest.TestCase):
    def test_table(self):
        rows = [
            ({"pendingRequestCount": 1, "activeRunId": "r"}, {"status": "running"}, ("waiting", None)),
            ({"status": "waiting"}, {"status": "completed"}, ("done", None)),
            ({"status": "waiting", "pendingRequestCount": 0, "pendingBackgroundTasks": [{"taskId": "b1", "awaited": True}]},
             {"status": "completed"}, ("working", None)),
            ({"status": "waiting", "pendingBackgroundTasks": [{"taskId": "b2", "awaited": False}]},
             {"status": "completed"}, ("done", None)),
            ({"status": "waiting", "blockedOnThreadWait": True}, {"status": "completed"}, ("working", None)),
            ({"activeRunId": "r"}, {"status": "running"}, ("working", None)),
            ({}, {"status": "starting"}, ("working", None)),
            ({}, {"status": "queued"}, ("queued", None)),
            ({}, {"status": "failed"}, ("failed", None)),
            ({}, {"status": "interrupted"}, ("done", "Stopped")),
            ({}, {"status": "completed"}, ("done", None)),
        ]
        for thread, run, expected in rows:
            with self.subTest(thread=thread, run=run):
                self.assertEqual(tasks.thread_state(thread, run), expected)


    def test_agrees_with_the_t3_thread_list(self):
        names = {"working": "working", "queued": "working", "waiting": "waiting", "failed": "failed", "done": "finished"}
        for thread, run in [({"status": "waiting", "activeRunId": "r"}, {"status": "running"}),
                            ({"pendingRequestCount": 2}, {"status": "completed"}),
                            ({}, {"status": "completed"}), ({}, {"status": "failed"})]:
            with self.subTest(thread=thread):
                state, _ = tasks.thread_state(thread, run)
                self.assertEqual(names[state], t3threads.state_of(thread, run))


class TestStep(unittest.TestCase):
    def test_newest_words_win_over_tools(self):
        items = [
            {"type": "dynamic_tool", "title": "Read /home/user/x/shot.png"},
            {"type": "assistant_message", "text": "The rebase is done. Now the screenshots."},
            {"type": "reasoning", "title": "Thinking", "text": "hmm"},
            {"type": "checkpoint", "text": "[]"},
        ]
        self.assertEqual(tasks.latest_step(items), "The rebase is done.")

    def test_tools_read_in_plain_words(self):
        self.assertEqual(tasks.latest_step([{"type": "dynamic_tool", "title": "Read /home/user/x/shot.png"}]), "Read shot.png")
        self.assertEqual(tasks.latest_step([{"type": "command_execution", "status": "in_progress", "text": "$ /usr/bin/cargo test"}]), "Running cargo")
        self.assertIsNone(tasks.latest_step([{"type": "reasoning", "text": "x"}]))
        self.assertIsNone(tasks.latest_step(None))


class TestKind(unittest.TestCase):
    def setUp(self):
        tasks._kinds.clear()
        tasks._unreadable.clear()

    def test_an_unreadable_project_is_not_asked_again_at_once(self):
        with mock.patch("hub.t3link.call_tool", side_effect=RuntimeError("down")) as call:
            self.assertIsNone(tasks.resolve_kind("nemurenai", "p9"))
            self.assertIsNone(tasks.resolve_kind("nemurenai", "p9"))
        self.assertEqual(call.call_count, 1)

    def test_diablo_is_self_improvement_and_the_rest_partner(self):
        self.assertEqual(tasks.kind_of_project(DIABLO), "self_improvement")
        self.assertEqual(tasks.kind_of_project(ANIBEAM), "partner")
        self.assertEqual(tasks.kind_of_project({"workspaceRoot": "/home/user/Projects/diablo"}), "self_improvement")
        self.assertEqual(tasks.kind_of_project({}), "partner")


def fake_t3(project, run_status="completed", active=None, completed=None, pending=0, prs=None, items=None, started=None,
            status=None, requests=None, background=None):
    def call(env, name, arguments, timeout=60):
        if name == "t3_project_read":
            return project
        if name == "t3_pending_request":
            if arguments["action"] == "list":
                return {"requestIds": list(requests or {})}
            return requests[arguments["requestId"]]
        if arguments.get("view") == "activity":
            if callable(items):
                return items(arguments)
            return {"thread": {}, "items": items or []}
        return {"thread": {"threadId": arguments["threadId"], "projectId": project["id"], "title": "T3 title",
                           "activeRunId": active, "pendingRequestCount": pending, "itemCount": 40,
                           "pullRequests": prs or [], "link": f"<t3-thread://v2/env-1/{arguments['threadId']}>",
                           "status": status or run_status, "pendingBackgroundTasks": background or [],
                           "updatedAt": iso(time.time())},
                "recentRuns": [{"status": run_status, "startedAt": iso(started or time.time() - 60),
                                "completedAt": iso(completed) if completed else None}]}
    return call


class TestWatchLoop(DbCase):
    def run_loop(self, call, turn_text="Done, sir."):
        delivered = []

        async def deliver(text, watched):
            delivered.append(text)

        with mock.patch("hub.t3link.call_tool", side_effect=call), \
                mock.patch.object(watch.brains, "ask", mock.AsyncMock(return_value=brains.Turn(text=turn_text))) as ask:
            asyncio.run(watch.check_once(deliver))
        return delivered, ask

    def test_finished_self_improvement_updates_the_card_only(self):
        db.add_watch("t1", "nemurenai", "Settings page", "main")
        pr = [{"number": 156, "url": "https://github.com/marcusbandit/diablo/pull/156", "state": "open"}]
        delivered, ask = self.run_loop(fake_t3(DIABLO, completed=time.time() + 5, prs=pr))
        self.assertEqual(delivered, [])
        ask.assert_not_called()
        task = db.get_task("t1")
        self.assertEqual((task["state"], task["kind"], task["machine"], task["title"]), ("done", "self_improvement", "nemurenai", "Settings page"))
        self.assertEqual(task["links"]["pr_number"], 156)
        self.assertEqual(task["links"]["thread"], "t3code://threads/env-1/t1")
        self.assertEqual(db.open_watches(), [])

    def test_finished_partner_work_gets_a_turn(self):
        db.add_watch("t2", "nemurenai", "Timer app", "main")
        delivered, ask = self.run_loop(fake_t3(ANIBEAM, completed=time.time() + 5))
        self.assertEqual(delivered, ["Done, sir."])
        self.assertIn("[t3 event]", ask.call_args.args[1])
        self.assertIn("one short line", ask.call_args.args[1])

    def test_failed_self_improvement_gets_a_turn(self):
        db.add_watch("t3", "nemurenai", "Fix", "main")
        delivered, ask = self.run_loop(fake_t3(DIABLO, run_status="failed", completed=time.time() + 5))
        self.assertEqual(delivered, ["Done, sir."])
        self.assertIn("what failed", ask.call_args.args[1])
        self.assertEqual(db.get_task("t3")["state"], "failed")

    def test_waiting_self_improvement_gets_a_turn(self):
        db.add_watch("t4", "nemurenai", "Fix", "main")
        delivered, ask = self.run_loop(fake_t3(DIABLO, run_status="running", active="r", pending=1))
        self.assertEqual(delivered, ["Done, sir."])
        self.assertIn("what it asks", ask.call_args.args[1])
        self.assertEqual(db.get_task("t4")["state"], "waiting")

    def test_running_thread_shows_its_latest_step_and_no_turn(self):
        db.add_watch("t5", "banditbox", "Tasks", "main")
        items = [{"type": "assistant_message", "text": "Writing the tests now."}]
        delivered, ask = self.run_loop(fake_t3(DIABLO, run_status="running", active="r", items=items))
        ask.assert_not_called()
        task = db.get_task("t5")
        self.assertEqual((task["state"], task["step"]), ("working", "Writing the tests now."))

    def test_the_step_comes_from_the_last_page(self):
        db.add_watch("t10", "banditbox", "Paged", "main")

        def pages(arguments):
            if arguments["afterPosition"] < 30:
                return {"items": [{"type": "assistant_message", "text": "Old words."}], "hasMore": True, "nextPosition": 30}
            return {"items": [{"type": "assistant_message", "text": "Newest words."}], "hasMore": False, "nextPosition": 40}

        self.run_loop(fake_t3(DIABLO, run_status="running", active="r", items=pages))
        self.assertEqual(db.get_task("t10")["step"], "Newest words.")

    def test_a_rewatched_thread_is_not_done_from_its_old_run(self):
        db.add_watch("t11", "nemurenai", "Second round", "main")
        tasks.save("t11", {"title": "Second round", "kind": "partner", "state": "working", "step": "Starting"})
        old = time.time() - 3600
        delivered, ask = self.run_loop(fake_t3(ANIBEAM, completed=old + 600, started=old))
        self.assertEqual(db.get_task("t11")["state"], "working")
        ask.assert_not_called()

    def test_a_waiting_step_does_not_outlive_the_wait(self):
        db.add_watch("t12", "nemurenai", "Asks", "main")
        self.run_loop(fake_t3(DIABLO, run_status="running", active="r", pending=1))
        self.assertEqual(db.get_task("t12")["step"], "Waits on 1 request from you")
        self.run_loop(fake_t3(DIABLO, run_status="running", active="r"))
        self.assertEqual(db.get_task("t12")["step"], "Carrying on")

    def test_stopped_partner_work_is_not_called_done(self):
        db.add_watch("t13", "nemurenai", "Halted", "main")
        delivered, ask = self.run_loop(fake_t3(ANIBEAM, run_status="interrupted", completed=time.time() + 5))
        self.assertIn("stopped before it finished", ask.call_args.args[1])
        self.assertEqual(db.get_task("t13")["step"], "Stopped")

    def test_a_thread_waiting_on_its_own_background_job_stays_watched_and_working(self):
        db.add_watch("t30", "box-a", "Timer", "main")
        tasks.save("t30", {"title": "Timer", "state": "working", "source": "t3:t30"})
        delivered, ask = self.run_loop(fake_t3(ANIBEAM, completed=time.time() + 5, status="waiting",
                                               background=[{"taskId": "b1", "kind": "command", "awaited": True}]))
        self.assertEqual(delivered, [])
        ask.assert_not_called()
        self.assertTrue(db.watch_is_open("t30"))
        self.assertEqual(db.get_task("t30")["state"], "working")

    def test_a_waiting_card_carries_its_question_with_the_pick_marked(self):
        db.add_watch("t31", "box-b", "Venue", "main")
        request = {"requestId": "r1", "questions": [
            {"header": "Venue", "question": "Which room for the demo?", "multiSelect": False, "options": [
                {"label": "Hall A (Recommended)", "description": "Seats forty"},
                {"label": "Hall B", "description": "Has a projector"},
                {"label": "Online", "description": "No room at all"}]},
            {"header": "Time", "question": "When?", "options": [{"label": "Noon"}, {"label": "Evening"}]}]}
        delivered, ask = self.run_loop(fake_t3(ANIBEAM, run_status="running", active="r", pending=1, requests={"r1": request}))
        card = tasks.public(db.get_task("t31"))
        self.assertEqual(card["state"], "waiting")
        self.assertEqual(card["ask"]["question"], "Which room for the demo?")
        self.assertEqual([(o["label"], o["recommended"]) for o in card["ask"]["options"]],
                         [("Hall A", True), ("Hall B", False), ("Online", False)])
        self.assertEqual((card["ask"]["id"], card["ask"]["header"], card["ask"]["more"]), ("r1", "Venue", 1))
        prompt = ask.call_args.args[1]
        self.assertIn("Which room for the demo? Options: Hall A (recommended); Hall B; Online.", prompt)

    def test_an_approval_still_says_what_it_waits_on(self):
        db.add_watch("t32", "box-b", "Deploy", "main")
        self.run_loop(fake_t3(ANIBEAM, run_status="running", active="r", pending=1, requests={}))
        card = tasks.public(db.get_task("t32"))
        self.assertEqual((card["ask"]["kind"], card["ask"]["question"]), ("approval", tasks.APPROVAL_ASK["question"]))

    def test_the_question_leaves_with_the_wait(self):
        db.add_watch("t33", "box-b", "Asks", "main")
        request = {"questions": [{"question": "Go?", "options": [{"label": "Yes"}, {"label": "No"}]}]}
        self.run_loop(fake_t3(ANIBEAM, run_status="running", active="r", pending=1, requests={"r2": request}))
        self.assertIsNotNone(db.get_task("t33")["ask"])
        self.run_loop(fake_t3(ANIBEAM, run_status="running", active="r"))
        card = db.get_task("t33")
        self.assertEqual((card["state"], card["ask"]), ("working", None))
        self.assertNotIn("ask", card["links"])

    def test_a_thread_left_waiting_with_nothing_to_wait_on_is_done(self):
        db.add_watch("t34", "box-a", "Idle", "main")
        delivered, ask = self.run_loop(fake_t3(ANIBEAM, completed=time.time() + 5, status="waiting",
                                               background=[{"taskId": "b3", "awaited": False}]))
        self.assertFalse(db.watch_is_open("t34"))
        self.assertEqual(db.get_task("t34")["state"], "done")
        self.assertEqual(delivered, ["Done, sir."])

    def test_a_rewatch_held_at_waiting_does_not_invent_an_approval(self):
        db.add_watch("t35", "box-a", "Again", "main")
        tasks.save("t35", {"title": "Again", "state": "waiting", "source": "t3:t35"})
        old = time.time() - 3600
        call = fake_t3(ANIBEAM, completed=old + 600, started=old, requests={})
        with mock.patch("hub.t3link.call_tool", side_effect=call) as calls:
            tasks.observe(db.open_watches()[0], call("box-a", "t3_thread_read", {"threadId": "t35"}))
        self.assertEqual(db.get_task("t35")["state"], "waiting")
        self.assertIsNone(db.get_task("t35")["ask"])
        self.assertNotIn("t3_pending_request", [c.args[1] for c in calls.call_args_list])

    def test_an_explicit_kind_beats_the_project(self):
        db.add_watch("t6", "nemurenai", "For himself", "main")
        tasks.save("t6", {"title": "For himself", "kind": "self_improvement"})
        delivered, ask = self.run_loop(fake_t3(ANIBEAM, completed=time.time() + 5))
        self.assertEqual(delivered, [])
        self.assertEqual(db.get_task("t6")["kind"], "self_improvement")


class TestT3ThreadsKeepCards(DbCase):
    def test_the_fast_read_updates_the_card_too(self):
        db.add_watch("t7", "nemurenai", "Quick", "main")
        call = fake_t3(ANIBEAM, run_status="running", active="r")
        with mock.patch.object(t3threads.t3link, "call_tool", side_effect=call):
            row = asyncio.run(t3threads.one(db.open_watches()[0]))
        self.assertEqual(row["state"], "working")
        self.assertEqual(db.get_task("t7")["state"], "working")


    def test_a_closed_watch_never_puts_its_card_back_to_working(self):
        db.add_watch("t20", "box-a", "Open AniBeam", "main")
        tasks.save("t20", {"title": "Open AniBeam", "state": "done", "source": "t3:t20"})
        db.set_watch("t20", "done")
        call = fake_t3(ANIBEAM, run_status="running", active="r")
        with mock.patch.object(t3threads.t3link, "call_tool", side_effect=call):
            row = asyncio.run(t3threads.one(db.recent_watches(0)[0]))
        self.assertEqual(row["state"], "working")
        self.assertEqual(db.get_task("t20")["state"], "done")


class TestSweep(DbCase):
    def test_a_thread_card_without_an_open_watch_is_finished(self):
        db.add_watch("t21", "box-a", "Open AniBeam", "main")
        tasks.save("t21", {"title": "Open AniBeam", "state": "working", "source": "t3:t21"}, now=time.time() - 600)
        db.set_watch("t21", "done")
        finished = tasks.sweep()
        self.assertEqual([t["id"] for t in finished], ["t21"])
        card = db.get_task("t21")
        self.assertEqual((card["state"], card["step"]), ("done", tasks.UNWATCHED_STEP))

    def test_watched_and_fresh_cards_stay(self):
        db.add_watch("t22", "box-b", "Watched", "main")
        tasks.save("t22", {"title": "Watched", "state": "working", "source": "t3:t22"}, now=time.time() - 600)
        tasks.save("task-a", {"title": "Drafting", "state": "working"}, now=time.time() - 60)
        self.assertEqual(tasks.sweep(), [])
        self.assertEqual({db.get_task(i)["state"] for i in ("t22", "task-a")}, {"working"})

    def test_a_tool_card_left_alone_for_hours_is_finished(self):
        tasks.save("task-b", {"title": "Drafting", "state": "queued"}, now=time.time() - tasks.UNTRACKED_S - 1)
        tasks.sweep()
        card = db.get_task("task-b")
        self.assertEqual((card["state"], card["step"]), ("done", tasks.UNTRACKED_STEP))

    def test_the_watch_loop_sweeps_even_with_nothing_watched(self):
        tasks.save("t24", {"title": "Old", "state": "working", "source": "t3:t24"}, now=time.time() - 600)
        with mock.patch.object(watch.brains, "ask", mock.AsyncMock()) as ask:
            asyncio.run(watch.check_once(mock.AsyncMock()))
        ask.assert_not_called()
        self.assertEqual(db.get_task("t24")["state"], "done")

    def test_a_rewatch_on_an_old_run_stays_open_and_working(self):
        db.add_watch("t25", "box-b", "Again", "main")
        old_run = fake_t3(DIABLO, completed=time.time() - 30, started=time.time() - 3600)
        with mock.patch("hub.t3link.call_tool", side_effect=old_run), \
                mock.patch.object(watch.brains, "ask", mock.AsyncMock()) as ask:
            tasks.save("t25", {"title": "Again", "state": "working", "source": "t3:t25"})
            asyncio.run(watch.check_once(mock.AsyncMock()))
        ask.assert_not_called()
        self.assertTrue(db.watch_is_open("t25"))
        self.assertEqual(db.get_task("t25")["state"], "working")

    def test_a_waiting_thread_card_without_an_open_watch_is_finished_now(self):
        now = time.time()
        db.add_watch("t26", "box-a", "Asks", "main")
        tasks.save("t26", {"title": "Asks", "state": "waiting", "source": "t3:t26"}, now=now - 20 * 3600)
        db.set_watch("t26", "done")
        finished = tasks.sweep(now=now)
        self.assertEqual([(t["id"], t["expired"]) for t in finished], [("t26", False)])
        card = db.get_task("t26")
        self.assertEqual((card["state"], card["step"], card["finished_at"]), ("done", tasks.UNWATCHED_STEP, now))
        self.assertIn("t26", [t["id"] for t in tasks.listing(now=now)])
        self.assertEqual(tasks.listing(["waiting"], now=now), [])

    def test_a_waiting_card_with_its_question_stays(self):
        now = time.time()
        db.add_watch("t27", "box-a", "Asks", "main")
        ask = {"question": "Ship it tonight?", "options": ["Yes (Recommended)", "Tomorrow"]}
        tasks.save("t27", {"title": "Asks", "state": "waiting", "source": "t3:t27", "ask": ask}, now=now - 5 * 3600)
        tasks.save("task-c", {"title": "Pick a venue", "state": "waiting", "ask": ask}, now=now - 5 * 3600)
        self.assertEqual(tasks.sweep(now=now), [])
        self.assertEqual({db.get_task(i)["state"] for i in ("t27", "task-c")}, {"waiting"})

    def test_a_waiting_card_with_no_question_expires_and_is_reported(self):
        now = time.time()
        tasks.save("task-e", {"title": "Bare", "state": "waiting"}, now=now - tasks.WAITING_BARE_S - 1)
        tasks.save("task-f", {"title": "Fresh", "state": "waiting"}, now=now - 60)
        finished = tasks.sweep(now=now)
        self.assertEqual([t["id"] for t in finished], ["task-e"])
        card = db.get_task("task-e")
        self.assertEqual((card["state"], card["step"]), ("done", tasks.EXPIRED_STEP))
        self.assertIn("with no question on it", card["result"])
        self.assertEqual(db.get_task("task-f")["state"], "waiting")
        with self.assertLogs("hub.watch", "WARNING") as logged, \
                mock.patch.object(tasks, "sweep", return_value=finished), \
                mock.patch.object(watch.brains, "ask", mock.AsyncMock()):
            asyncio.run(watch.check_once(mock.AsyncMock()))
        self.assertIn("task-e", logged.output[0])

    def test_an_asked_tool_card_expires_once_unanswered_for_long(self):
        now = time.time()
        ask = {"question": "Which hall?", "options": ["A", "B"]}
        tasks.save("task-h", {"title": "Hall", "state": "waiting", "ask": ask}, now=now - tasks.ASKED_KEPT_S - 1)
        finished = tasks.sweep(now=now)
        self.assertEqual([(t["id"], t["expired"]) for t in finished], [("task-h", True)])
        self.assertEqual(db.get_task("task-h")["step"], tasks.UNANSWERED_STEP)

    def test_a_closed_watch_is_not_logged_as_expired(self):
        db.add_watch("t28", "box-a", "Asks", "main")
        tasks.save("t28", {"title": "Asks", "state": "waiting", "source": "t3:t28"})
        db.set_watch("t28", "done")
        self.assertEqual([t["expired"] for t in tasks.sweep()], [False])

    def test_the_bare_wait_follows_the_setting(self):
        db.set_setting("tasks.waiting_bare_s", "120")
        now = time.time()
        tasks.save("task-g", {"title": "Bare", "state": "waiting"}, now=now - 121)
        self.assertEqual([t["id"] for t in tasks.sweep(now=now)], ["task-g"])

    def test_an_old_card_keeps_its_old_finish_time(self):
        long_ago = time.time() - 5 * 86400
        tasks.save("task-d", {"title": "Ancient", "state": "working"}, now=long_ago)
        tasks.sweep()
        self.assertEqual(db.get_task("task-d")["finished_at"], long_ago)
        self.assertNotIn("task-d", [t["id"] for t in tasks.listing()])


class TestMcpTools(DbCase):
    def setUp(self):
        super().setUp()
        hub_down = mock.patch.object(mcp_server.httpx, "post", side_effect=mcp_server.httpx.ConnectError("down"))
        hub_down.start()
        self.addCleanup(hub_down.stop)

    def test_watch_takes_the_kind_from_the_project(self):
        with mock.patch("hub.t3link.call_tool", side_effect=fake_t3(DIABLO, run_status="running", active="r")):
            reply = mcp_server.call("watch_t3_thread", {"environment": "nemurenai", "thread_id": "t8", "title": "Self"})
        self.assertIn("task card (self_improvement)", reply)
        task = db.get_task("t8")
        self.assertEqual((task["kind"], task["kind_source"], task["state"], task["source"]), ("self_improvement", "project", "working", "t3:t8"))

    def test_watch_kind_argument_wins_and_an_unreadable_thread_guesses_partner(self):
        with mock.patch("hub.t3link.call_tool", side_effect=RuntimeError("down")):
            mcp_server.call("watch_t3_thread", {"environment": "nemurenai", "thread_id": "a", "kind": "self_improvement"})
            mcp_server.call("watch_t3_thread", {"environment": "nemurenai", "thread_id": "b"})
        self.assertEqual((db.get_task("a")["kind"], db.get_task("a")["kind_source"]), ("self_improvement", "arg"))
        self.assertEqual((db.get_task("b")["kind"], db.get_task("b")["kind_source"]), ("partner", "guess"))

    def test_watch_goes_through_the_hub_when_it_answers(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"task": {"id": "t9", "kind": "partner", "state": "working"}}
        with mock.patch.object(mcp_server.httpx, "post", return_value=response) as post, \
                mock.patch("hub.t3link.call_tool", side_effect=RuntimeError("down")):
            mcp_server.call("watch_t3_thread", {"environment": "nemurenai", "thread_id": "t9", "kind": "partner"})
        self.assertTrue(post.call_args.args[0].endswith("/v1/tasks"))
        self.assertEqual(post.call_args.kwargs["json"]["id"], "t9")

    def test_a_refused_write_is_reported_and_not_written(self):
        response = mock.Mock(status_code=403, text="forbidden")
        with mock.patch.object(mcp_server.httpx, "post", return_value=response), \
                mock.patch("hub.t3link.call_tool", side_effect=RuntimeError("down")):
            reply = mcp_server.call("watch_t3_thread", {"environment": "nemurenai", "thread_id": "t14", "kind": "partner"})
            update = mcp_server.handle({"method": "tools/call", "params": {"name": "update_task", "arguments": {"title": "x"}}})
        self.assertIn("refused the task card (403)", reply)
        self.assertIsNone(db.get_task("t14"))
        self.assertTrue(update["isError"])
        self.assertEqual(db.list_tasks(), [])

    def test_update_task_creates_then_updates(self):
        reply = mcp_server.call("update_task", {"title": "Back up the vault", "machine": "nemurenai"})
        task_id = reply.split()[1]
        self.assertTrue(task_id.startswith("task-"))
        mcp_server.call("update_task", {"id": task_id, "state": "done", "result": "Backed up, 2 GB",
                                        "pr": "https://github.com/marcusbandit/x/pull/4"})
        task = tasks.public(db.get_task(task_id))
        self.assertEqual((task["state"], task["result"], task["links"]["pr_number"], task["source"], task["kind"]),
                         ("done", "Backed up, 2 GB", 4, "tool", "partner"))

    def test_update_task_refuses_nonsense(self):
        self.assertIn("needs a title", mcp_server.call("update_task", {}))
        self.assertIn("no task", mcp_server.call("update_task", {"id": "task-nope", "state": "done"}))
        self.assertIn("kind is one of", mcp_server.call("update_task", {"title": "x", "kind": "evil"}))

    def test_update_task_is_listed_and_in_agent_tools(self):
        self.assertIn("update_task", [t["name"] for t in mcp_server.tools()])
        self.assertIn("update_task", [t["function"]["name"] for t in agent.TOOLS])
        kind = [t for t in mcp_server.tools() if t["name"] == "watch_t3_thread"][0]["inputSchema"]["properties"]["kind"]
        self.assertEqual(kind["enum"], ["self_improvement", "partner"])

    def test_list_tasks_reads_the_cards_waiting_first(self):
        now = time.time()
        tasks.save("w", {"title": "Ship the card", "state": "working", "step": "Running tests", "machine": "desk", "started_at": now - 125}, now)
        tasks.save("q", {"title": "Book a table", "state": "waiting", "started_at": now - 30}, now)
        reply = agent._call_tool("list_tasks", {})
        lines = reply.splitlines()
        self.assertEqual(lines[0], "1 waiting on Marcus")
        self.assertTrue(lines[1].startswith("waiting: Book a table, waits on Marcus, for "), lines[1])
        self.assertIn("working: Ship the card, Running tests, for 2 min, on desk [w]", lines[2])
        self.assertEqual(agent._call_tool("list_tasks", {"state": "failed"}), "no failed tasks")
        self.assertIn("state is one of", agent._call_tool("list_tasks", {"state": "nope"}))
        self.assertIn("list_tasks", [t["name"] for t in mcp_server.tools()])

    def test_agent_writes_straight_to_the_database(self):
        reply = agent._call_tool("update_task", {"title": "Api brain task"})
        self.assertIn("saved", reply)
        mcp_server.httpx.post.assert_not_called()

    def test_update_task_is_allowed_while_locked(self):
        self.assertIn("update_task", lockguard.SAFE_HOUSE)


class TestApi(DbCase):
    def setUp(self):
        super().setUp()
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def test_list_filter_and_seen(self):
        tasks.save("mcp:abc", {"title": "Running", "state": "working"})
        tasks.save("t-done", {"title": "Finished", "state": "done"})
        body = self.client.get("/v1/tasks", headers=HEADERS).json()
        self.assertEqual([t["id"] for t in body["tasks"]], ["mcp:abc", "t-done"])
        self.assertIn("now", body)
        only = self.client.get("/v1/tasks?state=done", headers=HEADERS).json()["tasks"]
        self.assertEqual([t["id"] for t in only], ["t-done"])
        self.assertEqual(self.client.get("/v1/tasks?state=bogus", headers=HEADERS).status_code, 400)
        seen = self.client.post("/v1/tasks/mcp:abc/seen", headers=HEADERS)
        self.assertEqual(seen.status_code, 200)
        self.assertIsNotNone(seen.json()["task"]["seen_at"])
        self.assertEqual(self.task_events()[-1]["change"], "seen")
        self.assertEqual(self.client.post("/v1/tasks/nope/seen", headers=HEADERS).status_code, 404)

    def test_the_hub_api_keeps_the_ask(self):
        body = self.client.post("/v1/tasks", headers=HEADERS, json={
            "id": "t41", "title": "Venue", "state": "waiting",
            "ask": {"question": "Which hall?", "options": ["Hall A", "Hall B"]}}).json()
        self.assertEqual(body["task"]["ask"]["question"], "Which hall?")
        listed = self.client.get("/v1/tasks", headers=HEADERS).json()["tasks"]
        self.assertEqual(listed[0]["ask"]["options"][1]["label"], "Hall B")

    def test_a_device_reads_but_only_the_hub_token_writes(self):
        device = devices.admit("phone")[0]
        mine = {"X-Diablo-Token": device}
        self.assertEqual(self.client.get("/v1/tasks", headers=mine).status_code, 200)
        self.assertEqual(self.client.post("/v1/tasks", headers=mine, json={"title": "x"}).status_code, 403)
        made = self.client.post("/v1/tasks", headers=HEADERS, json={"title": "Hub write", "kind": "partner"})
        self.assertEqual(made.status_code, 200)
        self.assertEqual(made.json()["task"]["title"], "Hub write")
        self.assertEqual(self.client.post("/v1/tasks", headers=HEADERS, json={"title": "x", "state": "bad"}).status_code, 400)
        self.assertEqual(self.client.get("/v1/tasks").status_code, 403)


class TestAsk(DbCase):
    def test_the_recommended_pick_comes_from_the_label_or_the_name(self):
        marked = tasks.clean_ask({"question": "Q?", "options": ["A", "B (Recommended)"]})
        self.assertEqual([(o["label"], o["recommended"]) for o in marked["options"]], [("A", False), ("B", True)])
        named = tasks.clean_ask({"question": "Q?", "options": ["A", "B"], "recommended": "a"})
        self.assertEqual([o["recommended"] for o in named["options"]], [True, False])
        numbered = tasks.clean_ask({"question": "Q?", "options": ["A", "B"], "recommended": 1})
        self.assertEqual([o["recommended"] for o in numbered["options"]], [False, True])
        suffixed = tasks.clean_ask({"question": "Q?", "options": ["Hall A", "Hall B"], "recommended": "Hall B (Recommended)"})
        self.assertEqual([o["recommended"] for o in suffixed["options"]], [False, True])
        long_name = "A very long option label that runs well past the limit of the card"
        trimmed = tasks.clean_ask({"question": "Q?", "options": ["Short", long_name], "recommended": long_name})
        self.assertEqual([o["recommended"] for o in trimmed["options"]], [False, True])
        plain = tasks.clean_ask({"question": "Q?", "options": ["A", "B"]})
        self.assertEqual([o["recommended"] for o in plain["options"]], [False, False])

    def test_bad_asks_are_dropped_and_long_ones_trimmed(self):
        self.assertIsNone(tasks.clean_ask(None))
        self.assertIsNone(tasks.clean_ask({"options": ["A"]}))
        ask = tasks.clean_ask({"question": "Q" * 900, "options": [f"Option {i}" for i in range(9)], "more": "x"})
        self.assertEqual((len(ask["question"]), len(ask["options"]), ask["more"]), (tasks.QUESTION_CHARS, tasks.OPTIONS_MAX, 0))

    def test_the_ask_round_trips_and_shows_only_while_waiting(self):
        ask = {"question": "Ship it?", "options": ["Yes", "No"]}
        tasks.save("t40", {"title": "Ship", "state": "waiting", "ask": ask})
        stored = db.get_task("t40")
        self.assertEqual(stored["ask"]["question"], "Ship it?")
        self.assertNotIn("ask", tasks.public(stored)["links"])
        tasks.save("t40", {"state": "done"})
        self.assertIsNone(tasks.public(db.get_task("t40"))["ask"])
        self.assertIsNone(db.get_task("t40")["ask"])

    def test_list_tasks_reads_the_question_out(self):
        tasks.save("q2", {"title": "Book a table", "state": "waiting",
                          "ask": {"question": "Where?", "options": ["Here (Recommended)", "There"]}})
        self.assertIn("waiting: Book a table, asks: Where? Options: Here (recommended); There.", agent._call_tool("list_tasks", {}))


class TestUpdateTaskAsks(DbCase):
    def setUp(self):
        super().setUp()
        hub_down = mock.patch.object(mcp_server.httpx, "post", side_effect=mcp_server.httpx.ConnectError("down"))
        hub_down.start()
        self.addCleanup(hub_down.stop)

    def test_a_waiting_card_needs_its_question(self):
        self.assertIn("needs the question", mcp_server.call("update_task", {"title": "Venue", "state": "waiting"}))
        self.assertEqual(db.list_tasks(), [])
        self.assertIn("two to four options", mcp_server.call("update_task", {"title": "Venue", "state": "waiting",
                                                                             "question": "Which?", "options": ["A"]}))

    def test_a_question_makes_the_card_waiting_and_bad_options_are_refused(self):
        reply = mcp_server.call("update_task", {"title": "Venue", "question": "Which hall?", "options": ["Hall A", "Hall B"]})
        self.assertIn("saved: waiting", reply)
        self.assertIsNotNone(db.get_task(reply.split()[1])["ask"])
        self.assertIn("list of two to four", mcp_server.call("update_task", {"title": "x", "question": "Q?", "options": "Yes, No"}))
        self.assertIn("list of two to four", mcp_server.call("update_task", {"title": "x", "question": "Q?", "options": list("abcde")}))
        self.assertIn("goes on a waiting card", mcp_server.call("update_task", {"title": "x", "state": "working",
                                                                               "question": "Q?", "options": ["A", "B"]}))

    def test_a_waiting_card_with_its_question_is_saved(self):
        reply = mcp_server.call("update_task", {"title": "Venue", "state": "waiting", "question": "Which hall?",
                                                "options": ["Hall A", "Hall B"], "recommended": "Hall B"})
        card = tasks.public(db.get_task(reply.split()[1]))
        self.assertEqual([(o["label"], o["recommended"]) for o in card["ask"]["options"]], [("Hall A", False), ("Hall B", True)])
        self.assertIn("saved: waiting", mcp_server.call("update_task", {"id": card["id"], "step": "Asked by voice"}))


class TestCharter(unittest.TestCase):
    def test_work_shows_through_its_card(self):
        text = charter.CHARTER
        self.assertIn("# Task cards", text)
        self.assertIn("The card is the report", text)
        self.assertIn("one short line: done, working, or needs your decision", text)
        self.assertIn("only when he asks for them, or for partner work", text)
        self.assertIn("Never a wall of issue numbers, review counts, commit hashes or hashtags", text)
        self.assertIn("Finished self-improvement work needs no report at all unless something failed or needs him", text)
        self.assertIn("update_task", text)
        self.assertIn("kind self_improvement", text)
        self.assertNotIn("report back", text)
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)

    def test_event_prompt_asks_for_one_line(self):
        w = {"title": "x", "thread_id": "t", "env": "nemurenai"}
        for state in ("waiting", "failed", "done"):
            text = watch.event_text(w, "something", state)
            self.assertTrue(text.startswith("[t3 event]"))
            self.assertIn("task card already shows", text)
            self.assertIn("one short line", text)


if __name__ == "__main__":
    unittest.main()
