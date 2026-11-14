"""The two window verbs: focus only focuses, stage is Super+Shift+Enter on the named window."""

import json
import subprocess
import unittest
from unittest import mock

from hub import mcp_server, route
from voice import apps, commands, window


class FakeHyprland:
    """Two windows on one workspace in the stage layout; 0x2 holds the stage."""

    def __init__(self, layout_on=True):
        self.clients = [
            {"address": "0x1", "mapped": True, "class": "com.t3tools.T3Code", "title": "T3 Code", "workspace": {"id": 1},
             "at": [0, 0], "size": [1000, 1000]},
            {"address": "0x2", "mapped": True, "class": "zen", "title": "Zen Browser", "workspace": {"id": 1},
             "at": [1000, 0], "size": [3000, 1000]},
        ]
        self.active, self.stage, self.layout_on = "0x2", "0x2", layout_on
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[:3] == ["hyprctl", "-j", "clients"]:
            return self.done(json.dumps(self.clients))
        if cmd[:3] == ["hyprctl", "-j", "activewindow"]:
            return self.done(json.dumps({"address": self.active}))
        verb, arg = cmd[1], cmd[2]
        if verb == "dispatch" and "hl.dsp.focus" in arg:
            self.active = arg.split("address:")[1].split('"')[0]
        elif verb == "eval" and "put_on_stage" in arg:
            if not self.layout_on:
                return self.done('error: [string "..."]:1: off', 1)
            old = next(c for c in self.clients if c["address"] == self.stage)
            new = next(c for c in self.clients if c["address"] == self.active)
            old["at"], new["at"], old["size"], new["size"] = new["at"], old["at"], new["size"], old["size"]
            self.stage = self.active
        elif verb == "eval" and "not staged" in arg:
            if f'address:{self.stage}"' not in arg:
                return self.done('error: [string "..."]:1: not staged', 1)
        return self.done("ok")

    @staticmethod
    def done(out, code=0):
        return subprocess.CompletedProcess([], code, stdout=out, stderr="")

    def dispatched(self, word):
        return [c[2] for c in self.calls if c[1] in ("dispatch", "eval") and word in c[2]]


class TestVerbs(unittest.TestCase):
    def setUp(self):
        self.hypr = FakeHyprland()
        patch = mock.patch("voice.window.subprocess.run", side_effect=self.hypr.run)
        patch.start()
        self.addCleanup(patch.stop)

    def test_focus_only_focuses_and_warps_to_the_middle(self):
        self.assertEqual(window.focus("0x1"), (True, ""))
        self.assertEqual((self.hypr.active, self.hypr.stage), ("0x1", "0x2"))
        self.assertEqual(self.hypr.dispatched("put_on_stage"), [])
        self.assertEqual(self.hypr.dispatched("cursor.move"), ["hl.dsp.cursor.move({ x = 500, y = 500 })"])

    def test_stage_calls_the_binds_own_function_and_warps_to_the_stage(self):
        self.assertEqual(window.stage("0x1"), (True, ""))
        self.assertEqual((self.hypr.active, self.hypr.stage), ("0x1", "0x1"))
        self.assertEqual(len(self.hypr.dispatched("put_on_stage")), 1)
        self.assertIn('require("lua.stage")', self.hypr.dispatched("put_on_stage")[0])
        self.assertEqual(self.hypr.dispatched("cursor.move"), ["hl.dsp.cursor.move({ x = 2500, y = 500 })"])

    def test_stage_leaves_a_window_already_on_stage(self):
        self.assertEqual(window.stage("0x2"), (True, ""))
        self.assertEqual(self.hypr.dispatched("put_on_stage"), [])

    def test_stage_says_so_when_the_layout_is_not_the_stage(self):
        self.hypr.layout_on = False
        self.assertEqual(window.stage("0x1"), (False, "the stage layout is not on here"))
        self.assertEqual(self.hypr.dispatched("cursor.move"), [])

    def test_find_matches_spoken_names(self):
        self.assertEqual(window.find("T3 Code")[0]["address"], "0x1")
        self.assertEqual(window.find("zen")[0]["address"], "0x2")
        self.assertIsNone(window.find("discord")[0])

    def test_the_cli_prints_one_line(self):
        with mock.patch("builtins.print") as said:
            self.assertEqual(window.main(["stage", "T3", "Code"]), 0)
        said.assert_called_once_with("staged com.t3tools.T3Code")

    def test_apps_skills_take_the_same_verbs(self):
        self.assertEqual(apps.focus_window({"target": "zen"}, {}), (True, "focused zen"))
        self.assertEqual(self.hypr.stage, "0x2")
        self.assertEqual(apps.stage_window({"target": "t3 code"}, {}), (True, "staged com.t3tools.T3Code"))
        self.assertEqual(self.hypr.stage, "0x1")

    def test_the_ears_run_focus_and_stage_apart(self):
        with mock.patch.object(commands, "_save_history"):
            self.assertTrue(commands._run_act({"action": "focus_window", "window": "0x1"})[0])
            self.assertEqual(self.hypr.stage, "0x2")
            self.assertTrue(commands._run_act({"action": "stage_window", "window": "0x1"})[0])
            self.assertEqual(self.hypr.stage, "0x1")


class TestRouteAndHub(unittest.TestCase):
    DESKTOP = {"windows": [{"address": "0xaa", "class": "zen", "focused": True},
                           {"address": "0xbb", "class": "com.t3tools.T3Code", "focused": False}]}

    def answer(self, action, choice):
        return {"desktop": action, "desktop_confidence": 0.95, "window": choice, "window_confidence": 0.95}

    def test_center_a_named_window_is_a_stage_act(self):
        with mock.patch.object(route.settings, "get", return_value=""):
            act = route.desktop_action(self.answer("stage_window", "w2"), "center T3 Code", self.DESKTOP)
            self.assertEqual((act["action"], act["window"]), ("stage_window", "0xbb"))
            self.assertEqual(act["did"], "put com.t3tools.T3Code on the stage")
            act = route.desktop_action(self.answer("stage_window", "focused"), "stage it", self.DESKTOP)
            self.assertEqual(act["window"], "0xaa")
            act = route.desktop_action(self.answer("focus_window", "w1"), "focus zen", self.DESKTOP)
            self.assertEqual((act["action"], act["did"]), ("focus_window", "switched to zen"))

    def test_the_hub_tools_run_the_verb_on_the_desktop(self):
        seen = {}

        def ssh(argv, **kwargs):
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, stdout="staged com.t3tools.T3Code\n", stderr="")

        with mock.patch("subprocess.run", side_effect=ssh), mock.patch.object(mcp_server.lockguard, "host", return_value="desk"):
            self.assertEqual(mcp_server.window_verb("stage", {"target": "T3 Code"}), "staged com.t3tools.T3Code")
        self.assertEqual(seen["argv"][0], "ssh")
        self.assertEqual(seen["argv"][-2], "desk")
        self.assertIn("-m voice.window stage 'T3 Code'", seen["argv"][-1])
        names = {t["name"] for t in mcp_server.EXTRA}
        self.assertTrue({"focus_window", "stage_window"} <= names)


if __name__ == "__main__":
    unittest.main()
