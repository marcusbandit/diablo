import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import brains, db, mcp_server, settings, t3link, watch


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))


class TestJudge(unittest.TestCase):
    def watch(self, state="open", created=None):
        return {"thread_id": "t", "env": "nemurenai", "title": "x", "state": state, "created": created or time.time()}

    def test_running_thread_is_left_alone(self):
        data = {"thread": {"activeRunId": "run:1"}, "recentRuns": [{"status": "running"}]}
        self.assertEqual(watch.judge(self.watch(), data), (None, None))

    def test_finished_run_after_the_watch_is_done(self):
        now = time.time()
        data = {"thread": {"activeRunId": None}, "recentRuns": [{"status": "completed", "completedAt": _iso(now + 30)}]}
        state, what = watch.judge(self.watch(created=now), data)
        self.assertEqual(state, "done")
        self.assertIn("completed", what)

    def test_old_finished_run_is_not_news(self):
        now = time.time()
        data = {"thread": {"activeRunId": None}, "recentRuns": [{"status": "completed", "completedAt": _iso(now - 3600)}]}
        self.assertEqual(watch.judge(self.watch(created=now), data), (None, None))

    def test_pending_request_asks_once(self):
        data = {"thread": {"activeRunId": "run:1", "pendingRequestCount": 1}, "recentRuns": []}
        state, what = watch.judge(self.watch(), data)
        self.assertEqual(state, "asking")
        self.assertIn("pending request", what)
        self.assertEqual(watch.judge(self.watch(state="asking"), data), (None, None))


class TestWatchLoop(DbCase):
    def test_event_turn_is_delivered_and_closed(self):
        db.add_watch("t1", "nemurenai", "timer app", "main")
        now = time.time()
        data = {"thread": {"activeRunId": None}, "recentRuns": [{"status": "completed", "completedAt": _iso(now + 5)}]}
        delivered = []

        async def deliver(text, watched):
            delivered.append((text, watched["thread_id"]))

        turn = brains.Turn(text="The timer app is done, sir.")
        with mock.patch.object(watch.t3link, "call_tool", return_value=data), \
                mock.patch.object(watch.brains, "ask", mock.AsyncMock(return_value=turn)) as ask:
            asyncio.run(watch.check_once(deliver))
        self.assertEqual(delivered, [("The timer app is done, sir.", "t1")])
        self.assertIn("[t3 event]", ask.call_args.args[1])
        self.assertEqual(db.open_watches(), [])


class FakeStream:
    def __init__(self, lines):
        self.lines = [json.dumps(line).encode() + b"\n" for line in lines]

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""


class TestClaudeEvents(DbCase):
    def test_stream_json_turn_collects_text_steps_and_objects(self):
        session = brains.ClaudeSession("main")
        session.proc = mock.MagicMock()
        session.proc.stdout = FakeStream([
            {"type": "system", "subtype": "init", "session_id": "s-1"},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "mcp__t3-nemurenai__t3_thread_launch", "input": {"title": "Timer app"}},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "mcp__diablo__emit_object", "input": {"kind": "readout", "cells": []}},
            ]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Started."}]}},
            {"type": "result", "subtype": "success", "result": "Started.", "total_cost_usd": 0.01},
        ])
        steps = []

        async def on_step(label):
            steps.append(label)

        result = brains.Turn()
        asyncio.run(session._read(result, on_step))
        self.assertEqual(result.text, "Started.")
        self.assertEqual(result.objects, [{"kind": "readout", "cells": []}])
        self.assertEqual(steps[0], "t3_thread_launch on nemurenai: Timer app")
        self.assertEqual(settings.get("brain.session.claude.main"), "s-1")

    def test_context_size_is_read_from_the_last_call(self):
        session = brains.ClaudeSession("main", lane=True)
        session.proc = mock.MagicMock()
        session.proc.stdout = FakeStream([
            {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 3, "cache_read_input_tokens": 51000,
                                                                   "cache_creation_input_tokens": 400, "output_tokens": 20},
                                              "content": [{"type": "text", "text": "Done."}]}},
            {"type": "result", "subtype": "success", "result": "Done.", "total_cost_usd": 0.01,
             "usage": {"input_tokens": 3, "output_tokens": 20}},
        ])
        result = brains.Turn()
        asyncio.run(session._read(result, None))
        self.assertEqual(result.text, "Done.")
        self.assertEqual((result.tokens, session.tokens), (51403, 51403))

    def test_error_result_is_an_error(self):
        session = brains.ClaudeSession("main")
        session.proc = mock.MagicMock()
        session.proc.stdout = FakeStream([
            {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "usage limit reached"},
        ])
        result = brains.Turn()
        asyncio.run(session._read(result, None))
        self.assertIn("usage limit reached", result.error)


class TestPrefix(unittest.TestCase):
    def test_origin_and_notes(self):
        head = brains.prefix("telegram", ["a note"])
        self.assertTrue(head.startswith("[telegram, "))
        self.assertTrue(head.endswith("\na note"))


class TestT3Link(DbCase):
    def test_only_live_tokens_become_servers(self):
        store = Path(self.tmp.name) / "t3.json"
        with mock.patch.dict("os.environ", {"DIABLO_T3_TOKENS": str(store)}):
            t3link.save({
                "nemurenai": {"url": "https://nemurenai.x/mcp", "token": "a", "expires_at": time.time() + 999},
                "banditbox": {"url": "https://banditbox.x/mcp", "token": "b", "expires_at": time.time() - 1},
            })
            with mock.patch.object(t3link, "live_urls", side_effect=lambda urls: set(urls)):
                servers = t3link.mcp_servers()
            self.assertEqual(list(servers), ["t3-nemurenai"])
            self.assertEqual(servers["t3-nemurenai"]["headers"]["Authorization"], "Bearer a")
            self.assertEqual(store.stat().st_mode & 0o777, 0o600)

    def test_pairing_token_is_parsed(self):
        out = "Pairing with x.\n\nPairing URL: http://localhost:3773/pair#token=abc\nToken: abc123\nExpires: soon\n"
        done = mock.MagicMock(returncode=0, stdout=out, stderr="")
        with mock.patch.object(t3link.subprocess, "run", return_value=done):
            self.assertEqual(t3link.mint_pairing_code("nemurenai"), "abc123")


class TestMcpServer(DbCase):
    def test_lists_house_tools_without_raw_shell(self):
        names = [tool["name"] for tool in mcp_server.tools()]
        for wanted in ("hue_light", "emit_object", "notify", "watch_t3_thread", "brain_search"):
            self.assertIn(wanted, names)
        for unwanted in ("run_shell", "start_task", "frontend_edit"):
            self.assertNotIn(unwanted, names)

    def test_watch_tool_records_the_thread(self):
        with mock.patch.dict("os.environ", {"DIABLO_CONVERSATION": "web-x"}):
            reply = mcp_server.handle({"method": "tools/call", "params": {
                "name": "watch_t3_thread", "arguments": {"environment": "nemurenai", "thread_id": "t9", "title": "app"},
            }})
        self.assertFalse(reply["isError"])
        self.assertEqual(db.open_watches()[0]["conv"], "web-x")


if __name__ == "__main__":
    unittest.main()
