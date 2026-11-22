"""The two window verbs, focus and stage, the only way Diablo moves between windows.

focus: focus the window (Hyprland switches workspace for it), then put the cursor in its middle.
It never stages, centers or resizes anything.

stage: what Super+Shift+Enter does in his stage layout (hypr-config, lua/stage.lua), done to the
named window: focus it, call that bind's own put_on_stage, then put the cursor in its middle.
The staging itself stays in hypr-config; nothing here lays windows out.

thread: open a t3code://threads/... link in T3 Code and focus its window, for a thread the hub
just made ("new thread in AniBeam").

Run over ssh from the hub as `python -m voice.window focus|stage <app or title>` or
`python -m voice.window thread <t3code link>`; it prints one line and exits 0 when done. No target, or "it" or "this", means the focused window. The voice
side calls focus() and stage() with an address.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

STAGE_MODULE = "lua.stage"
OFF = "stage layout off"
HOLDER = 'local s = require("{module}") local w = hl.get_window("address:{address}") ' \
         'error("holder=" .. tostring(w and w.workspace and s.stage_of[w.workspace.id or w.workspace.name]))'
PUT = 'local s = require("{module}") if not s.window() then error("{off}") end s.put_on_stage()'
FOCUSED = ("", "it", "this", "that", "this window", "that window", "the window", "this one")


def _newest(paths):
    found = []
    for path in paths:
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return max(found)[1] if found else None


def fill_session(env):
    """Fills in the desktop session's WAYLAND_DISPLAY and HYPRLAND_INSTANCE_SIGNATURE from the
    runtime dir when the process did not get them (ssh, or a service started before the
    compositor exported them), or when the ones it has point at a compositor that is gone.
    Changes env in place and returns the names it changed."""
    runtime = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    changed = []
    display = env.get("WAYLAND_DISPLAY")
    if not display or not Path(runtime, display).exists():
        socket = _newest(p for p in Path(runtime).glob("wayland-*") if not p.name.endswith(".lock"))
        if socket is not None and socket.name != display:
            env["WAYLAND_DISPLAY"] = socket.name
            changed.append("WAYLAND_DISPLAY")
    signature = env.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not signature or not Path(runtime, "hypr", signature, ".socket.sock").exists():
        socket = _newest(Path(runtime, "hypr").glob("*/.socket.sock"))
        if socket is not None and socket.parent.name != signature:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = socket.parent.name
            changed.append("HYPRLAND_INSTANCE_SIGNATURE")
    return changed


def session_env():
    """This process's env with the session read fresh: a compositor restart makes new sockets,
    and a value read once at start would point at the old ones."""
    env = dict(os.environ)
    fill_session(env)
    return env


def _env():
    return session_env()


def _hyprctl(*args):
    try:
        done = subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=10, env=_env())
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"hyprctl failed: {error}"
    out = (done.stdout or done.stderr).strip()
    return done.returncode == 0 and out == "ok", out


def _json(*args):
    try:
        done = subprocess.run(["hyprctl", "-j", *args], capture_output=True, text=True, timeout=10, env=_env())
        return json.loads(done.stdout or "null") if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def clients():
    rows = _json("clients")
    return [c for c in rows if c.get("mapped")] if isinstance(rows, list) else None


def find(target):
    """(client, why): the open window whose class, then title, holds the words; "it" is the focused one."""
    words = " ".join(str(target or "").lower().split())
    if words in FOCUSED:
        active = _json("activewindow") or {}
        return (active, "") if active.get("address") else (None, "no window has focus")
    rows = clients()
    if rows is None:
        return None, "hyprctl gave no window list"
    squashed = words.replace(" ", "")
    for field in ("class", "initialClass", "title"):
        for c in rows:
            value = str(c.get(field) or "").lower()
            if words in value or squashed in value.replace(" ", ""):
                return c, ""
    return None, f"no window matching {target}; open: " + ", ".join(sorted({str(c.get('class') or '?') for c in rows}))


def _warp(address):
    """Puts the cursor in the middle of the window as it lies now. Best effort: the verb is done either way."""
    c = next((c for c in clients() or [] if c.get("address") == address), None)
    if c:
        (x, y), (w, h) = c.get("at") or (0, 0), c.get("size") or (0, 0)
        _hyprctl("dispatch", f"hl.dsp.cursor.move({{ x = {int(x + w / 2)}, y = {int(y + h / 2)} }})")
    return True, ""


def bring(address):
    """(ok, why): focus the window and check it really is the active one. No cursor, no stage."""
    ok, out = _hyprctl("dispatch", f'hl.dsp.focus({{ window = "address:{address}" }})')
    if not ok:
        return False, out[:120]
    if ((_json("activewindow") or {}).get("address")) != address:
        return False, "that window would not come forward"
    return True, ""


def focus(address):
    """(ok, why): focus only, then the cursor to the window's middle."""
    ok, why = bring(address)
    return _warp(address) if ok else (ok, why)


def holder(address):
    """The address on the stage of this window's workspace, or "" when none or not the stage layout."""
    _, out = _hyprctl("eval", HOLDER.format(module=STAGE_MODULE, address=address))
    found = re.search(r"holder=(0x[0-9a-fA-F]+)", out)
    return found.group(1) if found else ""


def stage(address):
    """(ok, why): focus, Super+Shift+Enter's own put_on_stage, then the cursor to the window's middle."""
    ok, why = bring(address)
    if not ok:
        return ok, why
    if holder(address) != address:
        ok, out = _hyprctl("eval", PUT.format(module=STAGE_MODULE, off=OFF))
        if not ok:
            return False, ("the stage layout is not on here" if OFF in out else f"staging failed: {out[:120]}")
        if holder(address) != address:
            return False, "it would not take the stage"
    return _warp(address)


T3_CLASS = "t3code"
THREAD_LINK = re.compile(r"^t3code://threads/[\w%.:-]+/[\w%.:-]+$")
THREAD_WAIT_S = 6


def t3_window():
    """The T3 Code window, or None."""
    for c in clients() or []:
        if T3_CLASS in str(c.get("class") or c.get("initialClass") or "").lower().replace(".", ""):
            return c
    return None


def thread(link, launch=None, wait=None, sleep=None):
    """(ok, why): opens the thread link in T3 Code, then focuses T3 Code with focus(). A T3 Code
    that is not running yet gets a few seconds to show its window."""
    import time

    if not THREAD_LINK.match(str(link or "")):
        return False, "that is not a T3 Code thread link"
    try:
        (launch or subprocess.Popen)(["xdg-open", link], start_new_session=True, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_env())
    except OSError as error:
        return False, f"xdg-open failed: {error}"
    deadline = time.monotonic() + (THREAD_WAIT_S if wait is None else wait)
    while True:
        c = t3_window()
        if c is not None:
            return focus(c["address"])
        if time.monotonic() >= deadline:
            return False, "T3 Code did not open"
        (sleep or time.sleep)(0.25)


def main(argv):
    if argv and argv[0] == "thread":
        ok, why = thread(argv[1] if len(argv) > 1 else "")
        print("opened the thread in T3 Code" if ok else why)
        return 0 if ok else 1
    if not argv or argv[0] not in ("focus", "stage"):
        print("usage: python -m voice.window focus|stage [app or title] | thread <t3code link>")
        return 2
    client, why = find(" ".join(argv[1:]))
    if client is None:
        print(why)
        return 1
    ok, why = (focus if argv[0] == "focus" else stage)(client["address"])
    name = client.get("class") or "the window"
    print((f"focused {name}" if argv[0] == "focus" else f"staged {name}") if ok else f"{name}: {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
