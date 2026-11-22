#!/usr/bin/env python3
"""A hub for trying the web app: the real hub/server.py with a canned brain and canned panels.

Everything lives in a temporary directory and every name in it is invented. Nothing reaches a
model, a lamp, Telegram or another machine.

    uv run --no-project --with-requirements hub/requirements.txt web/dev/fakehub.py --port 8799

Then open http://127.0.0.1:8799/app/?token=<printed token>, or pair with a code from
`curl -X POST -H "X-Diablo-Token: <token>" http://127.0.0.1:8799/v1/pair/code`.
DIABLO_WEB picks the build it serves (web/dist by default).
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOKEN = "fake-hub-master-token-0123456789abcdef"

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8799)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--data", help="data directory, a fresh temporary one by default")
args = parser.parse_args()

data = Path(args.data or tempfile.mkdtemp(prefix="diablo-fakehub-"))
data.mkdir(parents=True, exist_ok=True)
(data / "config.toml").write_text(f'hub_token = "{TOKEN}"\nhub_url = "http://{args.host}:{args.port}"\n')
os.environ.update({
    "DIABLO_HUB_DB": str(data / "diablo.db"),
    "DIABLO_HUB_CONFIG": str(data / "config.toml"),
    "DIABLO_HUB_FILES": str(data / "files"),
    "DIABLO_HOME": str(data / "home"),
    "DIABLO_LOCAL_ENV": "/nonexistent/diablo-local.env",
    "DIABLO_AUTOSHIP": "0",
    "DIABLO_MAIL": "0",
    "DIABLO_TAILNET": "tailnet.example.ts.net",
    "WORKOUT_DIR": str(data / "pushup"),
    "WORKOUT_INBOX": str(data / "pushup-inbox"),
})
os.environ.setdefault("DIABLO_WEB", str(REPO / "web" / "dist"))
sys.path.insert(0, str(REPO))

from hub import brains, db, devices, files, modules, questions, sections, selfrestart, server, settings, t3threads, threads, usage, watch  # noqa: E402



async def nothing(*_a, **_k):
    return None


# No skills, no idle reaper, no background readers of real machines.
brains.ensure_skills = lambda: None
brains.reap_idle = nothing
usage.warm = lambda: None
threads.migrate = nothing
threads.executable = lambda: None
watch.keep_links = nothing
watch.run = nothing
selfrestart.announce = nothing
t3threads.run = nothing
devices.hub_name = lambda: "hub.example"


async def offline(self, text):
    raise RuntimeError("no model in the fake hub")


threads.Quick.ask = offline

# The brain: a short pause, a couple of steps, a canned answer.

running = {}


async def fake_ask(conv, text, origin="web", images=None, on_step=None, **kwargs):
    stop = asyncio.Event()
    running[conv] = stop
    slow = "slow" in text.lower()
    steps = ["hue_light: desk lamp", "WebSearch: weather Sometown"] if "lamp" in text.lower() else ["brain_search: notes about the trip", "now"]
    try:
        for step in steps:
            try:
                await asyncio.wait_for(stop.wait(), timeout=6 if slow else 0.7)
                return brains.Turn(text="Stopped.", backend="fake", steps=[])
            except asyncio.TimeoutError:
                pass
            if on_step:
                await on_step(step)
        await asyncio.sleep(0.4)
    finally:
        running.pop(conv, None)
    if text.startswith("[answer to question"):
        reply = "Good, then I book the **morning** train and tell Alex Example."
        return brains.Turn(text=reply, backend="fake", steps=[])
    reply = (
        f"You said: *{text.strip()[:120]}*\n\n"
        "Here is what I found:\n\n"
        "| Item | State |\n| --- | --- |\n| Desk lamp | on, 40% |\n| Window | closed |\n\n"
        "- [x] read the notes\n- [ ] book the table\n"
    )
    return brains.Turn(text=reply, backend="fake", steps=steps,
                       objects=[{"kind": "readout", "title": "Right now", "cells": [{"v": "21.5 C", "k": "Room"}, {"v": "40%", "k": "Desk lamp"}]}])


async def fake_stop(conv):
    event = running.get(conv)
    if event is None:
        return False
    event.set()
    return True


brains.ask = fake_ask
brains.stop = fake_stop

# Panels: canned blocks per module, lamps that switch.

lamps = [
    {"id": "1", "name": "Desk lamp", "on": True, "level": 40, "color": "#ffd166", "ct": 2700, "caps": {"color": True, "ct": [2200, 6500]}},
    {"id": "2", "name": "Ceiling", "on": False, "level": 0, "color": None, "ct": None, "caps": {"color": False, "ct": [2200, 6500]}},
    {"id": "3", "name": "Shelf strip", "on": True, "level": 75, "color": "#74aaff", "ct": None, "caps": {"color": True, "ct": None}},
    {"id": "4", "name": "Hall", "on": False, "level": 0, "reachable": False, "caps": {"color": False, "ct": None}},
]
DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su", "Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def panel(module_id, section):
    if module_id == "today":
        return [{"kind": "stats", "module": "today", "items": [
            {"label": "Push-ups", "value": "62", "unit": "of 100", "icon": "dumbbell", "open": "workout"},
            {"label": "Lamps on", "value": str(sum(1 for l in lamps if l["on"])), "icon": "lightbulb", "open": "lights"},
            {"label": "Next reminder", "value": "16:00", "icon": "alarm"},
            {"label": "Unread mail", "value": "3", "icon": "mail"},
        ]}]
    if module_id == "workout":
        return real_panel(module_id, section)
    if module_id == "workout-canned":
        return [
            {"kind": "goal", "module": "workout", "title": "Push-ups today", "icon": "dumbbell", "value": 62, "max": 100,
             "caption": "On pace for 100 by 21:00",
             "badges": [{"icon": "flame", "label": "12 day streak"}, {"icon": "done", "label": "4 sets today"},
                        {"icon": "calendar", "label": "Day 12 of 30"}]},
            {"kind": "actions", "module": "workout", "items": [
                {"label": "Log 20 push-ups", "icon": "add", "say": "I did 20 push-ups"},
                {"label": "Plan tomorrow", "icon": "calendar", "say": "Plan tomorrow's session", "send": False}]},
            {"kind": "bars", "module": "workout", "title": "Sets a day", "caption": "Last two weeks", "icon": "trend",
             "labels": DAYS, "unit": "set",
             "series": [{"name": "Push", "tone": "accent", "values": [3, 4, 2, 5, 0, 6, 3, 4, 5, 2, 4, 6, 3, 4]},
                        {"name": "Pull", "tone": "ok", "values": [1, 0, 2, 1, 0, 2, 1, 2, 0, 1, 2, 1, 2, 1]}],
             "stacked": True},
            {"kind": "progress", "module": "workout", "label": "Week goal", "value": 21, "max": 30, "caption": "Sets this week"},
            {"kind": "list", "module": "workout", "title": "This week", "icon": "calendar", "items": [
                {"title": "Monday", "detail": "Push-ups, rows", "trailing": "5 sets"},
                {"title": "Tuesday", "detail": "Rest", "trailing": "0 sets"},
                {"title": "Wednesday", "detail": "Push-ups, squats", "trailing": "6 sets"}]},
        ]
    if module_id == "lights":
        return [
            {"kind": "lamps", "module": "lights", "title": "Lamps", "lamps": lamps},
            {"kind": "scenes", "module": "lights", "title": "Scenes", "items": [
                {"id": "s1", "name": "Reading", "colors": ["#ffd166", "#f2f1ee"]},
                {"id": "s2", "name": "Evening", "colors": ["#ff8a70", "#ffd166", "#8b67f2"]},
                {"id": "s3", "name": "Focus", "colors": ["#74aaff"]}]},
            {"kind": "actions", "module": "lights", "items": [
                {"label": "All off", "icon": "off", "act": {"module": "lights", "action": "scene", "args": {"name": "off"}}},
                {"label": "Bright", "icon": "bright", "act": {"module": "lights", "action": "scene", "args": {"name": "bright"}}},
                {"label": "Make it cosy", "icon": "cosy", "say": "Make the lights cosy"}]},
        ]
    if module_id == "network":
        return [{"kind": "devices", "module": "network", "title": "Devices", "items": [
            {"name": "alex-laptop", "kind": "laptop", "online": True, "how": "home network", "last_seen": "now", "os": "Linux"},
            {"name": "alex-phone", "kind": "phone", "online": True, "how": "direct", "last_seen": "now", "os": "Android"},
            {"name": "media-box", "kind": "server", "online": False, "how": "", "last_seen": "3 h ago", "os": "Linux"}]}]
    if module_id == "reminders":
        return [{"kind": "notice", "module": "reminders", "title": "Reminders", "tone": "fail", "retry": True,
                 "text": "The reminder service did not answer."}]
    return []


def act(module_id, action, args):
    if module_id == "workout":
        return real_act(module_id, action, args)
    if module_id != "lights":
        if action == "retry":
            return "Checked again"
        raise modules.ModuleError(module_id)
    if action == "toggle":
        for lamp in lamps:
            if lamp["id"] == args.get("lamp") or args.get("lamp") == "all":
                if lamp.get("reachable") is False:
                    raise modules.ActFailed(f"{lamp['name']} cannot be reached")
                lamp["on"] = not lamp["on"]
                lamp["level"] = lamp["level"] or 60 if lamp["on"] else lamp["level"]
                return f"{lamp['name']} {'on' if lamp['on'] else 'off'}"
    if action == "level":
        for lamp in lamps:
            if lamp["id"] == args.get("lamp"):
                lamp["level"] = int(args.get("level") or 0)
                lamp["on"] = lamp["level"] > 0
                return f"{lamp['name']} at {lamp['level']}%"
    if action == "scene":
        on = args.get("name") != "off"
        for lamp in lamps:
            if lamp.get("reachable") is not False:
                lamp["on"] = on
        return "All off" if not on else "Scene set"
    if action in ("color", "temperature"):
        return "Done"
    raise ValueError(f"unknown action {action}")


def seed_workout():
    """The real workout module over invented training: two sessions today and one still open."""
    import datetime as dt
    import json as _json
    from hub.modules import workout
    now = time.time()
    day = workout.day_of(now)
    (data / "pushup").mkdir(exist_ok=True)
    (data / "pushup-inbox").mkdir(exist_ok=True)
    first = (dt.date.fromisoformat(day) - dt.timedelta(days=27)).isoformat()
    recent = [{"date": (dt.date.fromisoformat(day) - dt.timedelta(days=i)).isoformat(), "sets": n, "lines": [line] if line else []}
              for i, (n, line) in enumerate([(4, "squats in 3 sets, body rows"), (6, "push-ups in 3 sets, plank in 3 sets"), (0, ""),
                                             (5, "lunges in 3 sets, chin-ups in 2 sets"), (3, "push-ups in 3 sets")])]
    overview = {"schema": 1, "generated": now - 120, "day": day, "mode": "mixed",
                "today": {"sets": 3, "line": "36 squats in 3 sets", "trained": []}, "streak": 4,
                "last": {"ts": now - 5400, "exercise": "squats", "did": 12},
                "week": {"neglected": ["calves", "obliques", "biceps"], "work": {"chest": 0.8, "quads": 1.0, "lats": 0.5, "abs": 0.6}, "stretch": {}},
                "timeline": {"first": first, "days": 28, "recent": recent,
                             "sets": {"push": [0] * 24 + [3, 0, 6, 0], "pull": [0] * 24 + [2, 0, 0, 1], "legs": [0] * 24 + [0, 3, 0, 3], "core": [0] * 24 + [0, 0, 3, 0]}},
                "challenge": None}
    (data / "pushup" / "overview.json").write_text(_json.dumps(overview))
    lines = []
    for sid, exercise, sets, end, closed in (("s-demo1", "squats", [15, 12, 12], now - 5400, True),
                                             ("s-demo2", "body rows", [8, 8], now - 300, False)):
        for i, did in enumerate(sets):
            ts = end - 90 * (len(sets) - 1 - i)
            lines.append({"id": f"demo-{sid}-{i}", "ts": ts, "logged": ts, "result": "manual", "exercise": exercise,
                          "did": did, "session_id": sid, "set_no": i + 1, "source": "app"})
        if closed:
            lines.append({"id": f"demo-{sid}-close", "ts": end + 60, "logged": end + 60, "result": "close", "session_id": sid, "source": "app"})
    (data / "pushup-inbox" / "diablo-demo.jsonl").write_text("".join(_json.dumps(l) + "\n" for l in lines))


real_panel = modules.panel
real_act = modules.act
seed_workout()
modules.panel = panel
modules.act = act

# The sections and their history.

SECTIONS = [
    {"id": "diablo", "name": "Diablo", "icon": "flame", "accent": "#2d81ff", "brief": "", "position": 0, "panels": ["today"]},
    {"id": "workout", "name": "Workout", "icon": "dumbbell", "accent": "#3fd6b8", "brief": "Training: sets, reps and the next session.", "position": 1, "panels": ["workout"]},
    {"id": "home", "name": "Home", "icon": "lightbulb", "accent": "#f2c94c", "brief": "The lamps and the house.", "position": 2, "panels": ["lights", "network", "reminders"]},
    {"id": "projects", "name": "Projects", "icon": "folder", "accent": "#f2a516", "brief": "Code and projects.", "position": 3, "panels": []},
]

PICTURE = """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="300" viewBox="0 0 640 300">
<rect width="640" height="300" fill="#15171a"/><g fill="none" stroke="#2d81ff" stroke-width="4">
<polyline points="20,250 120,200 220,220 320,120 420,150 520,60 620,90"/></g>
<g fill="#3fd6b8"><circle cx="320" cy="120" r="8"/><circle cx="520" cy="60" r="8"/></g>
<text x="20" y="40" fill="#f2f1ee" font-family="sans-serif" font-size="22">Steps a day, made up</text></svg>"""

REPLY = """Here is the plan for **Saturday**, as Alex Example asked:

1. Train to Sometown at 09:12
2. Lunch at the harbour, table for two
3. Back by 18:00

| Leg | Leaves | Arrives |
| --- | --- | --- |
| Out | 09:12 | 10:40 |
| Back | 16:25 | 17:55 |

- [x] tickets checked
- [ ] table booked

The booking page is at [example.com](https://example.com/booking). A quick script for the times:

```python
legs = {"out": ("09:12", "10:40"), "back": ("16:25", "17:55")}
for name, (leave, arrive) in legs.items():
    print(f"{name}: {leave} to {arrive}")
```

> Weather looks dry, 14 C at noon.
"""

DIFF = """diff --git a/trip.toml b/trip.toml
--- a/trip.toml
+++ b/trip.toml
@@ -1,4 +1,4 @@
 [saturday]
-train = "08:40"
+train = "09:12"
 seats = 2
+table = "harbour"
"""

PAGE = """<!doctype html><html><head><style>
body{padding:16px;font:15px var(--font-sans)} .row{display:flex;gap:8px;align-items:center;margin:6px 0}
.bar{height:14px;background:var(--accent)} .k{width:80px;color:var(--muted)}
</style></head><body><h3 style="margin:0 0 8px">Week at a glance</h3>
<div class="row"><span class="k">Monday</span><span class="bar" style="width:120px"></span></div>
<div class="row"><span class="k">Tuesday</span><span class="bar" style="width:60px"></span></div>
<div class="row"><span class="k">Wednesday</span><span class="bar" style="width:200px"></span></div>
<script>document.body.innerHTML = "scripts ran, the sandbox failed"</script></body></html>"""


def add(section, role, text, ts, meta=None):
    meta = dict(meta or {}, section=section)
    message_id = db.add_message("web" if role == "user" else "fleet", role, text, meta)
    with db._lock:
        db._conn.execute("UPDATE messages SET ts = ? WHERE id = ?", (ts, message_id))
        db._conn.commit()
    return message_id


def seed():
    stored = [dict(s, hidden=False, deleted=False, updated=time.time()) for s in SECTIONS]
    # The hub fills in every seed a stored list lacks; the fake shows only its own four.
    stored += [dict(seed, deleted=True, updated=time.time()) for seed in sections.seeds() if seed["id"] not in {s["id"] for s in SECTIONS}]
    settings.set_setting(sections.KEY, json.dumps(stored))
    now = time.time()
    day = 86400
    # Enough old lines to page backwards through.
    for i in range(70):
        ts = now - 3 * day + i * 600
        if i % 2 == 0:
            add("diablo", "user", f"Note {i // 2 + 1}: remember to water the plants", ts)
        else:
            add("diablo", "assistant", f"Noted, number {i // 2 + 1}.", ts)
    add("diablo", "user", "Plan Saturday with Alex Example, and show me what changed", now - 1800)
    picture = files.save(PICTURE.encode(), "steps.svg", "image/svg+xml")
    add("diablo", "assistant", REPLY, now - 1790, {
        "steps": [{"step": "brain_search: Saturday plans", "tool": "the vault"}, {"step": "WebSearch: trains Sometown", "tool": "the web"}],
        "objects": [
            {"kind": "readout", "title": "The trip", "cells": [{"v": "2", "k": "People"}, {"v": "09:12", "k": "Train"}, {"v": "14 C", "k": "Noon"}]},
            {"kind": "table", "title": "Costs", "head": ["What", "Price"], "rows": [["Train", "240 kr"], ["Lunch", "380 kr"]]},
            {"kind": "diff", "title": "trip.toml", "diff": DIFF},
            {"kind": "list", "title": "Mail about it", "icon": "mail", "items": [
                {"title": "Alex Example", "subject": "Saturday", "detail": "Sounds good, see you at the station", "trailing": "10:02",
                 "important": True, "url": "https://example.com/mail/1",
                 "facts": [{"label": "From", "value": "alex@example.com"}, {"label": "When", "value": "today 10:02"}],
                 "reply": "Reply to Alex: "},
                {"title": "Tickets", "detail": "Your booking 1234", "trailing": "yesterday"}]},
            {"kind": "image", "title": "Steps a day", "file": picture["id"], "name": "steps.svg", "caption": "A made up chart"},
            {"kind": "code", "title": "times.sh", "lang": "bash", "code": "for t in 09:12 16:25; do\n  echo \"train at $t\"\ndone"},
            {"kind": "html", "title": "Week at a glance", "html": PAGE, "height": 150},
            {"kind": "mystery", "note": "an unknown kind, skipped"},
        ]})
    question = questions.ask("main", {
        "header": "Which train", "question": "Morning or afternoon train on Saturday?",
        "options": [{"label": "Morning", "description": "09:12, back by 18:00"}, {"label": "Afternoon", "description": "13:40, back late"}],
        "recommended": 0, "multi": False, "allow_text": True, "harmless": True})
    add("diablo", "assistant", "One thing before I book:", now - 1700, {"objects": [questions.public(question)]})

    add("workout", "user", "I did 20 push-ups", now - 7200)
    add("workout", "assistant", "Logged **20 push-ups**. That is 62 today, 38 to go.", now - 7190,
        {"objects": [{"kind": "progress", "label": "Push-ups today", "value": 62, "max": 100}]})
    add("home", "user", "Is anything left on?", now - 3600)
    add("home", "assistant", "The desk lamp and the shelf strip are on.", now - 3590,
        {"objects": [{"kind": "lamps", "title": "On now", "lamps": [l for l in lamps if l["on"]]}]})


@server.app.on_event("startup")
async def seeded():
    if not db.section_messages("diablo", 0, 1, server.MAIN_SOURCES):
        await asyncio.to_thread(seed)
    print(f"fake hub on http://{args.host}:{args.port}/app/?token={TOKEN}", flush=True)
    print(f"data in {data}", flush=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(server.app, host=args.host, port=args.port, log_level="warning")
