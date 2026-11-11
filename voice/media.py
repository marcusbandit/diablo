"""Quick audio and machine skills for the fast tongue.

Playback goes through playerctl, the speaker switch through wpctl, memos
through pw-record, and the machine report reads /proc and nvidia-smi. Every
run function takes (args, ctx) and returns (ok, text) without raising.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

MEMOS = Path.home() / "Music/Diablo"
ACTIONS = ("play", "pause", "toggle", "next", "previous", "stop")
ALIASES = {
    "headphones": ("headphone",), "speakers": ("analog", "built-in"),
    "hdmi": ("hdmi",), "monitor": ("hdmi",), "tv": ("hdmi",),
}
FAILURES = (OSError, subprocess.TimeoutExpired, ValueError)


def _run(cmd, timeout=5):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _reason(error):
    return str(error).strip()[:120] or error.__class__.__name__


# ---- playback -------------------------------------------------------------

def media_control(args, ctx):
    action = str(args.get("action") or "").lower().strip()
    if action not in ACTIONS:
        return False, "action must be one of " + ", ".join(ACTIONS)
    player = str(args.get("player") or "").strip()
    cmd = ["playerctl"] + (["-p", player] if player else []) + ["play-pause" if action == "toggle" else action]
    try:
        done = _run(cmd)
    except FAILURES as error:
        return False, f"playerctl failed: {_reason(error)}"
    if "No players found" in done.stderr + done.stdout:
        return False, "nothing is playing anywhere"
    if done.returncode != 0:
        return False, f"playerctl failed: {_reason(done.stderr)}"
    return True, f"{action} sent to {player or 'the player'}"


def now_playing(args, ctx):
    cmd = ["playerctl", "-a", "metadata", "--format", "{{playerName}}|{{status}}|{{artist}}|{{title}}"]
    try:
        done = _run(cmd)
    except FAILURES as error:
        return False, f"playerctl failed: {_reason(error)}"
    rows = [line.split("|", 3) for line in done.stdout.splitlines() if line.count("|") >= 3]
    rows = [row for row in rows if row[1] in ("Playing", "Paused")]
    if not rows:
        return True, "nothing is playing"
    row = next((r for r in rows if r[1] == "Playing"), rows[0])
    player, status, artist, title = (part.strip() for part in row)
    what = ", ".join(part for part in (artist, title) if part) or "something untitled"
    return True, f"{player.capitalize()}, {status.lower()}: {what}"


# ---- memos ----------------------------------------------------------------

def record_memo(args, ctx):
    try:
        seconds = int(args.get("seconds") or 10)
    except (TypeError, ValueError):
        return False, "seconds must be a number"
    seconds = max(2, min(120, seconds))
    path = MEMOS / time.strftime("memo-%Y%m%d-%H%M%S.wav")
    try:
        MEMOS.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            ["pw-record", "--rate", "48000", "--channels", "1", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(seconds)
        proc.terminate()
        proc.wait(timeout=3)
    except FAILURES as error:
        return False, f"recording failed: {_reason(error)}"
    if not path.exists() or path.stat().st_size == 0:
        return False, "recording failed: no audio was written"
    return True, f"{seconds} second memo saved to {path}"


# ---- outputs --------------------------------------------------------------

_SINK = re.compile(r"^\s*│?\s*(\*?)\s*(\d+)\.\s+(.*?)\s*(\[vol:[^\]]*\])?\s*$")


def sinks():
    """Parses wpctl status into [{id, name, current}] for the Sinks block."""
    try:
        done = _run(["wpctl", "status"])
    except FAILURES:
        return []
    found, inside = [], False
    for line in done.stdout.splitlines():
        if "Sinks:" in line:
            inside = True
            continue
        if not inside:
            continue
        if "Sources:" in line or not line.replace("│", "").strip():
            break
        match = _SINK.match(line)
        if match:
            found.append({"id": int(match.group(2)), "name": match.group(3).strip(), "current": bool(match.group(1))})
    return found


def _describe(rows):
    return "outputs: " + "; ".join(f"{r['name']}{' (in use)' if r['current'] else ''}" for r in rows)


def _matches(rows, wanted):
    words = ALIASES.get(wanted, (wanted,))
    return [r for r in rows if any(word in r["name"].lower() for word in words)]


def list_outputs(args, ctx):
    rows = sinks()
    return True, _describe(rows) if rows else "no audio outputs found"


def audio_output(args, ctx):
    wanted = str(args.get("name") or "").lower().strip()
    rows = sinks()
    if not rows:
        return False, "no audio outputs found"
    if not wanted:
        return True, _describe(rows)
    hits = _matches(rows, wanted)
    if len(hits) != 1:
        return False, _describe(hits or rows)
    try:
        done = _run(["wpctl", "set-default", str(hits[0]["id"])])
    except FAILURES as error:
        return False, f"switch failed: {_reason(error)}"
    if done.returncode != 0:
        return False, f"switch failed: {_reason(done.stderr)}"
    return True, f"sound now goes to {hits[0]['name']}"


# ---- the machine ----------------------------------------------------------

def _uptime():
    seconds = float(Path("/proc/uptime").read_text().split()[0])
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"up {days} days {hours} hours"
    return f"up {hours} hours {minutes} minutes"


def _load():
    return f"load {os.getloadavg()[0]:.1f} on {os.cpu_count()} cores"


def _memory():
    fields = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        fields[key] = int(value.split()[0])
    total = fields["MemTotal"] / 1048576
    used = total - fields["MemAvailable"] / 1048576
    return f"memory {used:.1f} of {total:.0f} GB"


def _disk():
    return f"disk {shutil.disk_usage('/').free / 1e9:.0f} GB free"


def _gpu():
    if not shutil.which("nvidia-smi"):
        return ""
    done = _run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu", "--format=csv,noheader"])
    temp, util = (part.strip() for part in done.stdout.strip().split(",", 1))
    return f"GPU {temp} degrees at {util.replace(' ', '')} load"


def system_status(args, ctx):
    parts = []
    for piece in (_uptime, _load, _memory, _disk, _gpu):
        try:
            text = piece()
        except FAILURES + (KeyError, IndexError, ZeroDivisionError):
            continue
        if text:
            parts.append(text)
    return True, ", ".join(parts) if parts else "the machine would not say"


# ---- the table ------------------------------------------------------------

def _skill(name, description, run, kind, properties=None, required=()):
    parameters = {"type": "object", "properties": properties or {}}
    if required:
        parameters["required"] = list(required)
    return dict(name=name, description=description, parameters=parameters, run=run, kind=kind, bank="done")


SKILLS = [
    _skill(
        "media_control",
        "Control music or video playback: play, pause, toggle, next, previous, stop. "
        "Optional player name like spotify or firefox.",
        media_control,
        "act",
        {"action": {"type": "string", "enum": list(ACTIONS)}, "player": {"type": "string"}},
        ["action"],
    ),
    _skill("now_playing", "What song or video is playing right now.", now_playing, "tell"),
    _skill(
        "record_memo",
        "Record a voice memo from the microphone for a number of seconds and save it.",
        record_memo,
        "act",
        {"seconds": {"type": "integer", "minimum": 2, "maximum": 120}},
    ),
    _skill(
        "audio_output",
        "Send the sound to another output: headphones, speakers, hdmi, or part of a device name.",
        audio_output,
        "act",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _skill("list_outputs", "List the audio outputs and which one is in use.", list_outputs, "tell"),
    _skill("system_status", "How the machine is doing: uptime, load, memory, disk, GPU.", system_status, "tell"),
]
