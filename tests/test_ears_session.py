"""The ears find the desktop session on their own, and a paste that fails never loses the words."""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from voice import commands, ears, window


class TestFillSession(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.runtime = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def touch(self, name, age=0):
        path = self.runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def test_a_service_started_before_the_compositor_finds_both(self):
        self.touch("wayland-1")
        self.touch("wayland-1.lock")
        self.touch("hypr/abc_123/.socket.sock")
        env = {"XDG_RUNTIME_DIR": str(self.runtime)}
        self.assertEqual(window.fill_session(env), ["WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"])
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-1")
        self.assertEqual(env["HYPRLAND_INSTANCE_SIGNATURE"], "abc_123")

    def test_the_newest_session_wins(self):
        self.touch("wayland-0", age=600)
        self.touch("wayland-1")
        self.touch("hypr/old/.socket.sock", age=600)
        self.touch("hypr/new/.socket.sock")
        env = {"XDG_RUNTIME_DIR": str(self.runtime)}
        window.fill_session(env)
        self.assertEqual((env["WAYLAND_DISPLAY"], env["HYPRLAND_INSTANCE_SIGNATURE"]), ("wayland-1", "new"))

    def test_what_the_session_handed_over_is_kept(self):
        self.touch("wayland-1")
        self.touch("hypr/new/.socket.sock")
        env = {"XDG_RUNTIME_DIR": str(self.runtime), "WAYLAND_DISPLAY": "wayland-5",
               "HYPRLAND_INSTANCE_SIGNATURE": "given"}
        self.assertEqual(window.fill_session(env), [])
        self.assertEqual((env["WAYLAND_DISPLAY"], env["HYPRLAND_INSTANCE_SIGNATURE"]), ("wayland-5", "given"))

    def test_no_session_fills_nothing(self):
        env = {"XDG_RUNTIME_DIR": str(self.runtime)}
        self.assertEqual(window.fill_session(env), [])
        self.assertNotIn("WAYLAND_DISPLAY", env)


class TestTypeText(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patches = [
            mock.patch.object(commands, "_run", side_effect=self.run_cmd),
            mock.patch.object(commands, "_clipboard", return_value=("text/plain", b"before")),
            mock.patch.object(commands.subprocess, "run", side_effect=self.subprocess_run),
            mock.patch.object(commands.time, "sleep"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.copy_fails = False
        self.keys_fail = False

    def run_cmd(self, cmd, timeout=4.0):
        self.calls.append(cmd)
        if cmd[0] == "hyprctl":
            return True, '{"class": "zen"}'
        return (not self.keys_fail), ("no seat" if self.keys_fail else "")

    def subprocess_run(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[:2] == ["wl-copy", "--"] and self.copy_fails:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    def test_a_paste_that_works_gives_the_clipboard_back(self):
        self.assertEqual(commands.type_text("hello there"), (True, ""))
        self.assertIn(["wl-copy", "--type", "text/plain"], self.calls)

    def test_no_clipboard_says_so_in_words_he_can_hear(self):
        self.copy_fails = True
        ok, why = commands.type_text("hello there")
        self.assertFalse(ok)
        self.assertEqual(why, "the clipboard could not be reached")
        self.assertNotIn("wl-copy", why)

    def test_keys_that_fail_leave_the_words_on_the_clipboard(self):
        self.keys_fail = True
        ok, why = commands.type_text("hello there")
        self.assertFalse(ok)
        self.assertIn("on the clipboard", why)
        self.assertNotIn(["wl-copy", "--type", "text/plain"], self.calls)
        self.assertNotIn(["wl-copy", "--clear"], self.calls)


class TestKeepUnpasted(unittest.TestCase):
    def test_the_words_go_to_the_chat(self):
        posted = []
        with mock.patch.object(ears.hubclient, "post_message",
                               side_effect=lambda cfg, source, role, text, meta=None: posted.append((role, text, meta))):
            ears.keep_unpasted({"hub_url": "http://hub.example"}, "hello there", "Dictated")
            deadline = time.monotonic() + 2
            while not posted and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(posted, [("assistant", "Dictated, not pasted:\n\nhello there", {"kind": "unpasted"})])


class TestUnit(unittest.TestCase):
    def test_the_ears_start_and_stop_with_the_desktop_session(self):
        unit = (Path(__file__).resolve().parent.parent / "deploy" / "diablo-ears.service").read_text()
        self.assertIn("After=graphical-session.target", unit)
        self.assertIn("PartOf=graphical-session.target", unit)
        self.assertIn("WantedBy=graphical-session.target", unit)
        self.assertNotIn("default.target", unit)


if __name__ == "__main__":
    unittest.main()
