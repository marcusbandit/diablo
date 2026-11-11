"""Reading the desktop: the focused window, players, Discord calls and the lock.

Window changes arrive as Hyprland socket events; players, calls and the lock are polled.
"""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from .context import Player, Snapshot

DISCORD_NAMES = ("discord", "vesktop", "legcord", "armcord", "webcord", "equibop", "dissent")
WAKE_EVENTS = (b"activewindow", b"fullscreen", b"workspace", b"closewindow", b"openwindow", b"focusedmon",
               b"changefloatingmode", b"movewindow")


def run(cmd, env=None, timeout=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def hypr_env():
    """The environment with the live Hyprland instance, which a service started early may lack."""
    env = dict(os.environ)
    try:
        out = subprocess.run(["hyprctl", "instances", "-j"], capture_output=True, text=True, timeout=3, env=env).stdout
        instances = json.loads(out or "[]")
        if instances:
            newest = max(instances, key=lambda i: i.get("time", 0))
            env["HYPRLAND_INSTANCE_SIGNATURE"] = newest["instance"]
            env["WAYLAND_DISPLAY"] = newest.get("wl_socket", env.get("WAYLAND_DISPLAY", ""))
    except (OSError, ValueError, subprocess.SubprocessError, KeyError):
        pass
    return env


def active_window(env):
    try:
        w = json.loads(run(["hyprctl", "activewindow", "-j"], env=env) or "{}")
    except ValueError:
        return "", "", False
    return w.get("class", "") or "", w.get("title", "") or "", is_fullscreen(w.get("fullscreen"))


def is_fullscreen(value):
    """Hyprland's mode: 0 none, 1 maximised, 2 fullscreen, 3 both. Maximised is not fullscreen.
    Releases before 0.42 sent a plain bool."""
    if isinstance(value, bool):
        return value
    return isinstance(value, int) and value >= 2


_BARE = {"at": 0.0, "players": ()}  # players that have no metadata, refreshed every BARE_EVERY seconds
BARE_EVERY = 10.0


def players(now=None):
    """Every MPRIS player. One playerctl call a tick; the ones without metadata every 10 s."""
    now = now or time.time()
    out = run(["playerctl", "-a", "metadata", "--format",
               "{{playerInstance}}\t{{status}}\t{{xesam:url}}\t{{xesam:title}}"])
    found = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0]:
            found[parts[0]] = Player(parts[0], parts[1], parts[2] if len(parts) > 2 else "",
                                     parts[3] if len(parts) > 3 else "")
    # `metadata` skips players that have none at all; ask those for their status, now and then.
    if now - _BARE["at"] >= BARE_EVERY:
        bare = []
        for name in run(["playerctl", "-l"]).split():
            if name not in found:
                status = run(["playerctl", "-p", name, "status"]).strip()
                if status:
                    bare.append(Player(name, status))
        _BARE.update(at=now, players=tuple(bare))
    for p in _BARE["players"]:
        found.setdefault(p.name, p)
    return tuple(found.values())


def discord_pids():
    pids = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            comm = (proc / "comm").read_text().strip().lower()
        except OSError:
            continue
        if any(n in comm for n in DISCORD_NAMES):
            pids.add(proc.name)
    return pids


def discord_call():
    """A Discord client holding a microphone stream is in a voice call. Same test as pushup."""
    try:
        objects = json.loads(run(["pw-dump"], timeout=5) or "[]")
    except ValueError:
        return None
    pids = None
    for obj in objects:
        props = (obj.get("info") or {}).get("props") or {}
        media_class = props.get("media.class", "")
        if not media_class.startswith("Stream/Input/Audio") or "Internal" in media_class:
            continue
        hay = " ".join(str(props.get(k, "")) for k in (
            "application.name", "application.process.binary", "application.id", "node.name", "client.name",
        )).lower()
        if pids is None:
            pids = discord_pids()
        if any(n in hay for n in DISCORD_NAMES) or str(props.get("application.process.id", "")) in pids:
            name = props.get("application.name") or props.get("application.process.binary") or "Discord"
            return "Discord" if name.lower() in DISCORD_NAMES else name
    return None


def session_locked():
    """Any active session of this user with LockedHint=yes. Hyprland from a tty is Type=tty."""
    fake = os.environ.get("ROOM_FAKE_LOCK")
    if fake:
        return Path(fake).exists()
    user = os.environ.get("USER", "")
    rows = (line.split() for line in run(["loginctl", "list-sessions", "--no-legend"]).splitlines())
    ids = [row[0] for row in rows if len(row) >= 3 and row[2] == user]
    if not ids:
        return False
    props = run(["loginctl", "show-session", *ids, "-p", "LockedHint", "-p", "Active"])
    return any("Active=yes" in block and "LockedHint=yes" in block for block in props.split("\n\n"))


class Desktop:
    """Builds snapshots. Cheap checks run every tick, pw-dump and loginctl less often."""

    CALL_EVERY = 5.0
    LOCK_EVERY = 2.0

    def __init__(self):
        self.env = hypr_env()
        self._call = (0.0, None)
        self._lock = (0.0, False)

    def snapshot(self, now=None):
        now = now or time.time()
        fake = os.environ.get("ROOM_FAKE_SNAPSHOT")
        if fake:
            return fake_snapshot(fake)
        cls, title, fullscreen = active_window(self.env)
        if not cls and not title:
            self.env = hypr_env()  # Hyprland restarted, or the service came up before it
            cls, title, fullscreen = active_window(self.env)
        if now - self._call[0] >= self.CALL_EVERY:
            self._call = (now, discord_call())
        if now - self._lock[0] >= self.LOCK_EVERY:
            self._lock = (now, session_locked())
        return Snapshot(cls=cls, title=title, fullscreen=fullscreen, players=players(now),
                        call=self._call[1], locked=self._lock[1])


def fake_snapshot(path):
    """ROOM_FAKE_SNAPSHOT=file.json drives the daemon from a file, for tests on the real lamps."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return Snapshot()
    return Snapshot(cls=d.get("cls", ""), title=d.get("title", ""), fullscreen=bool(d.get("fullscreen")),
                    players=tuple(Player(**p) for p in d.get("players", [])), call=d.get("call"),
                    locked=bool(d.get("locked")))


def watch_hyprland(env_getter, wake, stop):
    """Set `wake` on every window event from Hyprland's socket2. Reconnects forever."""
    backoff = 1.0
    while not stop.is_set():
        env = env_getter()
        sig = env.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        runtime = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        path = Path(runtime) / "hypr" / sig / ".socket2.sock"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(str(path))
                backoff = 1.0
                buf = b""
                while not stop.is_set():
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    if any(line.startswith(WAKE_EVENTS) for line in lines):
                        wake.set()
        except OSError:
            pass
        stop.wait(backoff)
        backoff = min(backoff * 2, 30.0)


def start_hyprland_watch(desktop, wake, stop):
    t = threading.Thread(target=watch_hyprland, args=(lambda: desktop.env, wake, stop), daemon=True, name="hypr")
    t.start()
    return t
