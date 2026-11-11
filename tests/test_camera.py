import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import camera


def done(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class CameraCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, value in (("PHOTOS", root / "Diablo"), ("SHOTS", root), ("_OCR_LANGS", None)):
            patcher = mock.patch.object(camera, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("_copy_to_clipboard", "_notify"):
            patcher = mock.patch.object(camera, name)
            setattr(self, name.strip("_"), patcher.start())
            self.addCleanup(patcher.stop)


class TestPhoto(CameraCase):
    def test_success_builds_the_ffmpeg_command_and_returns_the_path(self):
        with mock.patch("voice.camera.subprocess.run", return_value=done()) as run:
            ok, text = camera.take_photo({}, {"cfg": {"camera_device": "/dev/video2"}})
        self.assertTrue(ok)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:2], ["ffmpeg", "-hide_banner"])
        self.assertIn("-input_format", cmd)
        self.assertEqual(cmd[cmd.index("-i") + 1], "/dev/video2")
        self.assertEqual(cmd[cmd.index("-frames:v") + 1], "1")
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        path = Path(cmd[-1])
        self.assertEqual(path.parent, camera.PHOTOS)
        self.assertTrue(path.name.startswith("photo-") and path.suffix == ".jpg")
        self.assertEqual(text, f"photo saved to {path}")
        self.assertTrue(camera.PHOTOS.is_dir())
        self.copy_to_clipboard.assert_called_once_with(path, "image/jpeg")
        self.notify.assert_called_once_with(path, "Photo taken")

    def test_default_device(self):
        with mock.patch("voice.camera.subprocess.run", return_value=done()) as run:
            camera.take_photo({}, {"cfg": {}})
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-i") + 1], "/dev/video0")

    def test_failure_returns_the_stderr(self):
        with mock.patch("voice.camera.subprocess.run", return_value=done(stderr="Device busy\n", returncode=1)):
            ok, text = camera.take_photo({}, {"cfg": {}})
        self.assertFalse(ok)
        self.assertIn("Device busy", text)
        self.copy_to_clipboard.assert_not_called()

    def test_timeout_does_not_raise(self):
        with mock.patch("voice.camera.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 15)):
            ok, text = camera.take_photo({}, {"cfg": {}})
        self.assertFalse(ok)
        self.assertIn("ffmpeg", text)


class TestScreenshot(CameraCase):
    def test_window_uses_the_active_window_geometry(self):
        window = json.dumps({"at": [100, 50], "size": [800, 600]})

        def fake(cmd, **kw):
            return done(stdout=window) if cmd[0] == "hyprctl" else done()

        with mock.patch("voice.camera.subprocess.run", side_effect=fake) as run:
            ok, text = camera.take_screenshot({"what": "window"}, {"cfg": {}})
        self.assertTrue(ok)
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls[0], ["hyprctl", "-j", "activewindow"])
        self.assertEqual(calls[1][:3], ["grim", "-g", "100,50 800x600"])
        path = Path(calls[1][-1])
        self.assertEqual(path.parent, camera.SHOTS)
        self.assertTrue(path.name.endswith("_grim.png"))
        self.assertEqual(text, f"screenshot of window saved to {path}")
        self.copy_to_clipboard.assert_called_once_with(path, "image/png")
        self.notify.assert_called_once_with(path, "Screenshot saved")

    def test_default_picks_the_focused_monitor(self):
        monitors = json.dumps([{"name": "DP-1", "focused": False}, {"name": "HDMI-A-1", "focused": True}])

        def fake(cmd, **kw):
            return done(stdout=monitors) if cmd[0] == "hyprctl" else done()

        with mock.patch("voice.camera.subprocess.run", side_effect=fake) as run:
            ok, text = camera.take_screenshot({}, {"cfg": {}})
        self.assertTrue(ok)
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls[0], ["hyprctl", "-j", "monitors"])
        self.assertEqual(calls[1][:3], ["grim", "-o", "HDMI-A-1"])
        self.assertIn("screenshot of screen saved to", text)

    def test_all_is_plain_grim(self):
        with mock.patch("voice.camera.subprocess.run", return_value=done()) as run:
            ok, _text = camera.take_screenshot({"what": "all"}, {"cfg": {}})
        self.assertTrue(ok)
        self.assertEqual(run.call_args.args[0][0], "grim")
        self.assertEqual(len(run.call_args.args[0]), 2)

    def test_grim_failure(self):
        def fake(cmd, **kw):
            return done(stderr="compositor refused", returncode=1) if cmd[0] == "grim" else done(stdout="[]")

        with mock.patch("voice.camera.subprocess.run", side_effect=fake):
            ok, text = camera.take_screenshot({"what": "all"}, {"cfg": {}})
        self.assertFalse(ok)
        self.assertIn("compositor refused", text)


class TestReadScreen(CameraCase):
    def test_collapses_whitespace_and_clips(self):
        monitors = json.dumps([{"name": "DP-1", "focused": True}])
        ocr = "hello   world\n\n\tthis  is\n" + "x" * 700

        def fake(cmd, **kw):
            if cmd[0] == "hyprctl":
                return done(stdout=monitors)
            if cmd[:2] == ["tesseract", "--list-langs"]:
                return done(stdout="List of available languages (2):\neng\ndan\n")
            if cmd[0] == "tesseract":
                return done(stdout=ocr)
            return done()

        with mock.patch("voice.camera.subprocess.run", side_effect=fake) as run:
            ok, text = camera.read_screen({}, {"cfg": {}})
        self.assertTrue(ok)
        self.assertTrue(text.startswith("hello world this is xxx"))
        self.assertEqual(len(text), 600)
        ocr_cmd = [c.args[0] for c in run.call_args_list if c.args[0][0] == "tesseract" and "stdout" in c.args[0]][0]
        self.assertEqual(ocr_cmd[ocr_cmd.index("-l") + 1], "eng+dan")
        self.assertEqual(ocr_cmd[2], "stdout")

    def test_empty_screen_and_eng_only(self):
        def fake(cmd, **kw):
            if cmd[0] == "hyprctl":
                return done(stdout=json.dumps([{"name": "DP-1", "focused": True}]))
            if cmd[:2] == ["tesseract", "--list-langs"]:
                return done(stdout="List of available languages (1):\neng\n")
            return done(stdout="  \n ")

        with mock.patch("voice.camera.subprocess.run", side_effect=fake) as run:
            ok, text = camera.read_screen({}, {"cfg": {}})
        self.assertEqual((ok, text), (True, "nothing readable on screen"))
        ocr_cmd = [c.args[0] for c in run.call_args_list if c.args[0][0] == "tesseract" and "stdout" in c.args[0]][0]
        self.assertEqual(ocr_cmd[ocr_cmd.index("-l") + 1], "eng")


class TestSkillTable(unittest.TestCase):
    def test_every_entry_has_the_keys(self):
        keys = {"name", "description", "parameters", "run", "kind", "bank"}
        self.assertEqual([s["name"] for s in camera.SKILLS], ["take_photo", "take_screenshot", "read_screen"])
        for skill in camera.SKILLS:
            self.assertEqual(set(skill), keys)
            self.assertTrue(callable(skill["run"]))
            self.assertIn(skill["kind"], ("act", "tell"))
            self.assertEqual(skill["parameters"]["type"], "object")


if __name__ == "__main__":
    unittest.main(verbosity=2)
