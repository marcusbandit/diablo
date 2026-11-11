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
                                     "seen_at", "updated_at", "links", "result", "source"})
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
            ({"status": "waiting"}, {"status": "completed"}, ("waiting", None)),
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


def fake_t3(project, run_status="completed", active=None, completed=None, pending=0, prs=None, items=None, started=None):
    def call(env, name, arguments, timeout=60):
        if name == "t3_project_read":
            return project
        if arguments.get("view") == "activity":
            if callable(items):
                return items(arguments)
            return {"thread": {}, "items": items or []}
        return {"thread": {"threadId": arguments["threadId"], "projectId": project["id"], "title": "T3 title",
                           "activeRunId": active, "pendingRequestCount": pending, "itemCount": 40,
                           "pullRequests": prs or [], "link": f"<t3-thread://v2/env-1/{arguments['threadId']}>"},
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
