"""room in the banditshell launcher: the scene now and why, pause, resume, and each scene to try.
The rows come from `room status --json`."""

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time

ROOM = shutil.which("room") or os.path.expanduser("~/.local/bin/room")
KEYWORDS = ["lights", "lamps", "hue", "cinema", "dim", "bright", "scene"]
LABELS = {
    "focus": "Focus", "everyday": "Everyday", "watching": "Watching", "cinema": "Cinema",
    "intermission": "Intermission", "game": "Game", "call": "Call", "away": "Away", "bedtime": "Bedtime",
}
ICONS = {
    "focus": "wb_sunny", "everyday": "light", "watching": "smart_display", "cinema": "theaters",
    "intermission": "pause_circle", "game": "sports_esports", "call": "call", "away": "lock", "bedtime": "bedtime",
}


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def room(*args, timeout=1.5):
    try:
        return subprocess.run([ROOM, *args], capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def clock(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def rows(text, prefixed=True):
    words = [w for w in text.lower().split() if len(w) >= 3]
    if not prefixed and not any(k.startswith(w) for w in words for k in KEYWORDS + ["room"]):
        return []  # a plain search about something else: don't even ask room
    try:
        s = json.loads(room("status", "--json"))
    except ValueError:
        return [{"id": "missing", "title": "room isn't answering", "subtitle": f"Looked for it at {ROOM}", "icon": "error"}]
    now = time.time()
    out = []
    if not s.get("running"):
        out.append({"id": "start", "title": "Room is not running", "subtitle": "Start it; the lights follow you again",
                    "icon": "power_settings_new", "keywords": KEYWORDS,
                    "run": ["systemctl", "--user", "start", "room.service"]})
    elif s.get("paused_until", 0) > now:
        out.append({"id": "resume", "title": f"Room is paused until {clock(s['paused_until'])}",
                    "subtitle": "Resume: the lights follow you again", "icon": "play_arrow", "keywords": KEYWORDS,
                    "run": [ROOM, "resume"]})
    else:
        state = s.get("state") or "everyday"
        held = [lamp["name"] for lamp in s.get("lamps", []) if lamp.get("held")]
        sub = s.get("reason") or ""
        if held:
            sub += f"; {', '.join(held)} left as you set {'it' if len(held) == 1 else 'them'}"
        if s.get("night"):
            sub += f"; night warmth {round(s['night'] * 100)} %"
        out.append({
            "id": "now", "title": f"Room: {LABELS.get(state, state)}", "subtitle": sub,
            "icon": ICONS.get(state, "light"), "keywords": KEYWORDS,
            "run": [ROOM, "pause", "1h"],
            "actions": [
                {"id": "pause-1h", "title": "Pause for an hour", "icon": "pause", "run": [ROOM, "pause", "1h"]},
                {"id": "pause-morning", "title": "Pause until morning", "icon": "bedtime", "run": [ROOM, "pause", "morning"]},
                {"id": "pause-30m", "title": "Pause 30 minutes", "icon": "pause", "run": [ROOM, "pause", "30m"]},
                *([{"id": "resume", "title": "Take back the lamps you changed", "icon": "restart_alt",
                    "run": [ROOM, "resume"]}] if held else []),
                {"id": "setup", "title": "Open the walkthrough", "icon": "tune", "run": [ROOM, "setup"]},
            ],
        })
    if prefixed:
        for name in room("names", "scenes").split():
            out.append({"id": f"try-{name}", "title": f"Try {LABELS.get(name, name)}",
                        "subtitle": "on the lamps for 8 seconds", "icon": ICONS.get(name, "light"),
                        "keywords": ["try", name], "run": [ROOM, "try", name, "8"]})
    return out


def matches(item, text):
    hay = " ".join([item["title"], *item.get("keywords", [])]).lower()
    return all(w in hay for w in text.lower().split())


for line in sys.stdin:
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if message.get("type") == "query":
        text = message.get("text", "")
        prefixed = bool(message.get("prefixed"))
        items = rows(text, prefixed)
        if text.strip() and prefixed:
            items = [i for i in items if i["id"] in ("now", "start", "resume", "missing") or matches(i, text)]
        elif not prefixed:
            items = [i for i in items if matches(i, text)]
        send({"type": "results", "seq": message["seq"], "items": items})
