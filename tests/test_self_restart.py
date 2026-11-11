import asyncio
import json
import os
import signal
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, charter, db, devices, mailbox, mcp_server, selfrestart, server, settings, standby, watch

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


class RestartCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "diablo.db")
        db.init(self.db_path)
        patches = {
            "env": mock.patch.dict(os.environ, {"INVOCATION_ID": "unit"}),
            "query": mock.patch.object(mailbox, "query", return_value=[]),
            "state": mock.patch.object(brains, "STATE", Path(self.tmp.name) / "state"),
            "down": mock.patch.object(selfrestart, "desktop_down", return_value=False),
        }
        started = {}
        for name, patch in patches.items():
            started[name] = patch.start()
            self.addCleanup(patch.stop)
        self.query, self.desktop_down = started["query"], started["down"]
        brains.inflight.clear()
        selfrestart._scheduled.update(conv=None, task=None)
        selfrestart.report_ears("asleep")

    def reasons(self, conv="main"):
        return " | ".join(selfrestart.assess(conv)["reasons"])


class TestSafety(RestartCase):
    def test_all_clear(self):
        verdict = selfrestart.assess("main")
        self.assertTrue(verdict["safe"])
        self.assertEqual(verdict["reasons"], [])

    def test_own_turn_does_not_block_but_another_conversation_does(self):
        brains.inflight["main"] = 1
        self.assertTrue(selfrestart.assess("main")["safe"])
        brains.inflight["web-7"] = 1
        self.assertIn("web-7", self.reasons())

    def test_second_message_waiting_in_the_same_conversation_blocks(self):
        brains.inflight["main"] = 2
        self.assertIn("waiting", self.reasons())

    def test_turn_started_by_nobody_blocks_when_no_conversation_is_excluded(self):
        brains.inflight["main"] = 1
        self.assertFalse(selfrestart.assess(None)["safe"])

    def test_ears_busy_states_block(self):
        for state in ("waking", "listening", "thinking", "speaking", "training", "something-new"):
            selfrestart.report_ears(state)
            self.assertIn(state, self.reasons(), state)

    def test_ears_quiet_states_are_fine(self):
        for state in ("asleep", "untrained", "off"):
            selfrestart.report_ears(state)
            self.assertTrue(selfrestart.assess("main")["safe"], state)

    def test_unknown_ears_state_is_unsafe(self):
        selfrestart._ears.update(state=None, at=0.0)
        self.assertIn("have not reported", self.reasons())

    def test_silent_ears_are_unknown(self):
        selfrestart.report_ears("asleep")
        selfrestart._ears["at"] = time.time() - 300
        self.assertIn("cannot tell", self.reasons())

    def test_silent_ears_while_the_desktop_is_down_are_not_in_the_way(self):
        selfrestart.report_ears("asleep")
        selfrestart._ears["at"] = time.time() - 300
        self.desktop_down.return_value = True
        self.assertTrue(selfrestart.assess("main")["safe"])
        selfrestart._ears.update(state=None, at=0.0)
        self.assertTrue(selfrestart.assess("main")["safe"])

    def test_loud_ears_block_even_when_the_desktop_looks_down(self):
        selfrestart.report_ears("speaking")
        self.desktop_down.return_value = True
        self.assertIn("speaking", self.reasons())

    def test_ears_that_said_off_stay_known(self):
        selfrestart.report_ears("off")
        selfrestart._ears["at"] = time.time() - 3600
        self.assertTrue(selfrestart.assess("main")["safe"])

    def test_open_approval_blocks(self):
        self.query.return_value = [{"id": 4, "status": "pending"}]
        self.assertIn("approval", self.reasons())
        self.assertIn("#4", self.reasons())

    def test_unreadable_approvals_are_unsafe(self):
        self.query.side_effect = RuntimeError("disk gone")
        self.assertIn("cannot tell", self.reasons())

    def test_no_approvals_table_means_none_open(self):
        self.query.side_effect = sqlite3.OperationalError("no such table: approvals")
        self.assertTrue(selfrestart.assess("main")["safe"])

    def test_thread_waiting_on_a_question_blocks_and_running_thread_only_listed(self):
        db.add_watch("t-run", "nemurenai", "Build", "main")
        db.add_watch("t-ask", "banditbox", "Review", "main")
        db.set_watch("t-ask", "asking")
        verdict = selfrestart.assess("main")
        self.assertFalse(verdict["safe"])
        self.assertEqual(len(verdict["reasons"]), 1)
        self.assertIn("Review", verdict["reasons"][0])
        status = {t["thread_id"]: t["status"] for t in verdict["threads"]}
        self.assertEqual(status["t-run"], "running")
        self.assertIn("approval or question", status["t-ask"])

    def test_running_thread_alone_does_not_block(self):
        db.add_watch("t-run", "nemurenai", "Build", "main")
        verdict = selfrestart.assess("main")
        self.assertTrue(verdict["safe"])
        self.assertEqual([t["thread_id"] for t in verdict["threads"]], ["t-run"])

    def test_switched_off_blocks(self):
        settings.set_setting("restart.allowed", "off")
        self.assertIn("switched off", self.reasons())

    def test_allowed_by_default(self):
        self.assertTrue(selfrestart.allowed())

    def test_outside_systemd_blocks(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIn("systemd", self.reasons())


class TestRateLimit(RestartCase):
    def test_a_recent_restart_blocks(self):
        settings.set_setting(selfrestart.LAST_KEY, str(time.time() - 60))
        self.assertIn("restarted 60 s ago", self.reasons())

    def test_an_old_restart_does_not(self):
        settings.set_setting(selfrestart.LAST_KEY, str(time.time() - 3600))
        self.assertTrue(selfrestart.assess("main")["safe"])

    def test_interval_is_a_setting(self):
        settings.set_setting(selfrestart.LAST_KEY, str(time.time() - 60))
        settings.set_setting(selfrestart.INTERVAL_KEY, "30")
        self.assertTrue(selfrestart.assess("main")["safe"])


class TestRequest(RestartCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_dry_run_schedules_nothing(self):
        async def go():
            return selfrestart.request("main", "check", dry_run=True)

        verdict = self.run_async(go())
        self.assertTrue(verdict["safe"])
        self.assertFalse(verdict["restarting"])
        self.assertIsNone(selfrestart._scheduled["conv"])

    def test_refusal_names_the_reasons_and_schedules_nothing(self):
        selfrestart.report_ears("speaking")

        async def go():
            return selfrestart.request("main", "new charter")

        verdict = self.run_async(go())
        self.assertFalse(verdict["restarting"])
        self.assertIn("speaking", verdict["reasons"][0])

    def test_a_reason_is_needed(self):
        async def go():
            return selfrestart.request("main", "  ")

        verdict = self.run_async(go())
        self.assertFalse(verdict["restarting"])
        self.assertIn("why", " ".join(verdict["reasons"]))

    def test_refusals_and_checks_are_logged(self):
        selfrestart.report_ears("listening")

        async def go():
            selfrestart.request("main", "try", dry_run=False)
            selfrestart.request("main", "peek", dry_run=True)

        with self.assertLogs("hub.selfrestart", "INFO") as logs:
            self.run_async(go())
        text = "\n".join(logs.output)
        self.assertIn("restart refused", text)
        self.assertIn("restart checked", text)
        rows = [json.loads(line) for line in (brains.STATE / "restarts.jsonl").read_text().splitlines()]
        self.assertEqual([r["outcome"] for r in rows], ["refused", "checked"])

    def test_safe_request_ends_the_process_once_and_sets_the_limit(self):
        killed = []

        async def go():
            with mock.patch.object(selfrestart, "SETTLE_S", 0), mock.patch.object(selfrestart.os, "kill", lambda pid, sig: killed.append((pid, sig))):
                first = selfrestart.request("main", "new charter")
                self.assertTrue(first["restarting"])
                second = selfrestart.request("main", "again")
                self.assertFalse(second["restarting"])
                self.assertIn("already scheduled", " ".join(second["reasons"]))
                await asyncio.sleep(0.1)
                third = selfrestart.request("main", "and again")
                return first, third

        first, third = self.run_async(go())
        self.assertEqual(killed, [(os.getpid(), signal.SIGTERM)])
        self.assertFalse(third["restarting"])
        self.assertIn("restarted", " ".join(third["reasons"]))
        note = json.loads(settings.get(selfrestart.ANNOUNCE_KEY))
        self.assertEqual(note["reason"], "new charter")

    def test_waits_for_the_reply_to_finish(self):
        killed = []

        async def go():
            brains.inflight["main"] = 1
            with mock.patch.object(selfrestart, "SETTLE_S", 0), mock.patch.object(selfrestart, "POLL_S", 0.02), \
                    mock.patch.object(selfrestart.os, "kill", lambda pid, sig: killed.append(sig)):
                self.assertTrue(selfrestart.request("main", "wait for me")["restarting"])
                await asyncio.sleep(0.15)
                self.assertEqual(killed, [])
                del brains.inflight["main"]
                await asyncio.sleep(0.15)

        self.run_async(go())
        self.assertEqual(killed, [signal.SIGTERM])

    def test_cancels_when_it_stays_unsafe_and_leaves_a_note(self):
        killed = []

        async def go():
            with mock.patch.object(selfrestart, "SETTLE_S", 0), mock.patch.object(selfrestart, "POLL_S", 0.02), \
                    mock.patch.object(selfrestart, "WAIT_S", 0.1), \
                    mock.patch.object(selfrestart.os, "kill", lambda pid, sig: killed.append(sig)):
                self.assertTrue(selfrestart.request("main", "wait for me")["restarting"])
                selfrestart.report_ears("speaking")
                await asyncio.sleep(0.4)

        self.run_async(go())
        self.assertEqual(killed, [])
        self.assertIsNone(selfrestart._scheduled["conv"])
        self.assertIn("cancelled", brains.take_handover("main"))
        self.assertEqual(selfrestart.last_restart(), 0.0)


class TestPerform(RestartCase):
    def test_needs_a_quiet_stretch_after_the_reply_so_it_can_be_delivered(self):
        killed = []

        async def go():
            brains.inflight["main"] = 1
            with mock.patch.object(selfrestart, "SETTLE_S", 0.3), mock.patch.object(selfrestart, "POLL_S", 0.02), \
                    mock.patch.object(selfrestart.os, "kill", lambda pid, sig: killed.append(time.monotonic())):
                selfrestart.request("main", "after the reply")
                await asyncio.sleep(0.1)
                del brains.inflight["main"]
                cleared = time.monotonic()
                await asyncio.sleep(0.6)
                return cleared

        cleared = asyncio.run(go())
        self.assertEqual(len(killed), 1)
        self.assertGreaterEqual(killed[0] - cleared, 0.25)

    def test_a_failure_is_recorded_and_the_brain_is_told(self):
        async def go():
            with mock.patch.object(selfrestart, "SETTLE_S", 0), mock.patch.object(selfrestart, "assess", side_effect=[
                    {"safe": True, "reasons": [], "threads": []}, RuntimeError("database is locked")]):
                selfrestart.request("main", "doomed")
                await asyncio.sleep(0.1)

        with self.assertLogs("hub.selfrestart", "ERROR"):
            asyncio.run(go())
        self.assertIn("failed", brains.take_handover("main"))
        self.assertIsNone(selfrestart._scheduled["conv"])
        rows = [json.loads(line) for line in (brains.STATE / "restarts.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["outcome"], "failed")


class TestAnnounce(RestartCase):
    def test_tells_marcus_once_after_a_restart(self):
        settings.set_setting(selfrestart.ANNOUNCE_KEY, json.dumps({"conv": "main", "reason": "new charter", "at": time.time()}))
        sent, asked = [], []

        async def deliver(text, watched):
            sent.append((text, watched))

        async def ask(conv, text, **kwargs):
            asked.append(text)
            return brains.Turn(text="I restarted for the new charter.")

        async def go():
            with mock.patch.object(selfrestart, "ANNOUNCE_DELAY_S", 0), mock.patch.object(brains, "ask", ask):
                await selfrestart.announce(deliver)
                await selfrestart.announce(deliver)

        asyncio.run(go())
        self.assertEqual(len(sent), 1)
        self.assertIn("new charter", asked[0])
        self.assertIn("Do not restart again", asked[0])

    def test_an_old_note_is_dropped(self):
        settings.set_setting(selfrestart.ANNOUNCE_KEY, json.dumps({"conv": "main", "reason": "x", "at": time.time() - 7200}))
        sent = []

        async def deliver(text, watched):
            sent.append(text)

        asyncio.run(selfrestart.announce(deliver))
        self.assertEqual(sent, [])
        self.assertEqual(settings.get(selfrestart.ANNOUNCE_KEY), "")


class TestInflight(unittest.TestCase):
    def test_ask_counts_a_turn_while_it_runs(self):
        seen = []

        async def slow(conv, text, *args, **kwargs):
            seen.append(dict(brains.inflight))
            return brains.Turn(text="ok")

        async def go():
            with mock.patch.object(brains, "_ask", slow):
                await brains.ask("main", "hi", origin="test")
                await asyncio.gather(brains.ask("main", "a"), brains.ask("main", "b"))

        brains.inflight.clear()
        asyncio.run(go())
        self.assertEqual(seen[0], {"main": 1})
        self.assertEqual(dict(brains.inflight), {})

    def test_a_failing_turn_still_clears_the_count(self):
        async def boom(conv, text, *args, **kwargs):
            raise RuntimeError("no")

        async def go():
            with mock.patch.object(brains, "_ask", boom):
                with self.assertRaises(RuntimeError):
                    await brains.ask("main", "hi")

        brains.inflight.clear()
        asyncio.run(go())
        self.assertEqual(dict(brains.inflight), {})


class TestWatchesSurviveARestart(unittest.TestCase):
    def test_watches_and_their_finish_events_outlive_the_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "diablo.db")
            db.init(path)
            db.add_watch("thread-1", "nemurenai", "Build the thing", "main")
            db.add_watch("thread-2", "banditbox", "Review", "web-3")
            db.set_watch("thread-2", "asking")
            db.init(path)
            kept = {w["thread_id"]: w["state"] for w in db.open_watches()}
            self.assertEqual(kept, {"thread-1": "open", "thread-2": "asking"})
            finished = {
                "thread": {"activeRunId": None},
                "recentRuns": [{"status": "completed", "completedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60))}],
            }
            delivered = []

            async def deliver(text, watched):
                delivered.append(watched["thread_id"])

            async def ask(conv, text, **kwargs):
                return brains.Turn(text="It finished.")

            async def go():
                with mock.patch.object(watch.t3link, "call_tool", return_value=finished), mock.patch.object(brains, "ask", ask):
                    await watch.check_once(deliver)

            asyncio.run(go())
            self.assertIn("thread-1", delivered)


class TestEndpoints(RestartCase):
    def setUp(self):
        super().setUp()
        for target in (server, devices, standby):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def test_needs_a_token(self):
        self.assertEqual(self.client.post("/v1/restart", json={"reason": "x"}).status_code, 403)
        self.assertEqual(self.client.post("/v1/ears/report", json={"state": "asleep"}).status_code, 403)

    def test_a_paired_device_may_not_restart_or_report(self):
        with mock.patch.object(devices, "authorized", return_value={"master": False, "device": {"id": 1}}):
            self.assertEqual(self.client.post("/v1/restart", headers=HEADERS, json={"dry_run": True}).status_code, 403)
            self.assertEqual(self.client.post("/v1/ears/report", headers=HEADERS, json={"state": "asleep"}).status_code, 403)

    def test_a_dry_run_needs_no_reason(self):
        verdict = self.client.post("/v1/restart", headers=HEADERS, json={"dry_run": True}).json()
        self.assertTrue(verdict["safe"])

    def test_ears_report_feeds_the_check(self):
        response = self.client.post("/v1/ears/report", headers=HEADERS, json={"state": "listening"})
        self.assertEqual(response.status_code, 200)
        verdict = self.client.post("/v1/restart", headers=HEADERS, json={"reason": "x", "dry_run": True}).json()
        self.assertFalse(verdict["safe"])
        self.assertIn("listening", verdict["reasons"][0])
        self.client.post("/v1/ears/report", headers=HEADERS, json={"state": "asleep"})
        verdict = self.client.post("/v1/restart", headers=HEADERS, json={"reason": "x", "dry_run": True}).json()
        self.assertTrue(verdict["safe"])
        self.assertTrue(verdict["dry_run"])
        self.assertFalse(verdict["restarting"])


class TestTool(unittest.TestCase):
    def test_listed_for_the_brain(self):
        names = [tool["name"] for tool in mcp_server.tools()]
        self.assertIn("restart_self", names)

    def test_refusal_lists_reasons_and_running_threads(self):
        text = mcp_server.restart_text({
            "safe": False, "restarting": False,
            "reasons": ["the ears are speaking, so he is speaking"],
            "threads": [{"thread_id": "t1", "title": "Build", "environment": "nemurenai", "status": "running"}],
        })
        self.assertIn("Not restarting", text)
        self.assertIn("speaking", text)
        self.assertIn("t1 on nemurenai", text)

    def test_scheduled_and_dry_run_wording(self):
        self.assertIn("scheduled", mcp_server.restart_text({"safe": True, "restarting": True, "threads": []}))
        self.assertIn("dry run", mcp_server.restart_text({"safe": True, "restarting": False, "threads": []}))

    def test_call_posts_the_conversation_and_token(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"safe": False, "reasons": ["a"], "threads": []}
        with mock.patch.object(mcp_server, "get_config", return_value={"hub_token": "tok"}), \
                mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "web-9"}), \
                mock.patch.object(mcp_server.httpx, "post", return_value=response) as post:
            text = mcp_server.call("restart_self", {"reason": "why", "dry_run": True})
        sent = post.call_args.kwargs
        self.assertEqual(sent["json"], {"reason": "why", "conv": "web-9", "dry_run": True})
        self.assertEqual(sent["headers"], {"X-Diablo-Token": "tok"})
        self.assertIn("Not restarting", text)

    def test_unreachable_hub_means_no_restart(self):
        with mock.patch.object(mcp_server.httpx, "post", side_effect=mcp_server.httpx.ConnectError("down")):
            self.assertIn("Not restarting", mcp_server.restart({"reason": "x"}))


class TestEarsClient(unittest.TestCase):
    def test_report_posts_the_state_with_the_token(self):
        from voice import hubclient

        with mock.patch.object(hubclient.httpx, "post") as post:
            self.assertTrue(hubclient.report_ears({"hub_url": "http://hub/", "hub_token": "tok"}, "speaking"))
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://hub/v1/ears/report")
        self.assertEqual(kwargs["json"], {"state": "speaking", "detail": ""})
        self.assertEqual(kwargs["headers"], {"X-Diablo-Token": "tok"})

    def test_a_down_hub_is_not_an_error(self):
        from voice import hubclient

        with mock.patch.object(hubclient.httpx, "post", side_effect=hubclient.httpx.ConnectError("down")):
            self.assertFalse(hubclient.report_ears({"hub_url": "http://hub"}, "asleep"))


class TestCharter(unittest.TestCase):
    def test_the_old_ban_is_gone_and_the_new_rule_is_there(self):
        text = charter.CHARTER
        self.assertNotIn("Never restart or stop diablo-hub.service", text)
        self.assertIn("restart_self", text)
        self.assertIn("speaking", text)
        self.assertIn("t3_thread_send", text)
        self.assertIn("one line", text)
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)


if __name__ == "__main__":
    unittest.main()
