import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import db, desktop, devices, lockguard, mcp_server

NAMES = ("desktop_front", "read_window", "find_on_screen", "act_on_desktop")


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
        patch = mock.patch.object(mcp_server.settings, "get", return_value="")
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "main"})
        patch.start()
        self.addCleanup(patch.stop)
        self.seen = []

    def ssh(self, stdout='{"ok": true}\n', returncode=0, stderr=""):
        def run(argv, **kwargs):
            self.seen.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)
        return run

    def call(self, name, arguments=None, **ssh):
        with mock.patch("subprocess.run", side_effect=self.ssh(**ssh)), \
                mock.patch.object(mcp_server.lockguard, "host", return_value="desk"):
            reply = mcp_server.handle({"method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
        return reply["content"][0]["text"]

    def remote(self):
        argv, _ = self.seen[-1]
        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[-2], "desk")
        command = argv[-1]
        self.assertTrue(command.startswith("cd Projects/diablo && ./deploy/diablo-hands "), command)
        return shlex.split(command.split(" && ", 1)[1])[1:]


class Listing(Case):
    def test_the_tools_are_listed_with_teaching_descriptions(self):
        tools = {t["name"]: t for t in mcp_server.tools()}
        for name in NAMES:
            self.assertIn(name, tools)
        act = tools["act_on_desktop"]
        self.assertIn("one call", act["description"])
        self.assertIn("click_text", act["description"])
        self.assertIn("never read the window again", act["description"])
        self.assertEqual(act["inputSchema"]["required"], ["steps"])
        kinds = act["inputSchema"]["properties"]["steps"]["items"]["properties"]["do"]["enum"]
        self.assertEqual(set(kinds), {"focus", "click_text", "click", "click_at", "fill", "type", "key", "scroll", "open", "wait"})

    def test_none_of_them_runs_while_locked(self):
        for name in NAMES:
            self.assertNotIn(name, lockguard.SAFE_HOUSE)
            self.assertFalse(lockguard.allowed(name))
            self.assertFalse(lockguard.allowed(f"mcp__diablo__{name}"))


class Calls(Case):
    def test_front(self):
        self.assertEqual(self.call("desktop_front", stdout='{"class": "editor", "ms": 20}\n'), '{"class": "editor", "ms": 20}')
        self.assertEqual(self.remote(), ["front"])
        self.assertEqual(self.seen[-1][0][:3], ["ssh", "-o", "ConnectTimeout=8"])

    def test_read_window(self):
        self.call("read_window", {"window": "Example  Domain", "max": 50})
        self.assertEqual(self.remote(), ["ui", "--window", "Example Domain", "--max", "50"])
        self.call("read_window")
        self.assertEqual(self.remote(), ["ui"])

    def test_find_on_screen_passes_text_after_a_separator(self):
        self.call("find_on_screen", {"text": "-rf  Save", "all": True})
        self.assertEqual(self.remote(), ["find-text", "--all", "--", "-rf Save"])
        text = self.call("find_on_screen", {"text": "  "})
        self.assertIn("say what text to find", json.loads(text)["error"])

    def test_act_sends_the_plan_as_json(self):
        steps = [{"do": "click_text", "text": "Save it's done"}, {"do": "key", "key": "ctrl+s"}]
        self.call("act_on_desktop", {"window": "editor", "steps": steps})
        argv = self.remote()
        self.assertEqual(argv[:4], ["act", "--window", "editor", "--steps"])
        self.assertEqual(json.loads(argv[4]), steps)
        self.assertEqual(self.seen[-1][1]["timeout"], 120)
        self.assertIn("give the steps", json.loads(self.call("act_on_desktop", {}))["error"])

    def test_the_cli_error_comes_back_as_is(self):
        text = self.call("act_on_desktop", {"steps": [{"do": "wait"}]}, stdout='{"error": "the screen is locked"}\n', returncode=1)
        self.assertEqual(json.loads(text), {"error": "the screen is locked"})

    def test_ssh_trouble_is_an_error_object(self):
        text = self.call("desktop_front", stdout="", stderr="ssh: connect to host desk port 22: refused\n", returncode=255)
        self.assertIn("refused", json.loads(text)["error"])

        def broken(argv, **kwargs):
            raise subprocess.TimeoutExpired("ssh", 40)

        with mock.patch("subprocess.run", side_effect=broken), mock.patch.object(mcp_server.lockguard, "host", return_value="desk"):
            text = mcp_server.hands("desktop_front", {})
        self.assertIn("could not reach desk", json.loads(text)["error"])

    def test_the_desktop_repo_and_host_come_from_settings(self):
        with mock.patch.dict(os.environ, {"DIABLO_DESKTOP_REPO": "~/code/diablo"}), \
                mock.patch.object(mcp_server.settings, "get", side_effect=lambda key: "box.example" if key == "desktop.host" else ""):
            with mock.patch("subprocess.run", side_effect=self.ssh()):
                mcp_server.hands("desktop_front", {})
        argv = self.seen[-1][0]
        self.assertEqual(argv[-2], "box.example")
        self.assertTrue(argv[-1].startswith("cd code/diablo && "))

    def test_on_the_desktop_box_itself_it_runs_without_ssh(self):
        with mock.patch("socket.gethostname", return_value="desk"):
            text = self.call("desktop_front", stdout='{"class": "editor"}\n')
        self.assertEqual(text, '{"class": "editor"}')
        argv, kwargs = self.seen[-1]
        self.assertEqual(argv[:2], ["bash", "-c"])
        self.assertEqual(argv[2], "cd Projects/diablo && ./deploy/diablo-hands front")
        self.assertEqual(kwargs["timeout"], 40)

    def test_the_window_verbs_reach_the_desktop_the_same_way(self):
        with mock.patch("subprocess.run", side_effect=self.ssh(stdout="focused zen\n")), \
                mock.patch.object(mcp_server.lockguard, "host", return_value="desk"):
            self.assertEqual(mcp_server.window_verb("focus", {"target": "zen"}), "focused zen")
        argv, kwargs = self.seen[-1]
        self.assertEqual(argv[:3] + argv[-2:-1], ["ssh", "-o", "ConnectTimeout=8", "desk"])
        self.assertEqual(argv[-1], "cd Projects/diablo && .venv-voice/bin/python -m voice.window focus zen")
        self.assertEqual(kwargs["timeout"], 30)

        def broken(argv, **kwargs):
            raise OSError("no ssh")

        with mock.patch("subprocess.run", side_effect=broken), mock.patch.object(mcp_server.lockguard, "host", return_value="desk"):
            self.assertEqual(mcp_server.window_verb("stage", {}), "could not reach desk: no ssh")

    def test_the_last_json_line_is_the_answer(self):
        self.assertEqual(desktop.last_json('warming up\n{"a": 1}\n{"b": 2}\nbye\n'), '{"b": 2}')
        self.assertEqual(desktop.last_json("no json here\n"), "")
        self.assertEqual(desktop.last_json(None), "")

    def test_a_locked_spoken_turn_refuses_before_ssh(self):
        guard = lockguard.for_turn(devices.KINDS["voice"], look=lambda: "locked")
        lockguard.arm("main", guard)
        text = self.call("act_on_desktop", {"steps": [{"do": "click_text", "text": "Save"}]})
        self.assertTrue(text.startswith("refused:"), text)
        self.assertEqual(self.seen, [])


if __name__ == "__main__":
    unittest.main()
