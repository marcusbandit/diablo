import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, db, events, server, t3threads

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


def thread(**overrides):
    base = {
        "threadId": "mcp:abc", "title": "Live presence", "status": "running",
        "activeRunId": "run:1", "pendingRequestCount": 0, "pullRequests": [],
        "link": "<t3-thread://v2/92e91b90/mcp:abc>",
        "createdAt": "2026-11-10T00:00:00Z", "updatedAt": "2026-11-10T00:05:00Z",
    }
    base.update(overrides)
    return base


WATCH = {"thread_id": "mcp:abc", "env": "banditbox", "title": "Presence", "conv": "main",
         "state": "open", "created": 1794268000.0, "checked": 0.0}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for target in (server, server.devices):
            patch = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            patch.start()
            self.addCleanup(patch.stop)
        t3threads._rows, t3threads._read_at, t3threads._lock = {}, 0.0, None
        self.client = TestClient(server.app)


class TestPlainStep(unittest.TestCase):
    def test_words_he_would_use(self):
        cases = {
            "hue_light: desk lamp": "lights",
            "t3_thread_launch on nemurenai: Fix the build": "T3 on nemurenai",
            "watch_t3_thread": "T3",
            "Bash: ssh -o BatchMode=yes bandit@diablo-pi uptime": "ssh diablo-pi",
            "Bash: ls -la": "the shell",
            "mail_jorbis: Send the kit": "mail",
            "WebSearch: weather": "the web",
            "some_new_tool": "some new tool",
            "": "a tool",
        }
        for label, word in cases.items():
            self.assertEqual(events.plain_step(label), word, label)


class TestTurnEvents(Case):
    def chat(self, ask):
        with mock.patch.object(server.brains, "ask", ask), mock.patch.object(server, "publish") as publish:
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": "lights off", "source": "voice"})
        return response, [c.args[0] for c in publish.call_args_list if c.args[0].get("kind") == "turn"]

    def test_thinking_doing_done(self):
        async def ask(conv, text, **kwargs):
            await kwargs["on_step"]("hue_light: all off")
            return brains.Turn(text="Off, sir.", backend="claude")

        response, turns = self.chat(ask)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([t["state"] for t in turns], ["thinking", "doing", "done"])
        self.assertEqual(turns[0], {"kind": "turn", "section": "diablo", "source": "voice", "state": "thinking"})
        self.assertEqual((turns[1]["tool"], turns[1]["step"]), ("lights", "hue_light: all off"))
        self.assertEqual(turns[2]["id"], response.json()["id"])

    def test_the_reply_keeps_its_steps(self):
        turn = brains.Turn(text="Started.", backend="claude", steps=["t3_thread_launch on banditbox: Live presence"])
        response, _ = self.chat(mock.AsyncMock(return_value=turn))
        meta = db.get_messages(1)[0]["meta"]
        self.assertEqual(meta["steps"], [{"step": "t3_thread_launch on banditbox: Live presence", "tool": "T3 on banditbox"}])

    def test_a_raised_turn_is_failed(self):
        response, turns = self.chat(mock.AsyncMock(side_effect=RuntimeError("boom")))
        self.assertEqual(response.status_code, 502)
        self.assertEqual([t["state"] for t in turns], ["thinking", "failed"])

    def test_a_cancelled_turn_is_failed(self):
        with mock.patch.object(server, "publish") as publish:
            async def go():
                with mock.patch.object(server.brains, "ask", mock.AsyncMock(side_effect=asyncio.CancelledError())):
                    await server.chat(server.ChatIn(text="hi", source="voice"), {"device": None})
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(go())
        states = [c.args[0]["state"] for c in publish.call_args_list if c.args[0].get("kind") == "turn"]
        self.assertEqual(states, ["thinking", "failed"])

    def test_an_error_without_text_is_failed(self):
        response, turns = self.chat(mock.AsyncMock(return_value=brains.Turn(text="", error="limit", backend="claude")))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(turns[-1]["state"], "failed")


class TestSummarize(unittest.TestCase):
    def test_running(self):
        row = t3threads.summarize(WATCH, {"thread": thread()})
        self.assertEqual(row["state"], "working")
        self.assertEqual(row["title"], "Live presence")
        self.assertIsNone(row["finished"])
        self.assertEqual(row["open"], "t3code://threads/92e91b90/mcp%3Aabc")

    def test_waiting_on_an_approval_wins(self):
        row = t3threads.summarize(WATCH, {"thread": thread(pendingRequestCount=1)})
        self.assertEqual((row["state"], row["pending"]), ("waiting", 1))

    def test_finished_with_a_pr_and_the_run_times(self):
        data = {
            "thread": thread(status="completed", activeRunId=None, link="https://app.t3.codes/threads/env1/mcp%3Aabc",
                             pullRequests=[{"number": 97, "url": "https://github.com/x/y/pull/97", "state": "merged"},
                                           {"number": 101, "url": "https://github.com/x/y/pull/101", "state": "open"}]),
            "recentRuns": [{"status": "completed", "startedAt": "2026-11-10T00:01:00Z", "completedAt": "2026-11-10T00:21:00Z"}],
        }
        row = t3threads.summarize(WATCH, data)
        self.assertEqual(row["state"], "finished")
        self.assertEqual(row["finished"] - row["started"], 1200)
        self.assertEqual(row["pr"], {"number": 101, "url": "https://github.com/x/y/pull/101", "state": "open"})
        self.assertEqual(row["open"], "t3code://threads/env1/mcp%3Aabc")

    def test_failed_and_stopped(self):
        for status, state in (("failed", "failed"), ("interrupted", "stopped"), ("idle", "finished")):
            row = t3threads.summarize(WATCH, {"thread": thread(status=status, activeRunId=None)})
            self.assertEqual(row["state"], state, status)

    def test_a_missing_link_opens_nothing(self):
        self.assertEqual(t3threads.open_link({"link": "somewhere"}), "")


class TestThreadsEndpoint(Case):
    def seed(self):
        db.add_watch("mcp:abc", "banditbox", "Presence", "main")
        db.add_watch("mcp:old", "nemurenai", "Old", "main")
        with db._lock:
            db._conn.execute("UPDATE t3_watches SET state = 'done', created = ? WHERE thread_id = 'mcp:old'",
                             (time.time() - 2 * t3threads.WINDOW_S,))
            db._conn.execute("UPDATE t3_watches SET created = created - 600 WHERE thread_id = 'mcp:abc'")
            db._conn.commit()
        db.add_watch("mcp:ask", "nemurenai", "Asks", "main")

    @staticmethod
    def call(env, name, args, timeout):
        if args["threadId"] == "mcp:abc":
            return {"thread": thread(threadId="mcp:abc", title="Asks first", pendingRequestCount=2)}
        return {"thread": thread(threadId="mcp:ask", title="Newer, working")}

    def test_lists_recent_watches_with_live_state_and_publishes_only_changes(self):
        self.seed()
        with mock.patch.object(t3threads.t3link, "call_tool", side_effect=self.call), \
                mock.patch.object(t3threads.events, "publish") as publish:
            asyncio.run(t3threads.refresh())
            asyncio.run(t3threads.refresh())
            body = self.client.get("/v1/t3/threads", headers=HEADERS).json()
        self.assertEqual([r["id"] for r in body["threads"]], ["mcp:abc", "mcp:ask"])
        self.assertEqual([r["state"] for r in body["threads"]], ["waiting", "working"])
        self.assertEqual(body["threads"][0]["asked"], "Presence")
        sent = [c.args[0] for c in publish.call_args_list]
        self.assertEqual([e for e in sent if e["kind"] == "t3"], [{"kind": "t3"}])
        self.assertEqual(sorted(e["task"]["id"] for e in sent if e["kind"] == "task"), ["mcp:abc", "mcp:ask"])

    def test_a_stale_list_answers_at_once_and_reads_in_the_background(self):
        self.seed()
        with mock.patch.object(t3threads, "_fresh_quietly", mock.AsyncMock()) as again:
            body = self.client.get("/v1/t3/threads", headers=HEADERS).json()
        self.assertEqual(body["threads"], [])
        again.assert_awaited()

    def test_a_thread_waiting_on_him_is_never_cut(self):
        watches = [{"thread_id": f"t{i}", "state": "done", "created": 1000.0 + i} for i in range(20)]
        watches.append({"thread_id": "old-ask", "state": "asking", "created": 1.0})
        picked = t3threads.chosen(watches)
        self.assertEqual(len(picked), t3threads.MOST)
        self.assertEqual(picked[0]["thread_id"], "old-ask")

    def test_an_unreachable_or_odd_read_keeps_the_row(self):
        db.add_watch("mcp:abc", "kangaeru", "Presence", "main")
        for answer in (RuntimeError("no t3 link to kangaeru"), "thread not found", {"thread": "nope"}):
            t3threads._rows, t3threads._read_at = {}, 0.0
            effect = answer if isinstance(answer, Exception) else None
            with mock.patch.object(t3threads.t3link, "call_tool", side_effect=effect, return_value=answer):
                rows = asyncio.run(t3threads.refresh())
            self.assertEqual((rows[0]["state"], rows[0]["reachable"], rows[0]["title"]), ("unknown", False, "Presence"), answer)

    def test_needs_a_token(self):
        self.assertEqual(self.client.get("/v1/t3/threads").status_code, 403)

    def test_the_loop_reads_only_while_someone_listens(self):
        with mock.patch.object(t3threads, "_fresh_quietly", mock.AsyncMock()) as fresh, \
                mock.patch.object(t3threads.asyncio, "sleep", mock.AsyncMock(side_effect=[None, StopAsyncIteration])):
            with mock.patch.object(t3threads.events, "subscribers", set()):
                with self.assertRaises(StopAsyncIteration):
                    asyncio.run(t3threads.run())
        fresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
