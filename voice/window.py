"""The two window verbs, focus and stage, the only way Diablo moves between windows.

focus: focus the window (Hyprland switches workspace for it), then put the cursor in its middle.
It never stages, centers or resizes anything.

stage: what Super+Shift+Enter does in his stage layout (hypr-config, lua/stage.lua), done to the
named window: focus it, call that bind's own put_on_stage, then put the cursor in its middle.
The staging itself stays in hypr-config; nothing here lays windows out.

Run over ssh from the hub as `python -m voice.window focus|stage <app or title>`; it prints one
line and exits 0 when done. No target, or "it" or "this", means the focused window. The voice
side calls focus() and stage() with an address.
"""

import functools
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


@functools.lru_cache(maxsize=1)
def _env():
    """The Hyprland session's env, found from the runtime dir when ssh did not bring it."""
    env = dict(os.environ)
    runtime = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if not env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        found = sorted(Path(runtime, "hypr").glob("*/.socket.sock"), key=lambda p: p.stat().st_mtime, reverse=True)
        if found:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = found[0].parent.name
    return env


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


def main(argv):
    if not argv or argv[0] not in ("focus", "stage"):
        print("usage: python -m voice.window focus|stage [app or title]")
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
