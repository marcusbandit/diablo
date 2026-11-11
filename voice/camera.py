"""His eyes: the webcam, the screen, and the words on it.

These are quick skills the fast tongue runs without Raphael. Each one
takes (args, ctx), shells out to ffmpeg, grim or tesseract, and returns
(ok, text) without ever raising. Pictures land in his Pictures folder and on
the clipboard.
"""

import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger("diablo.camera")

PHOTOS = Path.home() / "Pictures/Diablo"
SHOTS = Path.home() / "Pictures"
DEFAULT_DEVICE = "/dev/video0"
_OCR_LANGS = None


def _run(cmd, timeout, text=True):
    """Runs a command. Returns (done, error); error is a short reason or None."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=text, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return None, f"{cmd[0]} failed: {error}"
    if done.returncode != 0:
        tail = done.stderr if text else done.stderr.decode(errors="replace")
        return done, f"{cmd[0]} failed: {(tail or '').strip()[-120:]}"
    return done, None


def _copy_to_clipboard(path, mime):
    """wl-copy forks a child that keeps serving the clipboard, so its pipes must not be captured."""
    null = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(["wl-copy", "-t", mime], stdin=subprocess.PIPE, stdout=null, stderr=null, start_new_session=True)
        proc.communicate(Path(path).read_bytes(), timeout=5)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        log.debug("clipboard copy failed for %s", path)


def _notify(path, message):
    try:
        subprocess.run(["notify-send", "-i", str(path), "Diablo", message], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        log.debug("notify failed for %s", path)


def _grim(what, out):
    """Builds the grim command for what (screen, window, all). Returns (cmd, error)."""
    if what == "all":
        return ["grim", str(out)], None
    if what == "window":
        done, error = _run(["hyprctl", "-j", "activewindow"], timeout=10)
        if error:
            return None, error
        try:
            win = json.loads(done.stdout or "{}")
            x, y = win["at"]
            w, h = win["size"]
        except (ValueError, KeyError, TypeError):
            return None, "no focused window"
        return ["grim", "-g", f"{x},{y} {w}x{h}", str(out)], None
    done, error = _run(["hyprctl", "-j", "monitors"], timeout=10)
    if error:
        return None, error
    try:
        monitors = json.loads(done.stdout or "[]")
    except ValueError:
        return None, "hyprctl gave no monitors"
    focused = [m for m in monitors if m.get("focused")] or monitors
    if not focused:
        return None, "no monitor found"
    return ["grim", "-o", str(focused[0].get("name")), str(out)], None


def take_photo(args, ctx):
    device = str((ctx.get("cfg") or {}).get("camera_device") or DEFAULT_DEVICE)
    try:
        PHOTOS.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return False, f"cannot create {PHOTOS}: {error}"
    out = PHOTOS / time.strftime("photo-%Y%m%d-%H%M%S.jpg")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1920x1080",
        "-i", device, "-frames:v", "1", "-update", "1", str(out),
    ]
    _done, error = _run(cmd, timeout=15)
    if error:
        return False, error
    _copy_to_clipboard(out, "image/jpeg")
    _notify(out, "Photo taken")
    return True, f"photo saved to {out}"


def take_screenshot(args, ctx):
    what = str(args.get("what") or "screen").lower()
    if what not in ("screen", "window", "all"):
        what = "screen"
    try:
        SHOTS.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return False, f"cannot create {SHOTS}: {error}"
    out = SHOTS / time.strftime("%Y%m%d_%Hh%Mm%Ss_grim.png")
    cmd, error = _grim(what, out)
    if error:
        return False, error
    _done, error = _run(cmd, timeout=15)
    if error:
        return False, error
    _copy_to_clipboard(out, "image/png")
    _notify(out, "Screenshot saved")
    return True, f"screenshot of {what} saved to {out}"


def _ocr_langs():
    global _OCR_LANGS
    if _OCR_LANGS is None:
        done, error = _run(["tesseract", "--list-langs"], timeout=10)
        have = set() if error else set((done.stdout or "").split())
        _OCR_LANGS = "eng+dan" if {"eng", "dan"} <= have else "eng"
    return _OCR_LANGS


def read_screen(args, ctx):
    with tempfile.TemporaryDirectory(prefix="diablo-ocr-") as scratch:
        png = Path(scratch) / "screen.png"
        cmd, error = _grim("screen", png)
        if error:
            return False, error
        _done, error = _run(cmd, timeout=15)
        if error:
            return False, error
        done, error = _run(["tesseract", str(png), "stdout", "-l", _ocr_langs()], timeout=30)
    if error:
        return False, error
    text = re.sub(r"\s+", " ", done.stdout or "").strip()[:600]
    return True, text or "nothing readable on screen"


SKILLS = [
    dict(
        name="take_photo",
        description="Take a picture with the webcam right now and save it. Use for any photo, picture, snapshot or selfie request.",
        parameters={"type": "object", "properties": {}},
        run=take_photo,
        kind="act",
        bank="camera",
    ),
    dict(
        name="take_screenshot",
        description="Screenshot the screen (default), the focused window, or all monitors, saved to Pictures and copied to the clipboard.",
        parameters={"type": "object", "properties": {"what": {"type": "string", "enum": ["screen", "window", "all"]}}},
        run=take_screenshot,
        kind="act",
        bank="camera",
    ),
    dict(
        name="read_screen",
        description="Read the text currently visible on his screen and report it.",
        parameters={"type": "object", "properties": {}},
        run=read_screen,
        kind="tell",
        bank="done",
    ),
]
