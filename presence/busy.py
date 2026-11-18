"""The hub's running turns, one JSON line per change, for the orb beside the cursor.

shell.qml runs this and reads its stdout. It opens /v1/events, asks GET /v1/busy, and prints
every `busy` event as {"turns": [...]}, asking /v1/busy again every half minute so a missed end
can not hold the orb up; a keepalive from the hub prints {"alive": true},
so the orb can tell a quiet hub from a lost one. When the hub cannot be reached it prints
{"turns": [], "tasks": [], "lost": true} and tries again, so a dropped link never leaves the orb busy.

The live task cards ride along the same way: GET /v1/tasks at the start and with every recheck,
then each `task` event, printed as {"tasks": [{"id", "state", "title"}]} with only the active ones
(queued, working, waiting), so the orb can carry one moon for each.

Before each connection it prints {"hub": {"url", "token"}} from config.toml, so History in the
central card reads the hub with the same pair.

Standard library only: the system python runs it, no venv.

It also carries what the brain shows on screen (`screen` events from POST /v1/screen): a small
card goes into presence.json beside the ears' own cards, a large one opens the central card
through panel.json, and an HTML page is saved under pages/ for the web view to load. An `open`
event (POST /v1/screen/open) opens the central card on Settings at its page. A `marks` event
(POST /v1/screen/marks) is one beat of marks over the real screen, or a clear, kept in marks.json
for the overlay.

A `duck` event (POST /v1/ears/duck, the brain's keep_sound_up) goes to the ears on this machine
at 127.0.0.1:8758, which keep other sound up for the rest of the conversation or turn it down again.
"""

import json
import os
import sys
import threading
import time
import tomllib
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")) / "diablo" / "config.toml"
STATE = Path(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")) / "diablo"
KEEP_CARDS = 3
KEEP_PAGES = 20
PING_S = 10
RETRY_S = 3
RECHECK_S = 30
EARS_URL = "http://127.0.0.1:8758"


def config():
    cfg = {"hub_url": "http://diablo-pi:8755", "hub_token": ""}
    try:
        with open(CONFIG, "rb") as handle:
            data = tomllib.load(handle)
        cfg.update({key: data[key] for key in cfg if key in data})
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return cfg


LOCK = threading.Lock()


def say(body):
    with LOCK:
        sys.stdout.write(json.dumps(body) + "\n")
        sys.stdout.flush()


def shown(event):
    return {"turns": [{"words": turn.get("words") or "thinking", "origin": turn.get("origin", ""), "since": turn.get("since", 0)}
                      for turn in event.get("turns") or [] if isinstance(turn, dict)]}


ACTIVE = ("queued", "working", "waiting")
TASKS = {}
TASKS_LOCK = threading.Lock()
# Bumped by every task event, so a listing asked for before one never undoes it.
TASK_EVENTS = [0]
LAST_TASKS = [None]


def entry(task):
    """What the orb keeps of one active task, or None for a finished or malformed one."""
    if not isinstance(task, dict) or not task.get("id") or task.get("state") not in ACTIVE:
        return None
    return {"id": str(task["id"]), "state": task["state"],
            "title": " ".join(str(task.get("title") or "").split())[:60],
            "started_at": number(task.get("started_at"), 0.0)}


def tasks_line():
    with TASKS_LOCK:
        rows = sorted(TASKS.values(), key=lambda t: (t["started_at"], t["id"]))
    return {"tasks": [{k: v for k, v in row.items() if k != "started_at"} for row in rows]}


def say_tasks():
    """Prints the tasks line, only when it differs from the last one printed."""
    line = tasks_line()
    with TASKS_LOCK:
        if line == LAST_TASKS[0]:
            return
        LAST_TASKS[0] = line
    say(line)


def track(task):
    """One task event into the set of active ones. True when the set changed."""
    if not isinstance(task, dict) or not task.get("id"):
        return False
    TASK_EVENTS[0] += 1
    kept = entry(task)
    if kept is None:
        return TASKS.pop(str(task["id"]), None) is not None
    changed = TASKS.get(kept["id"]) != kept
    TASKS[kept["id"]] = kept
    return changed


def replace_tasks(listing, events_before=None):
    """The whole set from a listing, unless a task event came in while it was being fetched."""
    with TASKS_LOCK:
        if events_before is not None and events_before != TASK_EVENTS[0]:
            return False
        TASKS.clear()
        for kept in filter(None, map(entry, listing)):
            TASKS[kept["id"]] = kept
        return True


def write_json(path, body):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body))
    tmp.replace(path)


def read_json(path, fallback):
    try:
        body = json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback
    return body if isinstance(body, dict) else fallback


def save_page(card_id, html):
    """The page as a file the web view loads; only the newest KEEP_PAGES stay. The name is the
    card id kept to letters and digits, so nothing from the stream picks the path."""
    pages = STATE / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    name = "".join(ch for ch in str(card_id) if ch.isalnum())[:32] or "page"
    path = pages / f"{name}.html"
    path.write_text(html)
    old = sorted(pages.glob("*.html"), key=lambda p: p.stat().st_mtime)[:-KEEP_PAGES]
    for stale in old:
        stale.unlink(missing_ok=True)
    return str(path)


def number(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def locked():
    """The same lock voice/presence.py takes, so the ears and this never write over each other."""
    import fcntl

    STATE.mkdir(parents=True, exist_ok=True)
    handle = open(STATE / "presence.lock", "a")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


PAGES = ("settings", "voice", "history")
MARKS_CPS = 15.0


def marks_cps():
    """How many characters a second he speaks, for how long a beat stays: marks_cps in config.toml."""
    try:
        with open(CONFIG, "rb") as handle:
            value = number(tomllib.load(handle).get("marks_cps"), MARKS_CPS)
    except (OSError, tomllib.TOMLDecodeError):
        return MARKS_CPS
    return value if 3 <= value <= 60 else MARKS_CPS


def mark(beat, now=None):
    """One beat into marks.json: a beat of the show already there joins it, a new show replaces
    it, and clear takes everything down. Returns what happened."""
    now = time.time() if now is None else now
    if not isinstance(beat, dict):
        return None
    if beat.get("clear"):
        write_json(STATE / "marks.json", {"show": "", "beats": [], "count": 0, "at": now, "cleared": True})
        return "cleared"
    show = str(beat.get("show") or "")
    if not show or not isinstance(beat.get("n"), int):
        return None
    body = read_json(STATE / "marks.json", {})
    kept = {k: beat[k] for k in ("n", "say", "marks", "wait", "caption", "lazy") if k in beat}
    kept["at"] = now
    if body.get("show") != show:
        body = {"show": show, "beats": [], "at": now}
    beats = [b for b in body.get("beats") or [] if isinstance(b, dict) and b.get("n") != kept["n"]]
    beats.append(kept)
    write_json(STATE / "marks.json", {"show": show, "at": body.get("at", now), "count": int(beat.get("count") or len(beats)),
                                      "cps": marks_cps(), "beats": sorted(beats, key=lambda b: b["n"])})
    return "marked"


def show(event, now=None):
    """A `screen` event onto the desktop: small into presence.json, large into panel.json, an
    `open` one into panel.json as the Settings page it names, or the History tab."""
    now = time.time() if now is None else now
    if event.get("marks") is not None:
        return mark(event.get("marks"), now)
    if event.get("open") is not None:
        page = event.get("open")
        if page not in PAGES:
            return None
        panel = read_json(STATE / "panel.json", {})
        if page == "history":
            write_json(STATE / "panel.json", {**panel, "open": True, "view": "history", "page": "", "at": now})
        else:
            write_json(STATE / "panel.json", {**panel, "open": True, "view": "settings", "page": page, "page_at": now, "at": now})
        return page
    card = dict(event.get("card") or {})
    if not card.get("id"):
        return None
    html = card.pop("html", "") or ""
    if html:
        card["page"] = save_page(str(card["id"]), html)
    card["at"] = now
    if event.get("size") == "large" or card.get("sections"):
        panel = read_json(STATE / "panel.json", {})
        write_json(STATE / "panel.json", {**panel, "open": True, "view": "now", "card": card, "page": "", "at": now})
        return "large"
    with locked():
        body = read_json(STATE / "presence.json", {"cards": []})
        cards = [c for c in body.get("cards") or []
                 if isinstance(c, dict) and now < number(c.get("at"), now) + number(c.get("ttl"), 10)]
        write_json(STATE / "presence.json", {**body, "cards": (cards + [card])[-KEEP_CARDS:]})
    return "small"


def duck(event, opener=urllib.request.urlopen):
    """A `duck` event to the ears' control panel. True when the ears took it."""
    body = json.dumps({"keep_up": bool(event.get("keep_up", True))}).encode()
    request = urllib.request.Request(f"{EARS_URL}/v1/ears/duck", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Diablo-Ears": "1"})
    with opener(request, timeout=5) as reply:
        return reply.status == 200


def ask(base, headers):
    with urllib.request.urlopen(urllib.request.Request(f"{base}/v1/busy", headers=headers), timeout=10) as reply:
        say(shown(json.loads(reply.read())))
    url = f"{base}/v1/tasks?state={','.join(ACTIVE)}"
    with TASKS_LOCK:
        before = TASK_EVENTS[0]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as reply:
            replace_tasks(json.loads(reply.read()).get("tasks") or [], before)
    except Exception as error:  # the turns still count when the task list can not be read
        sys.stderr.write(f"tasks feed: {error}\n")
        return
    say_tasks()


def recheck(base, headers, done):
    while not done.wait(RECHECK_S):
        try:
            ask(base, headers)
        except Exception as error:
            sys.stderr.write(f"busy feed: {error}\n")


def follow(cfg):
    base = cfg["hub_url"].rstrip("/")
    headers = {"X-Diablo-Token": cfg["hub_token"]}
    request = urllib.request.Request(f"{base}/v1/events?ping={PING_S}", headers={**headers, "Accept": "text/event-stream"})
    done = threading.Event()
    try:
        with urllib.request.urlopen(request, timeout=PING_S * 3) as stream:
            ask(base, headers)
            threading.Thread(target=recheck, args=(base, headers, done), daemon=True).start()
            for raw in stream:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith(":"):
                    say({"alive": True})
                elif line.startswith("data:"):
                    try:
                        event = json.loads(line[5:])
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("kind") == "busy":
                        say(shown(event))
                    elif isinstance(event, dict) and event.get("kind") == "task":
                        with TASKS_LOCK:
                            changed = track(event.get("task"))
                        if changed:
                            say_tasks()
                    elif isinstance(event, dict) and event.get("kind") == "screen":
                        try:
                            show(event)
                        except Exception as error:  # a bad card never cuts the busy feed
                            sys.stderr.write(f"screen: {error}\n")
                    elif isinstance(event, dict) and event.get("kind") == "duck":
                        try:
                            duck(event)
                        except Exception as error:
                            sys.stderr.write(f"duck: {error}\n")
    finally:
        done.set()


def main():
    while True:
        cfg = config()
        say({"hub": {"url": cfg["hub_url"], "token": cfg["hub_token"]}})
        try:
            follow(cfg)
        except Exception as error:
            sys.stderr.write(f"busy feed: {error}\n")
        with TASKS_LOCK:
            TASKS.clear()
            LAST_TASKS[0] = {"tasks": []}
        say({"turns": [], "tasks": [], "lost": True})
        time.sleep(RETRY_S)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
