import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pool.brains import Brains
from pool.repl import (
    Sessions,
    describe,
    inline_runs,
    new_record,
    render_markdown,
    settle_title,
    snapshot,
    system_prompt,
    timeago,
    wrap_runs,
)
from pool.pool import parts_text

REPO = Path(__file__).resolve().parent.parent


class TestSnapshot(unittest.TestCase):
    def test_snapshot_names_the_directory_and_the_git_state(self):
        text = snapshot(REPO)
        self.assertIn(str(REPO), text)
        self.assertIn("git:", text)
        self.assertIn("top level:", text)

    def test_snapshot_of_a_plain_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = snapshot(tmp)
            self.assertIn("not a repository", text)


class TestSystemPrompt(unittest.TestCase):
    def test_prompt_carries_cwd_and_lay_of_the_land(self):
        prompt = system_prompt(REPO)
        self.assertIn(str(REPO), prompt)
        self.assertIn("Lay of the land:", prompt)
        self.assertIn("run_shell, read_file, write_file, edit_file", prompt)


class TestDescribe(unittest.TestCase):
    def test_each_tool_describes_itself(self):
        self.assertEqual(describe("run_shell", {"command": "pytest -q"}), "run: pytest -q")
        self.assertEqual(describe("read_file", {"path": "/x"}), "read: /x")
        self.assertIn("write: /x (5 bytes)", describe("write_file", {"path": "/x", "content": "hello"}))
        self.assertEqual(describe("edit_file", {"path": "/x"}), "edit: /x")
        self.assertEqual(describe("conjure", {}), "conjure")


class TestStepHook(unittest.TestCase):
    def test_hook_sees_each_tool_and_its_result(self):
        client = Brains({}, get_settings=lambda cfg: {})
        tool_reply = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "run_shell", "arguments": json.dumps({"command": "echo seen"})},
            }],
        }
        final = {"role": "assistant", "content": "done, sir"}
        events = []
        with mock.patch.object(Brains, "_chat", side_effect=[tool_reply, final]):
            sid = client.create_session("agent")
            response = client.message(
                sid, "work", ("http://x", "k", "m"), 60, step_hook=events.append
            )
        kinds = [(event["kind"], event["name"]) for event in events]
        self.assertEqual(kinds, [("tool", "run_shell"), ("result", "run_shell")])
        self.assertEqual(events[1]["output"], "seen")
        self.assertEqual(parts_text(response["parts"]), "done, sir")

    def test_max_steps_stops_the_loop(self):
        client = Brains({}, get_settings=lambda cfg: {})
        tool_reply = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "run_shell", "arguments": json.dumps({"command": "echo again"})},
            }],
        }
        with mock.patch.object(Brains, "_chat", return_value=dict(tool_reply)):
            sid = client.create_session("agent")
            response = client.message(
                sid, "loop forever", ("http://x", "k", "m"), 60, max_steps=3
            )
        self.assertEqual(response["parts"], [])


class TestSessions(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="diablo-sessions-")
        self.store = Sessions(directory=self.tmp)

    def record(self, ident, cwd, updated, title="new session"):
        return {
            "id": ident, "title": title, "cwd": cwd,
            "created": updated, "updated": updated,
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_list_is_sorted_newest_first(self):
        self.store.save(self.record("a", "/one", "2026-01-01T00:00:00+00:00"))
        self.store.save(self.record("b", "/two", "2026-01-02T00:00:00+00:00"))
        ids = [record["id"] for record in self.store.list()]
        self.assertEqual(ids, ["b", "a"])

    def test_last_for_matches_the_directory(self):
        self.store.save(self.record("a", "/one", "2026-01-01T00:00:00+00:00"))
        self.store.save(self.record("b", "/two", "2026-01-02T00:00:00+00:00"))
        self.assertEqual(self.store.last_for("/one")["id"], "a")
        self.assertIsNone(self.store.last_for("/nowhere"))

    def test_broken_files_are_skipped(self):
        (Path(self.tmp) / "junk.json").write_text("{not json")
        self.assertEqual(self.store.list(), [])

    def test_a_resumed_session_replays_the_transcript(self):
        from pool.repl import Agent

        record = {
            "id": "abc", "title": "the ledger work", "cwd": "/tmp/one",
            "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00",
            "messages": [
                {"role": "system", "content": "be a butler"},
                {"role": "user", "content": "fix the tests"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "function": {"name": "run_shell", "arguments": json.dumps({"command": "pytest -q"})}}
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "1 passed"},
                {"role": "assistant", "content": "they pass now"},
            ],
        }
        agent = Agent(mock.Mock(), Path("/tmp/one"))
        agent.start(record)
        kinds = [(kind, text) for kind, text in agent.lines]
        self.assertIn(("you", "fix the tests"), kinds)
        self.assertIn(("diablo", "they pass now"), kinds)
        self.assertTrue(any(kind == "tool" and "pytest -q" in text for kind, text in kinds))
        self.assertTrue(any(kind == "result" and "1 passed" in text for kind, text in kinds))
        self.assertEqual(agent.client.sessions[agent.sid][0]["content"], "be a butler")

    def test_a_resumed_session_continues_where_it_left(self):
        client = Brains({}, get_settings=lambda cfg: {})
        final = {"role": "assistant", "content": "carrying on, sir"}
        stored = [
            {"role": "system", "content": "be a butler"},
            {"role": "user", "content": "earlier work"},
            {"role": "assistant", "content": "earlier report"},
        ]
        with mock.patch.object(Brains, "_chat", return_value=final):
            sid = client.restore(stored, "earlier work")
            response = client.message(sid, "and now this", ("http://x", "k", "m"), 60)
        self.assertEqual(parts_text(response["parts"]), "carrying on, sir")
        session = client.sessions[sid]
        self.assertEqual(session[0]["content"], "be a butler")
        self.assertIn("earlier work", [message.get("content") for message in session])


class TestMarkdown(unittest.TestCase):
    def test_inline_styles(self):
        runs = inline_runs("plain **bold** `code` [text](http://x) ~~gone~~ *em*")
        styles = [style for _, style in runs]
        self.assertEqual(styles, ["normal", "bold", "normal", "code", "normal", "link", "dim", "normal", "dim", "normal", "em"])
        texts = [text for text, _ in runs]
        self.assertIn("bold", texts)
        self.assertIn("code", texts)

    def test_snake_case_survives_the_inline_parser(self):
        runs = inline_runs("run pytest on test_pool.py twice")
        self.assertEqual(runs, [("run pytest on test_pool.py twice", "normal")])

    def test_nested_code_inside_bold_leaks_no_backticks(self):
        runs = inline_runs("**run `npm test` twice**")
        texts = "".join(text for text, _ in runs)
        self.assertNotIn("`", texts)
        self.assertEqual(runs[1], ("npm test", "code"))
        self.assertEqual(runs[0], ("run ", "bold"))

    def test_headings_bullets_and_rules(self):
        lines = render_markdown("# Plan\n\n- one\n- two\n\n---\n", 80)
        flat = ["".join(text for text, _ in segments) for segments in lines]
        self.assertIn("▌ Plan", flat[0])
        self.assertTrue(any("• one" in line for line in flat))
        self.assertTrue(any("• two" in line for line in flat))
        self.assertTrue(any(line.startswith("─") for line in flat))

    def test_code_block_is_boxed(self):
        lines = render_markdown("before\n```python\ndef add(a, b):\n    return a + b\n```\nafter", 60)
        flat = ["".join(text for text, _ in segments) for segments in lines]
        self.assertTrue(flat[1].startswith("┌") and flat[1].endswith("┐"))
        self.assertTrue(any(line.startswith("│ ") and "def add" in line and line.endswith(" │") for line in flat))
        self.assertTrue(flat[-2].startswith("└") and flat[-2].endswith("┘"))
        self.assertEqual(flat[-1], "after")

    def test_code_block_hugs_its_content(self):
        lines = render_markdown("```python\nshort = 1\n```\n", 60)
        border = "".join(text for text, _ in lines[0])
        self.assertEqual(len(border), len("short = 1") + 4, border)
        self.assertLess(len(border), 60)

    def test_table_aligns_columns(self):
        md = "| name | count |\n|---|---|\n| ledger | 3 |\n| map | 12 |"
        lines = render_markdown(md, 80)
        flat = ["".join(text for text, _ in segments) for segments in lines]
        self.assertIn("name  ", flat[0])
        self.assertIn("│", flat[0])
        self.assertIn("ledger", flat[1])
        self.assertEqual(len(flat), 3)

    def test_wrap_runs_hangs_the_continuation(self):
        lines = wrap_runs([("  you  ", "label"), ("word " * 30, "normal")], 40, hang="       ")
        self.assertTrue("".join(text for text, _ in lines[0]).startswith("  you  "))
        for line in lines[1:]:
            self.assertEqual(line[0][0], "       ")


class TestRecords(unittest.TestCase):
    def test_settle_title_takes_the_first_user_line(self):
        record = new_record("/tmp/one")
        record["messages"] = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "fix the  failing   tests\nand run them"},
        ]
        settle_title(record)
        self.assertEqual(record["title"], "fix the failing tests and run them")

    def test_settle_title_keeps_a_chosen_title(self):
        record = new_record("/tmp/one")
        record["title"] = "the ledger work"
        settle_title(record)
        self.assertEqual(record["title"], "the ledger work")

    def test_timeago_buckets(self):
        from datetime import datetime, timedelta, timezone

        base = datetime.now(timezone.utc)
        self.assertEqual(timeago((base - timedelta(seconds=10)).isoformat()), "now")
        self.assertEqual(timeago((base - timedelta(minutes=5)).isoformat()), "5m")
        self.assertEqual(timeago((base - timedelta(hours=3)).isoformat()), "3h")
        self.assertEqual(timeago((base - timedelta(days=2)).isoformat()), "2d")


class TestEscapeKeys(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("pool.repl.SESSIONS_DIR", Path(tempfile.mkdtemp(prefix="diablo-sessions-")))
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent(self):
        from pool.repl import Agent

        agent = Agent(mock.Mock(), Path(tempfile.mkdtemp(prefix="diablo-agent-")))
        agent.start()
        return agent

    def test_esc_clears_typed_text_first(self):
        agent = self.agent()
        agent.buffer = "half typed"
        agent.cursor = 5
        agent.handle_key(27)
        self.assertEqual(agent.buffer, "")
        self.assertEqual(agent.cursor, 0)

    def test_esc_with_empty_input_returns_to_live(self):
        agent = self.agent()
        agent.scroll = 40
        agent.handle_key(27)
        self.assertEqual(agent.scroll, 0)

    def test_control_characters_never_reach_the_buffer(self):
        agent = self.agent()
        for key in (1, 3, 5, 7, 12, 27, 127):
            agent.handle_key(key)
        self.assertEqual(agent.buffer, "")


class TestRelaunch(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("pool.repl.SESSIONS_DIR", Path(tempfile.mkdtemp(prefix="diablo-sessions-")))
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent(self):
        from pool.repl import Agent

        agent = Agent(mock.Mock(), Path(tempfile.mkdtemp(prefix="diablo-agent-")))
        agent.start()
        return agent

    def test_relaunch_while_busy_waits_for_the_turn(self):
        with mock.patch("pool.repl.os.execv") as execv:
            agent = self.agent()
            agent.busy = True
            agent.buffer = "/relaunch"
            agent.submit()
            self.assertTrue(agent.relaunch_pending)
            self.assertFalse(execv.called, "exec waits for the turn")
            agent.events.put(("done", None))
            agent.drain_events()
            self.assertTrue(execv.called)

    def test_relaunch_flushes_the_queue_before_exec(self):
        with mock.patch("pool.repl.os.execv") as execv:
            agent = self.agent()
            agent.client.message = lambda *args, **kwargs: {"info": {"role": "assistant"}, "parts": []}
            agent.busy = True
            agent.buffer = "/relaunch"
            agent.submit()
            agent.queued.append("one more thing")
            agent.events.put(("done", None))
            agent.drain_events()
            self.assertFalse(execv.called, "queued work goes first")
            self.assertTrue(agent.busy, "the queued line started")
            agent.events.put(("done", None))
            agent.drain_events()
            self.assertTrue(execv.called)

    def test_relaunch_when_idle_execs_at_once(self):
        with mock.patch("pool.repl.os.execv") as execv:
            agent = self.agent()
            agent.buffer = "/relaunch"
            agent.submit()
            self.assertTrue(execv.called)
            self.assertEqual(
                mock.call(sys.executable, [sys.executable, "-m", "pool.repl"]),
                execv.call_args,
            )

    def test_relaunch_resumes_the_session_after_exec(self):
        import os as os_module

        with mock.patch("pool.repl.os.execv"), mock.patch.dict(os_module.environ, {}, clear=False):
            agent = self.agent()
            agent.record["title"] = "something"
            agent.buffer = "/relaunch"
            agent.submit()
            self.assertEqual(os_module.environ.get("DIABLO_AGENT_RESUME"), "last")


if __name__ == "__main__":
    unittest.main(verbosity=2)
