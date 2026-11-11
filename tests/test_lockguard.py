import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import brains, db, devices, lockguard, mcp_server

VOICE = devices.KINDS["voice"]
HYPR_LOCKED = "true\n" + lockguard.MARK + "\nActive=yes\nLockedHint=yes\n\nActive=yes\nLockedHint=no\n"
HYPR_OPEN = "false\n" + lockguard.MARK + "\nActive=yes\nLockedHint=no\n\nActive=yes\nLockedHint=no\n"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        state = Path(self.tmp.name) / "state"
        for name, value in (("STATE", state), ("LOG", state / "locked.log")):
            patch = mock.patch.object(lockguard, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        lockguard._cache.update(at=0.0, screen="")

    def arm(self, screen, origin=VOICE, conv="main"):
        guard = lockguard.for_turn(origin, look=lambda: screen)
        lockguard.arm(conv, guard)
        return guard

    def logged(self):
        return lockguard.LOG.read_text() if lockguard.LOG.exists() else ""


class TestParse(unittest.TestCase):
    def test_the_compositor_saying_true_is_locked(self):
        self.assertEqual(lockguard.parse(HYPR_LOCKED), "locked")

    def test_false_with_no_hint_is_unlocked(self):
        self.assertEqual(lockguard.parse(HYPR_OPEN), "unlocked")

    def test_banditshells_hint_alone_counts_as_locked(self):
        text = "false\n" + lockguard.MARK + "\nActive=yes\nLockedHint=yes\n"
        self.assertEqual(lockguard.parse(text), "locked")

    def test_a_hint_on_an_inactive_session_does_not_count(self):
        text = "false\n" + lockguard.MARK + "\nActive=no\nLockedHint=yes\n"
        self.assertEqual(lockguard.parse(text), "unlocked")

    def test_no_hyprland_is_unknown(self):
        text = "HYPRLAND_INSTANCE_SIGNATURE not set! (is hyprland running?)\n" + lockguard.MARK + "\nActive=yes\nLockedHint=no\n"
        self.assertEqual(lockguard.parse(text), "unknown")

    def test_nothing_at_all_is_unknown(self):
        self.assertEqual(lockguard.parse(""), "unknown")
        self.assertEqual(lockguard.parse("false"), "unknown")

    def test_ssh_failing_is_unknown(self):
        failed = lambda *a, **k: SimpleNamespace(returncode=255, stdout="")
        self.assertEqual(lockguard.probe(run=failed), "unknown")

        def slow(*a, **k):
            raise subprocess.TimeoutExpired("ssh", 4)

        self.assertEqual(lockguard.probe(run=slow), "unknown")

    def test_probe_reads_banditbox_over_ssh(self):
        seen = []

        def run(argv, **kwargs):
            seen.append(argv)
            return SimpleNamespace(returncode=0, stdout=HYPR_LOCKED)

        with mock.patch("socket.gethostname", return_value="diablo-pi"), \
                mock.patch.object(lockguard, "host", return_value="banditbox"):
            self.assertEqual(lockguard.probe(run=run), "locked")
        self.assertEqual(seen[0][0], "ssh")
        self.assertIn("banditbox", seen[0])
        self.assertIn("hyprctl -i 0 locked", seen[0][-1])


class TestOrigin(Case):
    def test_voice_at_his_desk_is_banditbox(self):
        self.assertEqual(lockguard.kind_of(VOICE), "voice")
        self.assertEqual(lockguard.kind_of(VOICE + ", section Home"), "voice")

    def test_desktop_app_on_banditbox_is_banditbox(self):
        self.assertEqual(lockguard.kind_of("desktop app on banditbox desktop"), "desktop")
        self.assertEqual(lockguard.kind_of("desktop app"), "desktop")
        self.assertEqual(lockguard.kind_of("desktop app, section Workout"), "desktop")

    def test_the_desktop_follows_the_lock_host(self):
        with mock.patch.object(lockguard, "host", return_value="kangaeru"):
            self.assertEqual(lockguard.kind_of("desktop app on kangaeru laptop"), "desktop")
            self.assertEqual(lockguard.kind_of("desktop app on banditbox desktop"), "")

    def test_other_places_are_not(self):
        for origin in ("telegram", "telegram voice note", "phone app on Google Pixel 10 Pro Fold",
                       "desktop app on kangaeru", "t3 watcher", "scheduled task", ""):
            self.assertEqual(lockguard.kind_of(origin), "", origin)


class TestForTurn(Case):
    def test_locked_voice_is_restricted_and_says_so(self):
        guard = lockguard.for_turn(VOICE, look=lambda: "locked")
        self.assertTrue(guard.restricted)
        self.assertEqual(guard.line, "[banditbox screen: locked]")

    def test_unknown_counts_as_locked_for_voice(self):
        guard = lockguard.for_turn(VOICE, look=lambda: "unknown")
        self.assertTrue(guard.restricted)
        self.assertEqual(guard.line, "[banditbox screen: unknown, treated as locked]")

    def test_a_failing_look_counts_as_unknown(self):
        def boom():
            raise RuntimeError("no")

        self.assertTrue(lockguard.for_turn(VOICE, look=boom).restricted)

    def test_unknown_desktop_typing_is_not_restricted(self):
        guard = lockguard.for_turn("desktop app on banditbox desktop", look=lambda: "unknown")
        self.assertFalse(guard.restricted)
        self.assertEqual(guard.line, "[banditbox screen: unknown]")

    def test_locked_desktop_is_restricted(self):
        self.assertTrue(lockguard.for_turn("desktop app on banditbox desktop", look=lambda: "locked").restricted)

    def test_unlocked_is_not_restricted(self):
        guard = lockguard.for_turn(VOICE, look=lambda: "unlocked")
        self.assertFalse(guard.restricted)
        self.assertEqual(guard.line, "[banditbox screen: unlocked]")

    def test_phone_and_telegram_never_look(self):
        look = mock.Mock(return_value="locked")
        for origin in ("telegram", "phone app on Google Pixel 10 Pro Fold"):
            guard = lockguard.for_turn(origin, look=look)
            self.assertFalse(guard.restricted)
            self.assertEqual(guard.line, "")
        look.assert_not_called()

    def test_the_switch_turns_it_off(self):
        db.set_setting("lock.enabled", "off")
        look = mock.Mock(return_value="locked")
        guard = lockguard.for_turn(VOICE, look=look)
        self.assertFalse(guard.restricted)
        self.assertEqual(guard.line, "")
        look.assert_not_called()

    def test_the_screen_is_cached_for_a_few_seconds(self):
        run = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout=HYPR_LOCKED))
        with mock.patch("socket.gethostname", return_value="diablo-pi"):
            self.assertEqual(lockguard.screen(now=100.0, run=run), "locked")
            self.assertEqual(lockguard.screen(now=101.0, run=run), "locked")
            self.assertEqual(run.call_count, 1)
            lockguard.screen(now=100.0 + lockguard.CACHE_SECONDS + 1, run=run)
            self.assertEqual(run.call_count, 2)


class TestPolicy(Case):
    def test_harmless_tools_pass(self):
        for name in ("hue_light", "phone_status", "fleet_where", "mcp__diablo__hue_light", "WebSearch",
                     "ToolSearch", "mcp__t3-banditbox__t3_thread_read", "list_reminders"):
            self.assertTrue(lockguard.allowed(name), name)

    def test_a_reminder_may_not_run_as_a_task(self):
        self.assertTrue(lockguard.allowed("add_reminder", {"text": "tea", "due": "+5m"}))
        self.assertFalse(lockguard.allowed("add_reminder", {"text": "ssh banditbox", "due": "+1m", "task": True}))
        self.assertFalse(lockguard.allowed("mcp__diablo__add_reminder", {"text": "x", "task": True}))

    def test_memory_and_todos_cannot_be_written(self):
        self.assertFalse(lockguard.allowed("save_fact"))
        self.assertFalse(lockguard.allowed("workspace_todo"))

    def test_conversations_never_share_a_state_file(self):
        self.assertNotEqual(lockguard.state_path("web-a/b"), lockguard.state_path("web-a_b"))
        self.assertNotEqual(lockguard.state_path("w" * 90 + "1"), lockguard.state_path("w" * 90 + "2"))

    def test_sensitive_tools_are_refused(self):
        for name in ("Bash", "Read", "Write", "Edit", "Agent", "fleet_power", "wake_banditbox", "notify",
                     "send_voice", "pair_device", "revoke_device", "brain_read", "brain_search", "mail_recent",
                     "mail_agent", "workout_log", "cancel_reminder", "run_shell", "fleet_run",
                     "mcp__diablo__fleet_power", "mcp__claude_ai_Gmail__search_threads",
                     "mcp__claude_ai_Google_Drive__read_file_content", "mcp__t3-nemurenai__t3_thread_launch",
                     "mcp__t3-banditbox__t3_thread_send", "something_new", ""):
            self.assertFalse(lockguard.allowed(name), name)


class TestMcpServer(Case):
    def call(self, name, arguments=None):
        reply = mcp_server.handle({"method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
        return reply["content"][0]["text"]

    def test_locked_refuses_a_sensitive_tool_and_logs_it(self):
        self.arm("locked")
        with mock.patch.object(mcp_server.agent, "_call_tool") as house, mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "main"}):
            text = self.call("fleet_power", {"machine": "banditbox", "action": "off"})
        house.assert_not_called()
        self.assertTrue(text.startswith("refused: banditbox's screen is locked"))
        self.assertIn("refused fleet_power via mcp", self.logged())

    def test_locked_still_allows_lights(self):
        self.arm("locked")
        with mock.patch.object(mcp_server.agent, "_call_tool", return_value="ok") as house, \
                mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "main"}):
            self.assertEqual(self.call("hue_light", {"action": "off"}), "ok")
        house.assert_called_once()
        self.assertEqual(self.logged(), "")

    def test_unlocked_allows_everything(self):
        self.arm("unlocked")
        with mock.patch.object(mcp_server.agent, "_call_tool", return_value="done") as house, \
                mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "main"}):
            self.assertEqual(self.call("fleet_power", {"machine": "banditbox", "action": "off"}), "done")
        house.assert_called_once()

    def test_unknown_voice_refuses(self):
        self.arm("unknown")
        with mock.patch.object(mcp_server, "notify") as push, mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "main"}):
            text = self.call("notify", {"text": "secret"})
        push.assert_not_called()
        self.assertIn("unknown state", text)

    def test_a_phone_turn_after_a_locked_voice_turn_is_free_again(self):
        self.arm("locked")
        self.arm("locked", origin="phone app on Google Pixel 10 Pro Fold")
        with mock.patch.object(mcp_server.agent, "_call_tool", return_value="done"), \
                mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "main"}):
            self.assertEqual(self.call("fleet_power", {"machine": "banditbox", "action": "on"}), "done")

    def test_another_conversation_is_not_affected(self):
        self.arm("locked", conv="main")
        with mock.patch.object(mcp_server.agent, "_call_tool", return_value="done"), \
                mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "web-x"}):
            self.assertEqual(self.call("fleet_power", {"machine": "banditbox", "action": "on"}), "done")


class TestHook(Case):
    def hook(self, name):
        err = io.StringIO()
        code = lockguard.hook("main", stdin=io.StringIO(json.dumps({"tool_name": name, "tool_input": {}})), stderr=err)
        return code, err.getvalue()

    def test_locked_blocks_bash(self):
        self.arm("locked")
        code, err = self.hook("Bash")
        self.assertEqual(code, 2)
        self.assertIn("refused: banditbox's screen is locked", err)
        self.assertIn("refused Bash via claude hook", self.logged())

    def test_locked_allows_lights_and_web_search(self):
        self.arm("locked")
        self.assertEqual(self.hook("mcp__diablo__hue_light")[0], 0)
        self.assertEqual(self.hook("WebSearch")[0], 0)

    def test_locked_blocks_a_reminder_task(self):
        self.arm("locked")
        err = io.StringIO()
        event = {"tool_name": "mcp__diablo__add_reminder", "tool_input": {"text": "ssh", "due": "+1m", "task": True}}
        self.assertEqual(lockguard.hook("main", stdin=io.StringIO(json.dumps(event)), stderr=err), 2)

    def test_locked_blocks_connectors_and_t3_steering(self):
        self.arm("locked")
        self.assertEqual(self.hook("mcp__claude_ai_Gmail__search_threads")[0], 2)
        self.assertEqual(self.hook("mcp__t3-nemurenai__t3_thread_launch")[0], 2)
        self.assertEqual(self.hook("mcp__t3-nemurenai__t3_thread_read")[0], 0)

    def test_unlocked_allows_bash(self):
        self.arm("unlocked")
        self.assertEqual(self.hook("Bash"), (0, ""))

    def test_nothing_armed_allows(self):
        self.assertEqual(self.hook("Bash"), (0, ""))

    def test_a_broken_state_file_blocks(self):
        path = lockguard.state_path("main")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertEqual(self.hook("Bash")[0], 2)

    def test_the_real_command_line_blocks_bash(self):
        home = Path(self.tmp.name) / "home"
        state = home / ".local" / "state" / "diablo"
        state.mkdir(parents=True)
        patch = mock.patch.object(lockguard, "STATE", state)
        patch.start()
        self.addCleanup(patch.stop)
        (state / lockguard.state_path("main").name).write_text(json.dumps({"restricted": True, "screen": "locked", "origin": VOICE}))
        env = {**os.environ, "HOME": str(home)}
        event = json.dumps({"tool_name": "Bash", "tool_input": {"command": "loginctl unlock-session"}})
        done = subprocess.run(["bash", "-c", lockguard.hook_command("main")], input=event, capture_output=True,
                              text=True, env=env, timeout=30)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("refused", done.stderr)
        self.assertIn("refused Bash via claude hook", (state / "locked.log").read_text())
        allowed = json.dumps({"tool_name": "mcp__diablo__hue_light", "tool_input": {}})
        done = subprocess.run(["bash", "-c", lockguard.hook_command("main")], input=allowed, capture_output=True,
                              text=True, env=env, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_an_unrestricted_turn_never_starts_python(self):
        self.arm("unlocked")
        command = lockguard.hook_command("main", python="/nonexistent/python")
        done = subprocess.run(["bash", "-c", command], input=json.dumps({"tool_name": "Bash"}) * 5000,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_restricted_turn_with_no_python_still_blocks(self):
        self.arm("locked")
        command = lockguard.hook_command("main", python="/nonexistent/python")
        done = subprocess.run(["bash", "-c", command], input=json.dumps({"tool_name": "Bash"}),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 2)

    def test_settings_put_the_hook_in_front_of_every_tool(self):
        hooks = json.loads(lockguard.hook_settings("main"))["hooks"]["PreToolUse"]
        self.assertEqual(hooks[0]["matcher"], "*")
        self.assertIn("-m hub.lockguard hook main", hooks[0]["hooks"][0]["command"])


class TestAsk(Case):
    def run_ask(self, origin, screen):
        db.set_setting("brain.backend", "api")
        seen = {}

        async def run_api(conv, text, origin_):
            seen["text"] = text
            seen["armed"] = lockguard.armed(conv)
            return brains.Turn(backend="api", text="Yes, sir.")

        with mock.patch.object(brains, "run_api", new=run_api), \
                mock.patch.object(lockguard, "screen", return_value=screen):
            asyncio.run(brains.ask(brains.MAIN, "what time is it", origin=origin))
        return seen

    def test_a_locked_voice_turn_carries_the_line_and_is_armed(self):
        seen = self.run_ask(VOICE, "locked")
        self.assertIn("[banditbox screen: locked]", seen["text"])
        self.assertTrue(seen["armed"]["restricted"])

    def test_a_telegram_turn_carries_no_line_and_is_free(self):
        seen = self.run_ask("telegram", "locked")
        self.assertNotIn("banditbox screen", seen["text"])
        self.assertFalse(seen["armed"]["restricted"])

    def test_the_api_agent_refuses_while_locked(self):
        self.arm("locked")
        with mock.patch.object(brains.agent, "_call_tool") as house:
            refused = lockguard.refusal("run_shell", "main", "api")
        house.assert_not_called()
        self.assertTrue(refused.startswith("refused"))

    def test_a_locked_turn_on_opencode_runs_on_the_guarded_api_brain(self):
        db.set_setting("brain.backend", "opencode")
        opencode = mock.AsyncMock(return_value=brains.Turn(backend="opencode", text="Hi."))
        api = mock.AsyncMock(return_value=brains.Turn(backend="api", text="Hi."))
        with mock.patch.object(brains, "run_opencode", new=opencode), mock.patch.object(brains, "run_api", new=api), \
                mock.patch.object(lockguard, "screen", return_value="locked"):
            asyncio.run(brains.ask("web-x", "hi", origin=VOICE))
        opencode.assert_not_called()
        api.assert_called_once()

    def test_the_api_agent_loop_never_runs_a_refused_tool(self):
        self.arm("locked", conv="web-x")
        call = {"id": "c1", "type": "function", "function": {"name": "run_shell", "arguments": json.dumps({"command": "loginctl unlock-session"})}}
        replies = iter([{"role": "assistant", "content": "", "tool_calls": [call]}, {"role": "assistant", "content": "It needs your screen unlocked, sir."}])
        chats = []

        def chat(messages, tools=None, brain=None):
            chats.append([dict(m) for m in messages])
            return next(replies)

        with mock.patch.object(brains.agent.llm, "chat", side_effect=chat), \
                mock.patch.object(brains.agent, "_run_shell") as shell:
            reply, _ = brains.agent.run_agent("unlock", {}, None, {"guard_conv": "web-x"})
        shell.assert_not_called()
        self.assertIn("unlocked", reply)
        fed_back = [m for m in chats[-1] if m.get("role") == "tool"]
        self.assertTrue(fed_back and fed_back[0]["content"].startswith("refused"))


if __name__ == "__main__":
    unittest.main()
