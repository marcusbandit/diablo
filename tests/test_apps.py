import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import apps

DESKTOP = {
    "spotify": "[Desktop Entry]\nType=Application\nName=Spotify\nGenericName=Music Player\nExec=spotify %U\n",
    "kitty": "[Desktop Entry]\nType=Application\nName=kitty\nExec=kitty\nTerminal=false\nCategories=System;TerminalEmulator;\n",
    "spotify-tray": "[Desktop Entry]\nType=Application\nName=Spotify Tray\nExec=spotify --tray\nNoDisplay=true\n",
    "signal": "[Desktop Entry]\nType=Application\nName=Signal\nExec=signal-desktop\n",
    "signal-beta": "[Desktop Entry]\nType=Application\nName=Signal Beta\nExec=signal-desktop-beta\n",
    "logic": "[Desktop Entry]\nType=Application\nName=Logic Pro\nExec=logic\nKeywords=audio;daw;\n",
}

CLIENTS = [
    {"address": "0x1", "mapped": True, "class": "Spotify", "title": "Spotify Premium", "workspace": {"id": 3}},
    {"address": "0x2", "mapped": True, "class": "zen", "title": "Arch wiki", "workspace": {"id": 1}},
    {"address": "0x3", "mapped": False, "class": "ghost", "title": "unmapped", "workspace": {"id": 1}},
]


def done(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


class AppsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for stem, body in DESKTOP.items():
            (Path(self.tmp.name) / f"{stem}.desktop").write_text(body)
        for patcher in (
            mock.patch.object(apps, "APP_DIRS", [Path(self.tmp.name)]),
            mock.patch.dict(apps._CACHE, {"stamp": None, "apps": []}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


class TestIndex(AppsBase):
    def test_index_skips_nodisplay_and_reads_fields(self):
        ids = {e["id"] for e in apps.apps_index()}
        self.assertIn("spotify", ids)
        self.assertNotIn("spotify-tray", ids)
        logic = apps.find_app("logic pro")
        self.assertEqual(logic["keywords"], ["audio", "daw"])
        self.assertFalse(logic["terminal"])

    def test_exact_beats_prefix_beats_substring(self):
        self.assertEqual(apps.find_app("signal")["id"], "signal")
        self.assertEqual(apps.find_app("signal b")["id"], "signal-beta")
        self.assertEqual(apps.find_app("beta")["id"], "signal-beta")
        self.assertEqual(apps.find_app("gnal")["id"], "signal")

    def test_keyword_hit(self):
        self.assertEqual(apps.find_app("audio")["id"], "logic")

    def test_typo_falls_back_to_difflib(self):
        self.assertEqual(apps.find_app("spotfy")["id"], "spotify")
        self.assertIsNone(apps.find_app("zzzz"))

    def test_terminal_and_browser_aliases(self):
        self.assertEqual(apps.find_app("the terminal")["id"], "kitty")
        with mock.patch("voice.apps.subprocess.run", return_value=done("signal.desktop\n")):
            self.assertEqual(apps.find_app("browser")["id"], "signal")


class TestLaunchers(AppsBase):
    def setUp(self):
        super().setUp()
        self.launch = mock.patch("voice.apps._launch", return_value=(True, "")).start()
        self.addCleanup(mock.patch.stopall)

    def test_open_app_launches_gtk_launch(self):
        self.assertEqual(apps.open_app({"name": "Spotify"}, {"cfg": {}}), (True, "opened Spotify"))
        self.launch.assert_called_once_with(["gtk-launch", "spotify"])

    def test_open_unknown_app_names_the_closest(self):
        ok, text = apps.open_app({"name": "signal messenger x"}, {"cfg": {}})
        self.assertFalse(ok)
        self.assertIn("Signal", text)
        self.launch.assert_not_called()

    def test_open_url_adds_https_and_refuses_files(self):
        self.assertEqual(apps.open_url({"url": "example.org"}, {})[1], "opened https://example.org")
        self.launch.assert_called_once_with(["xdg-open", "https://example.org"])
        self.assertFalse(apps.open_url({"url": "file:///etc/passwd"}, {})[0])
        self.launch.assert_called_once()

    def test_web_search_quotes_into_the_template(self):
        ok, _ = apps.web_search({"query": "hello world & co"}, {"cfg": {"search_url": "https://x.test/s?q={q}"}})
        self.assertTrue(ok)
        self.launch.assert_called_once_with(["xdg-open", "https://x.test/s?q=hello+world+%26+co"])

    def test_power_locks_and_refuses_poweroff(self):
        self.assertEqual(apps.power({"action": "poweroff"}, {}), (False, "I only lock or suspend from here"))
        self.launch.assert_not_called()
        self.assertTrue(apps.power({"action": "lock"}, {})[0])
        self.launch.assert_called_once_with(["hyprlock"])


class TestWindows(unittest.TestCase):
    def hyprctl(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[:3] == ["hyprctl", "-j", "clients"]:
            return done(json.dumps(CLIENTS))
        return done("ok\n")

    def setUp(self):
        self.calls = []
        mock.patch("voice.apps.subprocess.run", side_effect=self.hyprctl).start()
        self.addCleanup(mock.patch.stopall)

    def test_focus_window_matches_class_and_dispatches_the_address(self):
        self.assertEqual(apps.focus_window({"target": "spotify"}, {}), (True, "focused Spotify"))
        lua = self.calls[-1][2]
        self.assertEqual(self.calls[-1][:2], ["hyprctl", "dispatch"])
        self.assertIn("focus", lua)
        self.assertIn("address:0x1", lua)

    def test_focus_window_misses_list_open_classes(self):
        ok, text = apps.focus_window({"target": "ghost"}, {})
        self.assertFalse(ok)
        self.assertIn("Spotify", text)
        self.assertEqual(len(self.calls), 1)

    def test_list_windows_skips_unmapped(self):
        ok, text = apps.list_windows({}, {})
        self.assertTrue(ok)
        self.assertEqual(text, "Spotify | Spotify Premium (ws 3); zen | Arch wiki (ws 1)")

    def test_switch_workspace_rejects_out_of_range(self):
        for bad in (0, 11, "x"):
            self.assertFalse(apps.switch_workspace({"number": bad}, {})[0])
        self.assertEqual(self.calls, [])
        self.assertTrue(apps.switch_workspace({"number": "4"}, {})[0])
        self.assertIn("workspace = 4", self.calls[-1][2])


class TestClipboardAndTable(unittest.TestCase):
    def test_read_clipboard_on_empty(self):
        with mock.patch("voice.apps.subprocess.run", return_value=done("", 1)):
            self.assertEqual(apps.read_clipboard({}, {}), (True, "the clipboard is empty"))

    def test_read_clipboard_image(self):
        with mock.patch("voice.apps.subprocess.run", return_value=done("image/png\nimage/jpeg\n")):
            self.assertEqual(apps.read_clipboard({}, {}), (True, "the clipboard holds an image"))

    def test_every_skill_has_all_six_keys(self):
        for skill in apps.SKILLS:
            self.assertEqual(set(skill), {"name", "description", "parameters", "run", "kind", "bank"}, skill["name"])
            self.assertIn(skill["kind"], ("act", "tell"))
            self.assertIn(skill["bank"], ("open", "done"))
            self.assertTrue(callable(skill["run"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
