"""What Marcus is looking at when he says the name, so a voice turn is not a bare transcript.

The brain runs on diablo-pi and gets only text. Without this, "fix this button", "you
misheard me" or "what I just dictated" reach it with nothing to point at. At the wake,
before the listening chime, the ears take a snapshot here:

- lines(): short text notes for the turn: the focused window, the other windows on
  screen, and what he dictated through gvoice in the last few minutes.
- screenshot(): the focused monitor as a small JPEG, taken at the same moment, sent only
  when the words point at the screen (points_at_screen), so a plain question stays fast.

Everything here fails quietly: a missing hyprctl or grim gives fewer notes, never a
failed turn.
"""

import json
import logging
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger("diablo.surroundings")

GVOICE_HISTORY = Path.home() / ".local/share/gvoice/history.json"
DICTATION_WINDOW_S = 180
DICTATIONS_MAX = 3
DICTATION_CHARS = 300
WINDOWS_MAX = 8
TITLE_CHARS = 90
SHOT_WIDTH = 1600
SHOT_QUALITY = 70

# Words that only make sense with the screen in view.
# Plain "this" or "here" is left out: "turn this light off" must stay a fast direct turn.
_SCREEN_RE = re.compile(
    r"\b(over here|over there|on (the|my) screen|screen|look at|you see|can you see|"
    r"button|window|page|tab|ui|interface|layout|icon|logo|menu|panel|sidebar|"
    r"screenshot|pixels?|font|popup|dialog|error message)\b",
    re.I,
)


def _run(cmd, timeout=3.0, text=True):
    try:
        done = subprocess.run(cmd, capture_output=True, text=text, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _json(cmd):
    out = _run(cmd)
    try:
        return json.loads(out) if out else None
    except ValueError:
        return None


def _title(win):
    cls = str(win.get("class") or win.get("initialClass") or "?")
    title = " ".join(str(win.get("title") or "").split())[:TITLE_CHARS]
    if cls == "com.marcusrosado.Diablo":
        cls = "the Diablo desktop app"
    return f"{cls}: {title}" if title and title != cls else cls


def windows(monitors=None):
    """(focused, others on visible workspaces), as hyprctl gives them, or (None, [])."""
    active = _json(["hyprctl", "-j", "activewindow"]) or None
    clients = _json(["hyprctl", "-j", "clients"]) or []
    monitors = (_json(["hyprctl", "-j", "monitors"]) or []) if monitors is None else monitors
    shown = {m.get("activeWorkspace", {}).get("id") for m in monitors}
    focus = (active or {}).get("address")
    others = [
        c for c in clients
        if c.get("mapped", True) and not c.get("hidden")
        and c.get("workspace", {}).get("id") in shown and c.get("address") != focus
    ]
    return (active if active and active.get("address") else None), others[:WINDOWS_MAX]


def dictations(now=None, path=GVOICE_HISTORY):
    """His gvoice dictations from the last few minutes, oldest first, as (seconds ago, text)."""
    now = time.time() if now is None else now
    try:
        items = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        try:
            at = float(item.get("at") or item.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if text and 0 <= now - at <= DICTATION_WINDOW_S:
            out.append((int(now - at), text[:DICTATION_CHARS]))
    return out[-DICTATIONS_MAX:]


def lines(now=None, monitors=None):
    """Short notes for the turn about what is on his screen right now."""
    out = []
    focused, others = windows(monitors)
    if focused:
        out.append(f"Focused window: {_title(focused)} (workspace {focused.get('workspace', {}).get('name', '?')})")
    if others:
        out.append("Also on screen: " + "; ".join(_title(w) for w in others))
    # His gvoice dictations stay out: they go to other apps (T3 threads above all), and a
    # voice turn that carried them answered the dictation instead of what he said to Diablo.
    return out


def points_at_screen(text):
    return bool(_SCREEN_RE.search(text or ""))


def screenshot(monitors=None):
    """The focused monitor as JPEG bytes, about SHOT_WIDTH wide, or None."""
    monitors = (_json(["hyprctl", "-j", "monitors"]) or []) if monitors is None else monitors
    focused = [m for m in monitors if m.get("focused")] or monitors
    if not focused:
        return None
    mon = focused[0]
    # grim's -s is relative to the output's logical size, which is pixels over its scale.
    logical = float(mon.get("width") or SHOT_WIDTH) / float(mon.get("scale") or 1.0)
    scale = min(1.0, SHOT_WIDTH / logical) if logical else 1.0
    cmd = ["grim", "-o", str(mon.get("name")), "-s", f"{scale:.4f}",
           "-t", "jpeg", "-q", str(SHOT_QUALITY), "-"]
    return _run(cmd, timeout=5.0, text=False) or None


class Snapshot:
    """Taken on a thread at the wake, read after the transcript: lines and maybe a picture."""

    def __init__(self):
        import threading

        self.notes, self.shot, self.desktop = [], None, None
        self._done = threading.Event()
        threading.Thread(target=self._take, daemon=True, name="surroundings").start()

    def _take(self):
        try:
            # The windows and players first: cheap, and what a plain desktop command needs.
            from . import commands

            self.desktop = commands.desktop_state()
            monitors = _json(["hyprctl", "-j", "monitors"]) or []
            self.notes = lines(monitors=monitors)
            self.shot = screenshot(monitors)
        except Exception:
            log.exception("surroundings failed")
        finally:
            self._done.set()

    def get(self, timeout=8.0):
        self._done.wait(timeout)
        return list(self.notes), self.shot
