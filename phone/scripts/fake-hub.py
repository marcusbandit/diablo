#!/usr/bin/env python3
"""A fake Diablo hub for testing the phone app.

Runs the real hub/ code on a throwaway data dir with invented sections, messages and panels, and
a canned brain. Nothing reaches an LLM, the Claude CLI, Telegram, mail, Hue or the owner's data.

    fake-hub.py start [--port 8755] [--url http://10.0.2.2:8755] [--keep]
    fake-hub.py code                     mint a new pairing code and link
    fake-hub.py say [--in SECONDS] <section> <text>
    fake-hub.py status | stop | log
    fake-hub.py serve                    internal: what start runs in the background
"""

import argparse
import json
import os
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARKER = ".fake-hub"
DEFAULT_PORT = 8755


def state_dir():
    base = os.environ.get("DIABLO_FAKE_HUB_DIR")
    if base:
        return Path(base)
    return Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "diablo-fake-hub"


def state_file():
    return state_dir() / "state.json"


def read_state():
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return None


def write_state(state):
    path = state_file()
    path.write_text(json.dumps(state, indent=2))
    path.chmod(0o600)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def call(state, method, path, body=None, token=True, timeout=10):
    url = f"http://127.0.0.1:{state['port']}{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-Diablo-Token", state["token"])
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


def healthy(state):
    try:
        return bool(call(state, "GET", "/v1/health", token=False, timeout=2).get("ok"))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def hub_env(folder, port, url):
    """The child's environment: nothing DIABLO_* from this shell, every path inside folder."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DIABLO_", "TELEGRAM", "GITHUB_TOKEN", "GH_TOKEN", "ANTHROPIC", "OPENROUTER", "GROQ"))
           and k not in ("INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "PYTHONPATH")}
    nowhere = str(folder / "nowhere")
    env.update({
        "HOME": str(folder / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DIABLO_HUB_CONFIG": str(folder / "config.toml"),
        "DIABLO_HUB_DB": str(folder / "diablo.db"),
        "DIABLO_HUB_FILES": str(folder / "files"),
        "DIABLO_HUB_URL": url,
        "DIABLO_WEB": nowhere,
        "DIABLO_HOME": str(folder / "brain-home"),
        "DIABLO_BRAIN": str(folder / "vault"),
        "DIABLO_PERSONA_MD": nowhere,
        "DIABLO_T3_TOKENS": nowhere,
        "DIABLO_LOCAL_ENV": nowhere,
        "DIABLO_REMIND_BIN": nowhere,
        "DIABLO_HIMALAYA": nowhere,
        "DIABLO_CLAUDE": nowhere,
        "DIABLO_MAIL_DB": str(folder / "mail.db"),
        "DIABLO_FLEET_URL": "http://127.0.0.1:9",
        "DIABLO_TAILNET": "tailnet.example.ts.net",
        "DIABLO_HOME_SUBNET": "198.51.100.0/24",
        "DIABLO_MAIL_ACCOUNTS": "main=you@example.com",
        "DIABLO_MAIL_AGENT": "agent@example.com",
        "DIABLO_AUTOSHIP": "0",
        "DIABLO_MAIL": "0",
        "DIABLO_FAKE_HUB_DIR": str(folder),
        "DIABLO_FAKE_HUB_PORT": str(port),
    })
    return env


# ---------- commands that talk to a running fake hub (stdlib only) ----------

def cmd_start(args):
    folder = state_dir()
    old = read_state()
    if old and alive(old.get("pid")) and healthy(old):
        print(f"fake hub already running, pid {old['pid']}, port {old['port']}")
        return cmd_code(args)
    port = int(args.port or os.environ.get("DIABLO_FAKE_HUB_PORT") or DEFAULT_PORT)
    url = (args.url or f"http://10.0.2.2:{port}").rstrip("/")
    import socket
    with socket.socket() as probe:
        try:
            probe.bind((args.bind, port))
        except OSError:
            sys.exit(f"port {port} is taken on {args.bind}; pass --port")
    if folder.exists() and not args.keep:
        if not (folder / MARKER).exists():
            sys.exit(f"{folder} exists and is not a fake hub dir; set DIABLO_FAKE_HUB_DIR elsewhere")
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    (folder / MARKER).write_text("throwaway data for phone/scripts/fake-hub.py\n")
    for sub in ("home", "files", "brain-home", "vault"):
        (folder / sub).mkdir(exist_ok=True)
    token = (old or {}).get("token") if args.keep and old else None
    token = token or secrets.token_urlsafe(32)
    (folder / "config.toml").write_text(f'hub_token = "{token}"\nhub_url = "{url}"\ntelegram_bot_token = ""\n')
    (folder / "config.toml").chmod(0o600)
    log = open(folder / "hub.log", "ab")
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "serve", "--bind", args.bind, "--port", str(port)],
        env=hub_env(folder, port, url), cwd=str(folder), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        start_new_session=True,
    )
    state = {"pid": child.pid, "port": port, "bind": args.bind, "url": url, "token": token,
             "dir": str(folder), "started": time.time()}
    write_state(state)
    deadline = time.time() + 40
    while time.time() < deadline:
        if child.poll() is not None:
            sys.exit(f"fake hub exited with {child.returncode}; see {folder / 'hub.log'}")
        if healthy(state):
            break
        time.sleep(0.25)
    else:
        sys.exit(f"fake hub did not answer /v1/health in 40 s; see {folder / 'hub.log'}")
    print(f"fake hub up: pid {child.pid}, listening on {args.bind}:{port}")
    print(f"data dir:  {folder}")
    print(f"log:       {folder / 'hub.log'}")
    print(f"hub token: {token}")
    return cmd_code(args)


def running_state():
    state = read_state()
    if not state or not alive(state.get("pid")):
        sys.exit("fake hub is not running; start it with fake-hub.sh start")
    return state


def cmd_code(args):
    state = running_state()
    minted = call(state, "POST", "/v1/pair/code")
    code = minted["code"]
    plain = code.replace("-", "")
    local = "diablo://pair?" + urllib.parse.urlencode({"host": f"http://127.0.0.1:{state['port']}", "code": plain})
    print(f"pairing code: {code}  (good for {minted['ttl'] // 60} minutes, one device)")
    print(f"deep link:    {minted['link']}")
    print(f"adb reverse:  {local}")
    return 0


def cmd_say(args):
    if args.delay and args.delay > 0:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "say", args.section, args.text, "--after", str(args.delay)]
            + (["--objects", args.objects] if args.objects else []),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        print(f"will post to {args.section} in {args.delay:g} s")
        return 0
    if args.after:
        time.sleep(args.after)
    state = running_state()
    known = {s["id"] for s in call(state, "GET", "/v1/sections")["sections"] if not s.get("deleted")}
    if args.section not in known:
        sys.exit(f"no section {args.section}; known: {', '.join(sorted(known))}")
    meta = {"section": args.section, "backend": "fake"}
    if args.objects:
        meta["objects"] = json.loads(args.objects)
    out = call(state, "POST", "/v1/messages", {"source": "fleet", "role": "assistant", "text": args.text, "meta": meta})
    if not args.after:
        print(f"posted message {out['id']} to {args.section}")
    return 0


def cmd_task(args):
    state = running_state()
    fields = {"id": args.id, "state": args.state}
    if args.title:
        fields["title"] = args.title
    if args.step is not None:
        fields["step"] = args.step
    out = call(state, "POST", "/v1/tasks", fields)
    print(f"task {out['task']['id']} is {out['task']['state']}")
    return 0


def cmd_status(args):
    state = read_state()
    if not state or not alive(state.get("pid")):
        print("fake hub: stopped")
        return 1
    print(f"fake hub: {'up' if healthy(state) else 'not answering'}, pid {state['pid']}, port {state['port']}, url {state['url']}")
    print(f"data dir: {state['dir']}")
    return 0


def cmd_stop(args):
    state = read_state()
    if not state or not alive(state.get("pid")):
        print("fake hub: already stopped")
        return 0
    pid = int(state["pid"])
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 8
    while time.time() < deadline and alive(pid):
        time.sleep(0.2)
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
    if alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
    state["pid"] = None
    write_state(state)
    print("fake hub: stopped")
    return 0


def cmd_log(args):
    path = state_dir() / "hub.log"
    if not path.exists():
        sys.exit("no log yet")
    lines = path.read_text(errors="replace").splitlines()
    print("\n".join(lines[-args.lines:]))
    return 0


# ---------- the server itself ----------

def png(width, height, pixel):
    rows = b"".join(b"\x00" + bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def gradient(a, b, stripes=False):
    def pixel(x, y):
        t = x / 239
        band = 0.82 if stripes and (x + y) // 12 % 2 else 1.0
        return tuple(int((a[i] + (b[i] - a[i]) * t) * band) for i in range(3))
    return pixel


WARM, COOL, TEAL = (255, 77, 46), (45, 129, 255), (25, 179, 154)


def serve(args):
    folder = state_dir()
    if os.environ.get("DIABLO_HUB_CONFIG") != str(folder / "config.toml") or os.environ.get("HOME") != str(folder / "home"):
        sys.exit("serve is what start runs; use fake-hub.sh start")
    sys.path.insert(0, str(REPO))
    import asyncio
    import itertools

    import uvicorn
    from fastapi import Response

    from hub import brains, db, devices, events, files, lockguard, modules, questions, sections, server, settings
    from hub.modules.common import ActFailed

    app = server.app
    base_url = devices.public_url()
    devices.hub_name = lambda: "fake-hub"
    lockguard.for_turn = lambda origin, look=None: lockguard.Guard(origin=origin)

    # Fake modules: workout, today, lights. Real ones would read pushup files, Hue or mail.
    work = {"done": 45, "target": 100, "streak": 6, "sets": 4, "minutes": 35, "week": 310, "week_goal": 500}
    lamps = [
        {"id": "1", "name": "Desk lamp", "on": True, "level": 70, "color": "#ffd8a8", "ct": 2700,
         "caps": {"color": True, "ct": [2000, 6500]}, "reachable": True},
        {"id": "2", "name": "Ceiling", "on": False, "level": 0, "color": None, "ct": 4000,
         "caps": {"color": False, "ct": [2200, 6500]}, "reachable": True},
        {"id": "3", "name": "Hallway strip", "on": True, "level": 35, "color": "#8b67f2", "ct": None,
         "caps": {"color": True, "ct": None}, "reachable": True},
        {"id": "4", "name": "Balcony", "on": False, "level": 0, "color": None, "ct": None,
         "caps": {"color": False, "ct": None}, "reachable": False},
    ]

    def note_in(section_id, text):
        db.add_message("fleet", "assistant", text, {"section": section_id, "backend": "fake"})

    def workout_panel(section):
        now = time.time()
        left = max(0, work["target"] - work["done"])
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return [
            {"kind": "goal", "title": "Push-ups today", "icon": "dumbbell", "value": work["done"], "max": work["target"],
             "caption": f"{left} to go, about {max(1, left // 15)} sets" if left else "Done for today",
             "updated": {"age": "just now", "at": now, "every": 900}, "stale": False,
             "badges": [{"icon": "flame", "label": f"{work['streak']} day streak"},
                        {"icon": "done", "label": f"{work['sets']} sets today"},
                        {"icon": "calendar", "label": "Day 12 of 30"}]},
            {"kind": "actions", "items": [
                {"label": "Log 10 push-ups", "icon": "add", "act": {"module": "workout", "action": "log", "args": {"reps": 10}}},
                {"label": "Log 25 push-ups", "icon": "add", "act": {"module": "workout", "action": "log", "args": {"reps": 25}}},
                {"label": "I trained today", "icon": "run", "say": "I trained today: ", "send": False},
                {"label": "How am I doing?", "icon": "insights", "say": "How is my training going this week?"},
                {"label": "Reset today", "icon": "off", "act": {"module": "workout", "action": "reset", "args": {}}},
            ]},
            {"kind": "stats", "items": [
                {"label": "Sets today", "value": str(work["sets"]), "icon": "done"},
                {"label": "Streak", "value": str(work["streak"]), "unit": "days", "icon": "flame"},
                {"label": "Trained", "value": str(work["minutes"]), "unit": "min", "icon": "run"},
            ]},
            {"kind": "progress", "label": "Push-ups this week", "value": work["week"], "max": work["week_goal"],
             "caption": f"{work['week_goal'] - work['week']} left by Sunday", "icon": "trend"},
            {"kind": "bars", "title": "Sets a day", "caption": "Last week", "icon": "trend", "labels": days,
             "unit": "set", "stacked": True, "series": [
                 {"name": "push", "tone": "accent", "values": [4, 5, 0, 6, 3, 2, work["sets"]]},
                 {"name": "legs", "tone": "warn", "values": [2, 0, 3, 1, 0, 4, 1]},
                 {"name": "core", "tone": "ink", "values": [1, 2, 1, 0, 2, 1, 0]}]},
            {"kind": "list", "link": "days", "title": "This week", "icon": "calendar", "items": [
                {"title": "Today", "detail": f"{work['done']} push-ups; 20 min run", "trailing": f"{work['sets']} sets"},
                {"title": "Yesterday", "detail": "Bouldering, 60 min, hard", "trailing": "3 sets"},
                {"title": "Saturday", "detail": "Rest day", "trailing": "0 sets"},
            ]},
        ]

    def workout_act(action, args_in):
        if action == "log":
            reps = int(args_in.get("reps") or 0)
            if not 1 <= reps <= 1000:
                raise ValueError("reps must be 1 to 1000")
            work["done"] += reps
            work["sets"] += 1
            work["week"] += reps
            text = f"Logged {reps} push-ups. That is **{work['done']} of {work['target']}** today."
            note_in("workout", text)
            return text.replace("**", "")
        if action == "reset":
            work.update(done=0, sets=0)
            return "Today is back to zero."
        if action == "fail":
            raise ActFailed("the fake log refused that on purpose")
        raise ValueError(f"unknown action {action}")

    def lights_panel(section):
        on = sum(1 for lamp in lamps if lamp["on"])
        return [
            {"kind": "lamps", "title": "Living room", "lamps": [dict(lamp) for lamp in lamps]},
            {"kind": "scenes", "title": "Scenes", "items": [
                {"id": "s1", "name": "Evening", "colors": ["#ffb36b", "#ff7a45"]},
                {"id": "s2", "name": "Focus", "colors": ["#e8f0ff", "#9cc0ff"]},
                {"id": "s3", "name": "Movie", "colors": ["#8b67f2", "#2d1f5c"]}]},
            {"kind": "stats", "items": [{"label": "Lamps on", "value": str(on), "unit": f"/ {len(lamps)}", "icon": "lightbulb"}]},
            {"kind": "notice", "title": "Balcony", "text": "The balcony lamp is not answering the bridge.", "tone": "fail", "retry": True},
        ]

    def lights_act(action, args_in):
        pick = str(args_in.get("lamp") or "all")
        chosen = [lamp for lamp in lamps if pick in ("all", lamp["id"]) and lamp["reachable"]]
        if not chosen:
            raise ActFailed(f"lamp {pick} is unreachable")
        if action == "toggle":
            for lamp in chosen:
                lamp["on"] = not lamp["on"]
                lamp["level"] = lamp["level"] or 60
            return "Switched " + ", ".join(lamp["name"] for lamp in chosen) + "."
        if action == "level":
            level = int(args_in.get("level", -1))
            if not 0 <= level <= 100:
                raise ValueError("level must be 0 to 100")
            for lamp in chosen:
                lamp["level"], lamp["on"] = level, level > 0
            return f"Set to {level}%."
        if action == "scene":
            for lamp in lamps:
                if lamp["reachable"]:
                    lamp["on"], lamp["level"] = True, 55
            return "Scene on."
        if action in ("color", "temperature"):
            return "Changed."
        raise ValueError(f"unknown action {action}")

    def today_panel(section):
        on = sum(1 for lamp in lamps if lamp["on"])
        return [{"kind": "stats", "items": [
            {"label": "Push-ups", "value": str(work["done"]), "unit": f"/ {work['target']}", "icon": "dumbbell", "open": "workout"},
            {"label": "Lamps on", "value": str(on), "icon": "lightbulb", "open": "lights"},
            {"label": "Reminders", "value": "2", "icon": "alarm"},
        ]}]

    def fake(ident, title, panel, act=None):
        from types import SimpleNamespace
        fields = dict(ID=ident, TITLE=title, TOOLS=[], WRITES=(), CACHES=(), panel=panel,
                      call=lambda name, arguments: "fake", summary=lambda: None)
        if act:
            fields["act"] = act
        return SimpleNamespace(**fields)

    fakes = (fake("today", "Today", today_panel), fake("workout", "Workout", workout_panel, workout_act),
             fake("lights", "Lights", lights_panel, lights_act))
    modules.ORDER = fakes
    modules.BY_ID.clear()
    modules.BY_ID.update({m.ID: m for m in fakes})

    # Fake brain: replaces brains._ask, so brains.ask still runs its busy bookkeeping and run_chat
    # publishes thinking, doing and done exactly as with the real one.
    counter = itertools.count(1)

    def section_of(origin):
        marker = ", section "
        return str(origin or "").split(marker, 1)[1].strip() if marker in str(origin or "") else "Diablo"

    async def fake_ask(conv, text, origin="telegram", images=None, on_step=None, notes=None, machine=False,
                       message_id=None, thread_hint=None, desktop=None, on_text=None):
        n = next(counter)
        lower = text.lower()
        await asyncio.sleep(0.5)
        step = ("brain_search: " + text[:40]) if n % 2 else "WebSearch: " + text[:40]
        if text.startswith("[answer to question"):
            step = "save_fact: your answer"
        elif "light" in lower or "lamp" in lower:
            step = "hue_light: desk lamp"
        elif "push" in lower or "train" in lower or "workout" in lower:
            step = "workout_stats"
        if on_step:
            await on_step(step)
        await asyncio.sleep(1.0)
        if "slow" in lower:
            await asyncio.sleep(6)
        if "fail" in lower:
            return brains.Turn(error="the fake brain was asked to fail", backend="fake", steps=[step])
        reply, objects = canned(conv, text, n, section_of(origin))
        if objects and any(o.get("module") == "workout" for o in objects):
            events.publish({"kind": "panel", "modules": ["workout", "today"]})
        return brains.Turn(text=reply, objects=objects, backend="fake", model="fake-brain", steps=[step])

    brains._ask = fake_ask

    def canned(conv, text, n, where):
        lower = text.lower()
        quoted = " ".join(text.split())[:200]
        if text.startswith("[answer to question"):
            label = text.split(":", 1)[1].split("]", 1)[0].strip() if ":" in text else "your own words"
            return (f"Going with **{label}**. I moved it and told the others.\n\n"
                    "- Calendar updated\n- Invite sent to you@example.com\n\n> Done. Nothing else needs you."), [
                {"kind": "notice", "text": f"Picked: {label}", "tone": "ok"}]
        if "question" in lower or "ask me" in lower:
            q = questions.ask(conv, {
                "header": "Pick one", "question": "Which do you want first?", "harmless": True,
                "options": [{"label": "The report", "description": "Ten minutes"},
                            {"label": "The slides", "description": "Half an hour"},
                            {"label": "Neither", "description": "Leave both for tomorrow"}],
                "recommended": 1})
            return "Quick one before I start:", [questions.public(q)]
        if "table" in lower or "compare" in lower:
            return ("## Side by side\n\n| Plan | Price | Speed |\n| --- | ---: | --- |\n| Basic | 9 | slow |\n"
                    "| Plus | 19 | fast |\n| Max | 39 | *very* fast |\n\nPlus is the sweet spot."), [
                {"kind": "table", "title": "Plans", "head": ["Plan", "Price", "Speed"],
                 "rows": [["Basic", "9", "slow"], ["Plus", "19", "fast"], ["Max", "39", "very fast"]]}]
        if "code" in lower or "script" in lower:
            return ("Here it is. The long line is on purpose:\n\n```python\n"
                    "def greet(name):\n    return f\"Hello, {name}! This line is deliberately long so the app has to scroll or wrap it sideways without breaking the layout.\"\n"
                    "```\n\nRun it with `python greet.py`."), [
                {"kind": "code", "title": "greet.py", "lang": "python", "code": "def greet(name):\n    return f\"Hello, {name}\"\n"},
                {"kind": "diff", "title": "greet.py", "diff": "--- a/greet.py\n+++ b/greet.py\n@@ -1,2 +1,2 @@\n def greet(name):\n-    return 'hi'\n+    return f\"Hello, {name}\"\n"}]
        if "page" in lower or "html" in lower:
            return "I made a small page for it.", [html_block("Your week")]
        if "image" in lower or "picture" in lower:
            return "Here is a picture.", [image_file_block(), image_url_block()]
        if "push" in lower or "train" in lower or "workout" in lower:
            return (f"You are on **{work['done']} of {work['target']}** push-ups today and a *{work['streak']} day* streak.\n\n"
                    "1. One more set of 15 before dinner\n2. Stretch for five minutes\n3. Rest tomorrow"), [
                {"kind": "goal", "title": "Push-ups today", "icon": "dumbbell", "value": work["done"], "max": work["target"],
                 "badges": [{"icon": "flame", "label": f"{work['streak']} day streak"}], "module": "workout"},
                {"kind": "stats", "items": [{"label": "Sets", "value": str(work["sets"]), "icon": "done"},
                                            {"label": "Week", "value": str(work["week"]), "unit": "reps", "icon": "trend"}]}]
        if "light" in lower or "lamp" in lower:
            lamps[0]["on"] = not lamps[0]["on"]
            events.publish({"kind": "panel", "modules": ["lights", "today"]})
            return f"The desk lamp is **{'on' if lamps[0]['on'] else 'off'}** now.", [
                {"kind": "readout", "title": "Desk lamp", "cells": [
                    {"v": "on" if lamps[0]["on"] else "off", "k": "state"}, {"v": f"{lamps[0]['level']}%", "k": "level"}]}]
        variants = [
            (f"> {quoted}\n\nSure. Here is what I would do:\n\n- Start with the smallest piece\n- Check it on the phone\n"
             f"- Then the rest\n\nMore at [example.com](https://example.com/notes)."),
            (f"### About \"{quoted[:60]}\"\n\n- [x] Read it\n- [ ] Think it over\n- [ ] Answer properly\n\n"
             "_This is the fake hub, so the answer is canned._"),
            f"You said: **{quoted}**. In the {where} section that sounds like a job for tomorrow morning.",
        ]
        objects = [{"kind": "list", "title": "Next", "items": [{"title": "Look at it again", "trailing": "later"}]}] if n % 3 == 0 else []
        return variants[n % len(variants)], objects

    def html_block(title):
        page = ("<!doctype html><html><body style=\"margin:0;font-family:var(--font-sans,sans-serif);"
                "background:var(--raised,#fff);color:var(--ink,#111)\"><div style=\"padding:16px\">"
                f"<h2 style=\"margin:0 0 8px\">{title}</h2><p style=\"color:var(--muted,#666)\">Five of seven days done.</p>"
                "<div style=\"display:flex;gap:6px;align-items:flex-end;height:80px\">"
                + "".join(f"<div style=\"flex:1;height:{h}%;background:var(--accent,#2d81ff);border-radius:4px\"></div>"
                          for h in (40, 70, 55, 90, 30, 10, 10))
                + "</div></div></body></html>")
        return {"kind": "html", "title": title, "html": page, "height": 180}

    image_bytes = png(240, 135, gradient(WARM, COOL, stripes=True))

    def image_file_block():
        stored = settings.get("fake.image_file")
        record = files.get(stored) if stored else None
        if record is None:
            record = files.save(png(240, 135, gradient(TEAL, COOL)), "sunset-test.png", "image/png", "fake-hub")
            settings.set_setting("fake.image_file", record["id"])
        return {"kind": "image", "title": "Test pattern", "caption": "A file in the hub's file store",
                "file": record["id"], **files.public(record)}

    def image_url_block():
        return {"kind": "image", "title": "Stripes", "caption": "Served by url",
                "url": f"{base_url}/fake/pattern.png"}

    def pattern():
        return Response(image_bytes, media_type="image/png")

    app.add_api_route("/fake/pattern.png", pattern, methods=["GET"], include_in_schema=False)

    def seed():
        if settings.get("fake.seeded"):
            return
        settings.set_setting("questions.ttl_s", str(14 * 24 * 3600))
        now = time.time()
        stamp = now
        kept = [
            {"id": "diablo", "name": "Diablo", "icon": "flame", "accent": "#ff4d2e", "brief": "", "position": 0, "panels": ["today"]},
            {"id": "workout", "name": "Workout", "icon": "dumbbell", "accent": "#19b39a", "position": 1, "panels": ["workout"],
             "brief": "Training: sets, reps and sessions get logged here; questions about progress too."},
            {"id": "home", "name": "Home", "icon": "home", "accent": "#2d81ff", "position": 2, "panels": ["lights"],
             "brief": "The flat in Sometown: lamps, reminders and the network."},
            {"id": "projects", "name": "Projects", "icon": "folder", "accent": "#8b67f2", "position": 3, "panels": [],
             "brief": "Side projects, mostly the bakery site for Alex Example."},
        ]
        rows = [dict(s, hidden=False, deleted=False, updated=stamp) for s in kept]
        rows += [dict(s, hidden=False, deleted=True, updated=stamp) for s in sections.SEEDS if s["id"] not in {k["id"] for k in kept}]
        settings.set_setting(sections.KEY, json.dumps(rows))

        # recommended is 1-based, as ask_user takes it
        q = questions.ask(brains.MAIN, {
            "header": "Team sync", "question": "Where should the Thursday team sync go?", "harmless": True,
            "options": [{"label": "Keep Thursday 10:00", "description": "Nothing moves"},
                        {"label": "Move to Friday 09:00", "description": "Alex Example is free then"},
                        {"label": "Cancel this week", "description": "Send a short update instead"}],
            "recommended": 2})

        hours = 3600
        convo = {
            "diablo": [
                (70, "phone", "user", "Morning. What's on today?", None),
                (70, "phone", "assistant",
                 "## Today\n\nThree things, nothing urgent:\n\n- **10:00** call with Alex Example\n- Pick up the parcel in Sometown\n"
                 "- Gym after work, *if* the knee is fine\n\n### Still open\n\n- [x] Reply to the landlord\n- [ ] Book the dentist\n"
                 "- [ ] Send the invoice to you@example.com\n\nThe weather is at [example.com/weather](https://example.com/weather).",
                 [{"kind": "readout", "title": "Right now", "cells": [{"v": "14°", "k": "outside"}, {"v": "3", "k": "meetings"},
                                                                       {"v": "12", "k": "unread"}]},
                  {"kind": "list", "title": "Calendar", "icon": "calendar", "items": [
                      {"title": "Call with Alex Example", "detail": "Video, 30 min", "trailing": "10:00"},
                      {"title": "Parcel pickup", "detail": "Example Street 12, Sometown", "trailing": "15:00"},
                      {"title": "Gym", "trailing": "18:30"}]}]),
                (50, "fleet", "user", "Can you compare the two laptop offers Alex sent?", None),
                (50, "fleet", "assistant",
                 "Here is the short version.\n\n| | Model A | Model B |\n| --- | --- | --- |\n| Price | 1 099 | 1 349 |\n"
                 "| Battery | 11 h | 16 h |\n| Weight | 1.6 kg | 1.2 kg |\n| Ports | 3 | 2 |\n\n"
                 "> Model B is lighter and lasts longer, but you lose a port.\n\nI would take **B** unless you need the HDMI port.",
                 [{"kind": "table", "title": "Laptops", "head": ["", "Model A", "Model B"],
                   "rows": [["Price", "1 099", "1 349"], ["Battery", "11 h", "16 h"], ["Weight", "1.6 kg", "1.2 kg"]]}]),
                (26, "fleet", "user", "Show me the deploy script that failed last night", None),
                (26, "fleet", "assistant",
                 "It failed on the upload step. The fix is one flag:\n\n```bash\n#!/usr/bin/env bash\nset -euo pipefail\n"
                 "rsync -az --delete --exclude node_modules --exclude .git ./dist/ deploy@198.51.100.7:/srv/www/example-site/ --rsync-path='mkdir -p /srv/www/example-site && rsync'\n"
                 "```\n\nRun it again with `./deploy.sh --dry-run` first.",
                 [{"kind": "code", "title": "deploy.sh", "lang": "bash",
                   "code": "#!/usr/bin/env bash\nset -euo pipefail\nrsync -az --delete ./dist/ deploy@198.51.100.7:/srv/www/example-site/\n"},
                  {"kind": "diff", "title": "deploy.sh",
                   "diff": "--- a/deploy.sh\n+++ b/deploy.sh\n@@ -1,3 +1,3 @@\n #!/usr/bin/env bash\n set -euo pipefail\n"
                           "-rsync -az ./dist/ deploy@198.51.100.7:/srv/www/\n+rsync -az --delete ./dist/ deploy@198.51.100.7:/srv/www/example-site/\n"}]),
                (5, "phone", "user", "Should I move the team sync?", None),
                (5, "phone", "assistant", "Friday looks better, but it is your call:",
                 [questions.public(q), {"kind": "notice", "text": "Two people declined Thursday already."}]),
                (1, "phone", "user", "Make me a little status page for the week, with a picture", None),
                (1, "phone", "assistant", "Here you go. The page is live, the pictures are a test pattern.",
                 [html_block("This week"), image_file_block(), image_url_block()]),
            ],
            "workout": [
                (60, "phone", "user", "Did 30 push-ups and a 20 minute run", None),
                (60, "phone", "assistant", "Logged both. **30 push-ups** and a *20 min* run, normal effort.",
                 [{"kind": "stats", "items": [{"label": "Push-ups", "value": "30", "icon": "dumbbell"},
                                              {"label": "Run", "value": "20", "unit": "min", "icon": "run"}]},
                  {"kind": "progress", "label": "Week goal", "value": 230, "max": 500, "icon": "trend"}]),
                (30, "phone", "user", "Plan my week", None),
                (30, "phone", "assistant",
                 "## The week\n\n1. **Mon**: push-ups, 4 sets\n2. **Tue**: bouldering\n3. **Wed**: rest\n4. **Thu**: legs\n"
                 "5. **Fri**: push-ups and core\n\n- [ ] Buy new chalk\n- [x] Book the wall for Tuesday",
                 [{"kind": "list", "title": "Plan", "icon": "calendar", "items": [
                     {"title": "Monday", "detail": "Push-ups, 4 sets", "trailing": "20 min"},
                     {"title": "Tuesday", "detail": "Bouldering", "trailing": "60 min"},
                     {"title": "Wednesday", "detail": "Rest day"}]},
                  {"kind": "actions", "items": [{"label": "Start Monday", "icon": "run", "say": "Start the Monday plan"},
                                               {"label": "Change it", "icon": "add", "say": "Change the plan: ", "send": False}]}]),
                (3, "phone", "user", "How am I doing?", None),
                (3, "phone", "assistant", "Better than last week. You are *ahead* of pace and the streak holds.",
                 [{"kind": "goal", "title": "Push-ups today", "icon": "dumbbell", "value": 45, "max": 100,
                   "caption": "55 to go", "badges": [{"icon": "flame", "label": "6 day streak"}]}]),
            ],
            "home": [
                (40, "phone", "user", "Turn the desk lamp down at 22:00 every night", None),
                (40, "phone", "assistant", "Done. Every night at **22:00** the desk lamp goes to 20%.",
                 [{"kind": "notice", "text": "Repeats daily. Cancel it from the reminders card.", "tone": "ok"},
                  {"kind": "actions", "items": [{"label": "Dim it now", "icon": "dim",
                                                 "act": {"module": "lights", "action": "level", "args": {"lamp": "1", "level": 20}}}]}]),
                (8, "fleet", "user", "Is the network okay? The TV keeps buffering", None),
                (8, "fleet", "assistant",
                 "The line is fine; the TV is on the far access point.\n\n| Device | Address | Signal |\n| --- | --- | --- |\n"
                 "| TV | 198.51.100.23 | weak |\n| Laptop | 198.51.100.41 | good |\n| Phone | 198.51.100.52 | good |\n\n"
                 "Try moving it to the 5 GHz network.",
                 [{"kind": "readout", "title": "Network", "cells": [{"v": "480", "k": "Mbit/s down"}, {"v": "12 ms", "k": "ping"},
                                                                     {"v": "9", "k": "devices"}]}]),
            ],
            "projects": [
                (72, "fleet", "user", "What's left on the Sometown bakery site?", None),
                (72, "fleet", "assistant",
                 "## Bakery site\n\nNearly there.\n\n- [x] Menu page\n- [x] Opening hours\n- [ ] Contact form\n- [ ] Photos from Alex\n\n"
                 "Next steps:\n\n1. Wire the form to `hello@example.com`\n2. Compress the photos\n3. Ship",
                 [{"kind": "progress", "label": "Bakery site", "value": 6, "max": 9, "caption": "3 tasks left", "icon": "done"},
                  {"kind": "list", "title": "Left", "items": [{"title": "Contact form", "trailing": "1 h"},
                                                              {"title": "Photos", "detail": "Waiting on Alex Example", "trailing": "?"}]}]),
                (20, "fleet", "user", "Show the diff for the menu fix", None),
                (20, "fleet", "assistant", "Two lines changed in `menu.html`:",
                 [{"kind": "diff", "title": "menu.html",
                   "diff": "--- a/menu.html\n+++ b/menu.html\n@@ -10,3 +10,3 @@\n <li>Croissant</li>\n-<li>Sourdough 4.00</li>\n+<li>Sourdough 4.50</li>\n <li>Rye</li>\n"}]),
                (2, "phone", "user", "Ship it?", None),
                (2, "phone", "assistant", "Checks pass. Say the word and it goes live on **example.com**.",
                 [{"kind": "actions", "items": [{"label": "Ship it", "icon": "done", "say": "Ship the bakery site"},
                                               {"label": "Not yet", "icon": "clock", "say": "Hold the bakery site for now"}]},
                  {"kind": "notice", "text": "The contact form is still a placeholder.", "tone": "warn"}]),
            ],
        }
        for section_id, lines in convo.items():
            for i, (ago, source, role, text, objects) in enumerate(lines):
                meta = {"section": section_id, "client_id": f"seed-{section_id}-{i // 2}"}
                if role == "assistant":
                    meta.update(objects=objects or [], backend="fake")
                message_id = db.add_message(source, role, text, meta)
                ts = now - ago * hours + (60 if role == "assistant" else 0)
                with db._lock:
                    db._conn.execute("UPDATE messages SET ts = ? WHERE id = ?", (ts, message_id))
                    db._conn.commit()
        from hub import tasks
        for ago, fields in (
            (0.1, {"id": "task-fleet-doctor", "title": "Fleet doctor on every box", "kind": "partner", "state": "working",
                   "step": "Checking the build box", "machine": "box-two", "source": "t3"}),
            (0.2, {"id": "task-den", "title": "The phone opens on the den", "kind": "self_improvement", "state": "waiting",
                   "step": "Waiting on your look at the screenshots", "source": "t3",
                   "links": {"pr": "https://example.com/pull/82"}}),
            (0.05, {"id": "task-backup", "title": "Back up the hub database", "kind": "self_improvement", "state": "working",
                    "step": "Copying 41 MB", "source": "tool"}),
            (1.2, {"id": "task-ship", "title": "Ship the hub", "kind": "self_improvement", "state": "done",
                   "step": "Live", "result": "Live on the hub, tests green", "source": "t3"}),
        ):
            tasks.save(fields.pop("id"), fields, now=now - ago * hours)
        settings.set_setting("fake.seeded", "1")

    async def startup():
        db.init(server.DB_PATH)
        events.bind(asyncio.get_running_loop())
        db.listeners.append(lambda mid, source, meta: events.message_added(mid, source, meta, server.MAIN_SOURCES))
        db.listeners.append(questions.link)
        seed()
        asyncio.create_task(server.sweep_questions())

    # Swap the real startup (Telegram, T3 watchers, autoship, mail, skills) for ours.
    app.router.on_startup[:] = [startup]
    app.router.on_shutdown[:] = []
    uvicorn.run(app, host=args.bind, port=int(args.port), log_level="info")


def main():
    parser = argparse.ArgumentParser(prog="fake-hub", description="A fake Diablo hub for the phone app.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start", help="start in the background with fresh invented data")
    p.add_argument("--port", type=int)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--url", help="the hub url the app uses, default http://10.0.2.2:<port>")
    p.add_argument("--keep", action="store_true", help="keep the data from the last run")
    p.set_defaults(fn=cmd_start)
    p = sub.add_parser("serve", help="run in the foreground (start uses this)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--bind", default="0.0.0.0")
    p.set_defaults(fn=serve)
    sub.add_parser("code", help="mint a new pairing code").set_defaults(fn=cmd_code)
    p = sub.add_parser("say", help="post an assistant message to a section")
    p.add_argument("section")
    p.add_argument("text")
    p.add_argument("--in", dest="delay", type=float, default=0, help="post after this many seconds, detached")
    p.add_argument("--objects", help="JSON list of blocks for meta.objects")
    p.add_argument("--after", type=float, default=0, help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_say)
    p = sub.add_parser("task", help="create or move a task card, e.g. task task-fleet-doctor done")
    p.add_argument("id")
    p.add_argument("state", choices=["queued", "working", "waiting", "done", "failed"])
    p.add_argument("--title")
    p.add_argument("--step")
    p.set_defaults(fn=cmd_task)
    sub.add_parser("status", help="is it running").set_defaults(fn=cmd_status)
    sub.add_parser("stop", help="stop it").set_defaults(fn=cmd_stop)
    p = sub.add_parser("log", help="tail the hub log")
    p.add_argument("-n", dest="lines", type=int, default=40)
    p.set_defaults(fn=cmd_log)
    args = parser.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
