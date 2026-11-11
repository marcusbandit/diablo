"""pushup in the banditshell launcher: where the challenge stands, a set now, logging a set by
typing its number, feet-up push-ups on and off. The rows come from `pushup status --json`."""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

PUSHUP = shutil.which("pushup") or os.path.expanduser("~/.local/bin/pushup")
KEYWORDS = ["push-ups", "pushups", "challenge", "workout", "exercise", "bulgarian", "squats", "rows"]
# What a plain search for an overview of the training says: these open the receipts.
OVERVIEW = ["overview", "status", "stats", "statistics", "receipts", "progress", "history", "training", "workout",
            "timeline", "muscles", "muscle groups", "body", "push-ups", "fitness"]
TIMELINE = ["timeline", "muscles", "muscle groups", "body", "overview", "pull", "core", "legs"]
# What a typed name after the number means, matched with spaces and hyphens taken out, so 'pull ups'
# and 'pike push-ups' work. The one letter shorthands are the launcher's own. Anything else is no set.
NAMES = (
    (r"b|bs|bss|bulg\w*|split\w*|squat\w*|legs?", "bulgarian split squats"),
    (r"d|dec\w*|feet\w*|incl\w*", "decline push-ups"),
    (r"p|pu|push\w*", "push-ups"),
    (r"r|rows?|bodyrows?|invertedrows?", "body rows"),
    (r"pull\w*|chin\w*|assistedpull\w*", "assisted pull-ups"),
    (r"dips?|assisteddips?", "assisted dips"),
    (r"pikeups?|seatedpike\w*", "seated pike ups"),
    (r"compressions?|compressionholds?|legliftholds?", "compression hold"),
    (r"pikepush\w*|pikes", "pike push-ups"),
    (r"lsits?", "L-sit"),
)
ONE = {"push-ups": "push-up", "decline push-ups": "decline push-up", "body rows": "body row",
       "assisted pull-ups": "assisted pull-up", "assisted dips": "assisted dip", "seated pike ups": "seated pike up",
       "pike push-ups": "pike push-up"}


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def pushup(*args, timeout=1.5):
    try:
        return subprocess.run([PUSHUP, *args], capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def status():
    try:
        return json.loads(pushup("status", "--json"))
    except ValueError:
        return None


def clock(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def minutes(seconds):
    m = max(0, round(seconds / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def unit(exercise, n):
    if exercise == "bulgarian split squats":
        return "Bulgarians each leg"
    if exercise in ("L-sit", "compression hold"):  # holds count seconds
        return ("second of " if n == 1 else "seconds of ") + exercise
    return ONE[exercise] if n == 1 else exercise


def next_line(s, now):
    """When the next set is, in a few words: the launcher gives a subtitle little room."""
    if not s["running"]:
        return "timer off"
    if s["paused_until"] > now:
        return f"paused until {clock(s['paused_until'])}"
    c = s.get("challenge")
    if c and c.get("quiet"):
        return "quiet until tomorrow"
    if s["locked"]:
        return "on hold while locked"
    if s["waiting"]:
        return f"waiting for {s['waiting']}"
    if s["next_due"]:
        return f"next set {clock(s['next_due'])}"
    return "no set planned"


def log_row(text, prefixed=True):
    """'15', '6 b', '8 decline', '3x10 rows' as a row that logs it. A word nobody knows means it
    isn't a set ('10 minutes'), and a plain search needs the word too, so typing a year never
    offers to log."""
    m = re.fullmatch(r"\s*(?:log\s+)?(?:(\d)\s*[x*×]\s*)?(\d{1,3})(?:\s+(.*?))?\s*", text, re.I)
    if not m or int(m[2]) < 1 or (m[1] and int(m[1]) < 1):
        return None
    sets, n, word = int(m[1] or 1), int(m[2]), re.sub(r"[\s-]+", "", (m[3] or "").lower())
    if not word and not prefixed:
        return None
    exercise = next((ex for pattern, ex in NAMES if re.fullmatch(pattern, word)), None) if word else "push-ups"
    if not exercise:
        return None
    total = sets * n * (2 if exercise == "bulgarian split squats" else 1)
    return {
        "id": f"log:{sets}x{n}:{exercise}",
        "title": f"Log {sets} sets of {n} {unit(exercise, n)}" if sets > 1 else f"Log {n} {unit(exercise, n)}",
        "subtitle": f"{total} in total" if total != n else "counts toward today",
        "icon": "add_task",
        "run": [PUSHUP, "log", f"{sets}x{n}" if sets > 1 else str(n), *exercise.split()],
    }


def rows(text, prefixed=True):
    now = time.time()
    typed = log_row(text, prefixed)
    if not prefixed and not typed and not matches({"title": "", "keywords": KEYWORDS + OVERVIEW + TIMELINE}, text):
        return []  # a plain search about something else: no need to ask pushup anything
    s = status()
    if s is None:
        return [{"id": "missing", "title": "pushup isn't answering", "subtitle": f"Looked for it at {PUSHUP}",
                 "icon": "error"}]
    out = []
    if typed:
        out.append(typed)
    c = s.get("challenge")
    if c:
        done = c["done"] >= c["target"]
        out.append({
            "id": "today",
            "title": f"{c['done']} of {c['target']} push-ups, day {c['day']}",
            "subtitle": ("target done, " if done else "keep going, " if now > c["deadline"] else "")
                        + next_line(s, now),
            "icon": "emoji_events" if done else "fitness_center",
            "keywords": KEYWORDS,
            "run": [PUSHUP, "graph"],
        })
    else:
        out.append({
            "id": "start",
            "title": "Start the push-up challenge",
            "subtitle": "30 days toward 100 a day",
            "icon": "flag",
            "keywords": KEYWORDS,
            "run": [PUSHUP, "challenge", "start"],
        })
    goal = (c or {}).get("next") or []
    out.append({
        "id": "now",
        "title": "Do a set now",
        "subtitle": ((f"{goal[0]['exercise']}, {goal[0]['range']} s" if goal[0].get("stretch")
                      else f"{goal[0]['range']} {unit(goal[0]['exercise'], 2)}") if goal else "in a few seconds"),
        "icon": "sports_gymnastics",
        "keywords": KEYWORDS,
        "run": [PUSHUP, "now"],
    })
    if not typed:
        out.append({
            "id": "log",
            "title": "Log a set you did",
            "subtitle": "or type pu 15, pu 8 d, pu 6 b",
            "icon": "add_task",
            "keywords": KEYWORDS,
            "run": [PUSHUP, "add"],
        })
    if c:
        b = c["bss"]
        out.append({
            "id": "bss",
            "title": f"Bulgarians, {b['sets']} of {b['cap']} sets",
            "subtitle": f"Enter logs {b['goal']} each leg",
            "icon": "directions_walk",
            "run": [PUSHUP, "log", str(b["goal"]), "bulgarian", "split", "squats"],
        })
        r = c.get("rows") or {}
        if r.get("cap"):
            out.append({
                "id": "rows",
                "title": f"Rows, {r['sets']} of {r['cap']} sets",
                "subtitle": f"Enter logs {r['goal']} at the table edge",
                "icon": "rowing",
                "run": [PUSHUP, "log", str(r["goal"]), "body", "rows"],
            })
        on = c["feet_up"]
        out.append({
            "id": "feet-up",
            "title": "Feet-up push-ups: on" if on else "Feet-up push-ups: off",
            "subtitle": "Enter switches off" if on else "when you're ready",
            "icon": "trending_down" if on else "trending_flat",
            "actions": [{"id": "feet-up", "title": "Switch off" if on else "Switch on", "icon": "swap_horiz"}],
        })
    out.append({
        "id": "overview",
        "title": "Training overview",
        "subtitle": "today, the month, every move",
        "icon": "monitoring",
        "keywords": OVERVIEW,
        "run": [PUSHUP, "graph"],
    })
    out.append({
        "id": "timeline",
        "title": "Muscle groups over time",
        "subtitle": "push, pull, legs and core, day by day",
        "icon": "calendar_view_week",
        "keywords": TIMELINE,
        "run": [PUSHUP, "graph", "--tab", "timeline"],
    })
    if s["paused_until"] > now:
        out.append({"id": "resume", "title": "Resume the sets", "subtitle": f"paused until {clock(s['paused_until'])}",
                    "icon": "play_arrow", "run": [PUSHUP, "resume"]})
    else:
        out.append({
            "id": "pause",
            "title": "Pause for an hour",
            "subtitle": "more in actions",
            "icon": "pause",
            "run": [PUSHUP, "pause", "1h"],
            "actions": [{"id": f"pause-{d}", "title": f"Pause {label}", "icon": "pause", "run": [PUSHUP, "pause", d]}
                        for d, label in (("1h", "an hour"), ("30m", "30 minutes"), ("2h", "2 hours"),
                                         ("tomorrow", "until tomorrow"))],
        })
    return out


def matches(item, text):
    """Every typed word starts a word of the row's title or keywords: 'over' finds overview, 'line'
    finds nothing."""
    hay = " ".join([item["title"], *item.get("keywords", [])]).lower().split()
    words = text.lower().split()
    return bool(words) and all(any(h.startswith(w) for h in hay) for w in words)


last_text = ""
for line in sys.stdin:
    try:
        message = json.loads(line)
    except ValueError:
        continue
    kind = message.get("type")
    if kind == "query":
        last_text = message.get("text", "")
        items = rows(last_text, bool(message.get("prefixed")))
        if not message.get("prefixed"):
            # A plain search: only the rows it is about, and only when it is about push-ups.
            items = [i for i in items if i["id"].startswith("log:") or (i.get("keywords") and matches(i, last_text))]
        send({"type": "results", "seq": message["seq"], "items": items})
    elif kind == "activate" and message.get("id") == "feet-up":
        on = (status() or {}).get("challenge", {}) or {}
        turn = "off" if on.get("feet_up") else "on"
        pushup("challenge", "feet-up", turn)
        send({"type": "effect", "do": "notify", "title": f"Feet-up push-ups {turn}",
              "body": "Decline push-ups come first now." if turn == "on" else "Back to plain push-ups."})
        send({"type": "results", "seq": message.get("seq", 0), "items": rows(last_text)})
    elif kind == "complete":
        if message.get("id") == "log":
            send({"type": "fill", "text": "pu "})
