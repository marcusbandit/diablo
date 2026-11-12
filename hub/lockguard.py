"""Locked mode: while banditbox's screen is locked, turns from it may only do harmless things.

Nobody is verified at the keyboard while the screen is locked, and the room microphone turns noise
and other voices into commands. So a turn spoken at his desk, or sent from the desktop app on
banditbox, runs with a short allowlist of tools while the lock is up. Phone and Telegram are
separate trusted channels and never restricted here.

The lock state comes from banditbox itself over ssh, two signals in one call:

    hyprctl -i 0 locked     the compositor's own answer, true while a session lock surface is up
    loginctl LockedHint     set by banditshell's lock screen once the lock is secure

Either one saying locked counts as locked. hyprctl saying false counts as unlocked. Anything else
(ssh down, Hyprland gone) is unknown, which counts as locked for spoken turns.

The hub arms a small state file per conversation at the start of every turn. Three places read it
and refuse what is not allowed: the diablo MCP server, the api agent's tool loop, and a Claude
PreToolUse hook (`python -m hub.lockguard hook <conv>`) that sees every tool, Bash included.
On the opencode backend a restricted turn runs on the api brain instead, since opencode's own
tools and its remote t3 servers pass none of these.
"""

import hashlib
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

STATE = Path.home() / ".local" / "state" / "diablo"
LOG = STATE / "locked.log"
MARK = "--lockguard-hints--"
PROBE = (
    "hyprctl -i 0 locked 2>&1; echo " + MARK + "; "
    "loginctl show-session $(loginctl list-sessions --no-legend | awk -v u=\"$(id -un)\" '$3==u{print $1}') "
    "-p Active -p LockedHint 2>&1"
)
PROBE_SECONDS = 4
CACHE_SECONDS = 3

SAFE_BUILTINS = {"WebSearch", "WebFetch", "ToolSearch", "TodoWrite", "Skill"}
SAFE_HOUSE = {
    "hue_light", "phone_status", "fleet_where", "emit_object", "recall_facts",
    "t3_where", "list_devices", "watch_t3_thread", "workout_stats",
    "list_reminders", "add_reminder", "now", "update_task", "ask_user", "cancel_question",
    "show_on_screen",
}
LATER_TURNS = {"add_reminder"}
SAFE_T3 = {
    "t3_thread_list", "t3_thread_read", "t3_thread_search", "t3_project_list", "t3_project_read",
    "t3_worktree_list", "t3_worktree_status", "t3_environment_read", "t3_environment_links",
    "task_status", "list_scheduled_tasks", "orchestrator_capabilities",
}

_cache = {"at": 0.0, "screen": ""}
_elsewhere = {}
_cache_lock = threading.Lock()


@dataclass
class Guard:
    kind: str = ""
    screen: str = ""
    restricted: bool = False
    origin: str = ""
    host: str = ""

    @property
    def line(self):
        if not self.kind:
            return ""
        where = self.host or "banditbox"
        if self.screen == "unknown" and self.restricted:
            return f"[{where} screen: unknown, treated as locked]"
        return f"[{where} screen: {self.screen}]"


def parse(output):
    """locked, unlocked or unknown from the probe's output."""
    text = str(output or "")
    hypr, _, hints = text.partition(MARK)
    lines = [line.strip().lower() for line in hypr.strip().splitlines() if line.strip()]
    word = lines[-1] if lines else ""
    hinted = any(
        "Active=yes" in block and "LockedHint=yes" in block
        for block in re.split(r"\n\s*\n", hints)
    )
    if word == "true" or hinted:
        return "locked"
    if word == "false" and MARK in text:
        return "unlocked"
    return "unknown"


def enabled():
    from . import settings

    return str(settings.get("lock.enabled") or "on").strip().lower() not in ("0", "off", "false", "no")


def host():
    from . import settings

    try:
        return str(settings.get("lock.host") or "banditbox").strip() or "banditbox"
    except Exception:
        return "banditbox"


def probe(run=subprocess.run, target=None):
    """Ask the box now, banditbox unless another is named. Never raises; a failure is unknown."""
    import socket

    target = target or host()
    if socket.gethostname() == target:
        argv = ["bash", "-c", PROBE]
    else:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", target, PROBE]
    try:
        done = run(argv, capture_output=True, text=True, timeout=PROBE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if getattr(done, "returncode", 1) == 255:
        return "unknown"
    return parse(done.stdout)


def screen(now=None, run=subprocess.run, target=None):
    now = time.monotonic() if now is None else now
    target = str(target or "").strip().lower().split(".")[0]
    with _cache_lock:
        if target and target != host() and target not in _elsewhere and len(_elsewhere) >= 16:
            _elsewhere.clear()
        cache = _cache if not target or target == host() else _elsewhere.setdefault(target, {"at": 0.0, "screen": ""})
        if cache["screen"] and now - cache["at"] <= CACHE_SECONDS:
            return cache["screen"]
    found = probe(run, target or None)
    with _cache_lock:
        cache.update(at=now, screen=found)
    return found


def _spoken_on():
    from .devices import SPOKEN_ON

    head, _, tail = SPOKEN_ON.partition("{host}")
    return re.compile(re.escape(head) + r"([\w-]+)" + re.escape(tail))


SPOKEN_ON = _spoken_on()


def host_of(origin):
    """The box a spoken turn came from, read off its origin; the lock host when it names none."""
    found = SPOKEN_ON.match(" ".join(str(origin or "").split()))
    return found.group(1) if found else host()


def kind_of(origin):
    """voice or desktop when the turn came from the locked box, else empty.

    The desktop app is the door on banditbox, so an unnamed one counts; a named one counts when its
    device name carries the host's name, as "banditbox desktop" does.
    """
    from .devices import KINDS

    text = " ".join(str(origin or "").split())
    if text.startswith(KINDS["voice"]) or SPOKEN_ON.match(text):
        return "voice"
    desktop = KINDS["desktop"]
    if text.startswith(desktop):
        named = text.startswith(desktop + " on ")
        return "desktop" if not named or host().lower() in text.lower() else ""
    return ""


def decide(kind, found):
    return bool(kind) and (found == "locked" or (found == "unknown" and kind == "voice"))


def for_turn(origin, look=None):
    """The guard for one turn. Never raises: a failure counts as unknown."""
    try:
        if not enabled():
            return Guard(origin=origin)
        kind = kind_of(origin)
        if not kind:
            return Guard(origin=origin)
        where = host_of(origin) if kind == "voice" else host()
        try:
            found = look() if look else screen(target=where)
        except Exception:
            found = "unknown"
        return Guard(kind=kind, screen=found, restricted=decide(kind, found), origin=origin, host=where)
    except Exception:
        kind = ""
        try:
            kind = kind_of(origin)
        except Exception:
            pass
        return Guard(kind=kind, screen="unknown", restricted=kind == "voice", origin=origin)


def state_path(conv):
    conv = str(conv or "main")
    safe = re.sub(r"[^\w.-]", "_", conv)[:60]
    return STATE / f"locked-{safe}-{hashlib.sha1(conv.encode()).hexdigest()[:10]}.json"


def arm(conv, guard):
    """Leave this turn's guard where the tool layers can read it. A restricted turn that cannot be armed fails."""
    path = state_path(conv)
    body = json.dumps({"restricted": guard.restricted, "screen": guard.screen, "kind": guard.kind,
                       "origin": guard.origin[:160], "host": guard.host, "ts": time.time()})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(body)
        tmp.replace(path)
    except OSError:
        if guard.restricted:
            raise
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def armed(conv):
    """The armed state for a conversation; a missing file is unrestricted, a broken one restricted."""
    path = state_path(conv)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {"restricted": False}
    except OSError:
        return {"restricted": True, "screen": "unknown"}
    try:
        data = json.loads(text)
    except ValueError:
        return {"restricted": True, "screen": "unknown"}
    if not isinstance(data, dict):
        return {"restricted": True, "screen": "unknown"}
    return data


def allowed(name, arguments=None):
    """Whether a tool may run while locked. Takes bare house names and Claude's mcp__server__tool names.

    A reminder may be set, but not one that runs as a task: that turn would start later with no lock on it.
    """
    name = str(name or "")
    short = name
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) >= 3 else ""
        short = parts[-1]
        if server.startswith("t3-"):
            return short in SAFE_T3
        if server != "diablo":
            return False
    elif name in SAFE_BUILTINS:
        return True
    if short not in SAFE_HOUSE:
        return False
    if short in LATER_TURNS and isinstance(arguments, dict) and arguments.get("task"):
        return False
    return True


def reason(name, screen="locked", where="banditbox"):
    shown = "locked" if screen != "unknown" else "of unknown state, so it counts as locked"
    return (f"refused: {where or 'banditbox'}'s screen is {shown}, and turns from it may not use {name} until Marcus unlocks it. "
            "Tell him in a line that he needs to unlock first, or to ask from his phone or Telegram.")


def log(name, state, where):
    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} refused {name} via {where}, screen {state.get('screen') or 'locked'}, "
            f"from {str(state.get('origin') or '?')[:60]}\n")
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as handle:
            handle.write(line)
    except OSError:
        pass
    print("diablo.lockguard " + line.strip(), file=sys.stderr, flush=True)


def refusal(name, conv, where="hub", arguments=None):
    """The refusal text when this conversation's turn is locked and the tool is not allowed, else None."""
    state = armed(conv)
    if not state.get("restricted") or allowed(name, arguments):
        return None
    log(name, state, where)
    return reason(name, state.get("screen") or "locked", state.get("host") or "banditbox")


def hook_command(conv, root=None, python=None):
    """The hook's shell line. An unrestricted turn only costs a grep; anything else asks Python."""
    root = root or str(Path(__file__).resolve().parent.parent)
    python = python or sys.executable
    path = shlex.quote(str(state_path(conv)))
    full = f"env PYTHONPATH={shlex.quote(root)} {shlex.quote(python)} -S -m hub.lockguard hook {shlex.quote(str(conv))}"
    return (f"if [ ! -e {path} ] || grep -q '\"restricted\": false' {path}; then cat >/dev/null; exit 0; fi; {full} || exit 2")


def hook_settings(conv):
    """The --settings json that puts the PreToolUse guard in front of every tool in a claude session."""
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": hook_command(conv)}]}]}})


def hook(conv, stdin=sys.stdin, stderr=sys.stderr):
    """PreToolUse: exit 2 blocks the tool and shows stderr to the model. Any failure blocks a restricted turn."""
    try:
        raw = stdin.read()
        state = armed(conv)
        if not state.get("restricted"):
            return 0
        try:
            event = json.loads(raw or "{}")
        except ValueError:
            event = {}
        name = str(event.get("tool_name") or "")
        arguments = event.get("tool_input")
        if name and allowed(name, arguments):
            return 0
        text = refusal(name or "this tool", conv, "claude hook", arguments) or reason(name or "this tool")
    except Exception as error:
        text = f"refused: the lock guard failed ({error}), so nothing runs from a possibly locked banditbox"
    print(text, file=stderr, flush=True)
    return 2


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "hook":
        sys.exit(hook(sys.argv[2] if len(sys.argv) > 2 else "main"))
    print(screen())
