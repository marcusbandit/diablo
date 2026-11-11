"""Workout: the pushup training log, read and added to from chat.

pushup on banditbox owns the log. It writes overview.json, which Syncthing carries to nemurenai
and diablo-pi. Diablo never edits the log: a set or a session said in chat goes to this hub's own
file in the pushup inbox, and pushup counts it the next time it reads the inbox. The formats are in
pushup/docs/sync.md.
"""

import datetime as dt
import json
import os
import socket
import time
import uuid
from pathlib import Path

from .. import settings

ID = "workout"
TITLE = "Workout"

EFFORTS = ("easy", "normal", "hard")
ACTIVITIES = ("climbing", "bouldering", "weights", "running", "cycling", "swimming", "football", "hiit", "yoga", "walking", "other")
EXERCISES = (
    "push-ups", "decline push-ups", "squats", "lunges", "bulgarian split squats", "burpees", "squat jumps",
    "step-ups", "calf raises", "sit-ups", "plank", "side plank", "hollow hold", "glute bridges", "chair dips",
    "pike push-ups", "jumping jacks", "mountain climbers", "cossack squats", "wall sit", "supermen",
    "assisted pull-ups", "assisted dips", "body rows", "seated pike ups", "compression hold", "L-sit",
)
GROUPS = ("push", "pull", "legs", "core")
GROUP_COLORS = {"push": "#2d81ff", "pull": "#3fd6b8", "legs": "#f2a516", "core": "#e5484d"}
GROUP_TONES = {"push": "accent", "pull": "ok", "legs": "warn", "core": "ink"}
REFRESH_MIN = 15
STALE_S = 3600
BAR_DAYS = 14
DAY_ROLL_H = 7
PUSH_UPS = ("pushups", "declinepushups")
WRITES = ("workout_log",)

TOOLS = [
    {
        "name": "workout_log",
        "description": (
            "Log training Marcus did away from his desk into his pushup log. kind set is a number of reps of one "
            "exercise (holds like plank count seconds); kind session is a training session like climbing or "
            "running, with minutes and effort. pushup on banditbox counts it the next time it reads its inbox, "
            "which also steers his next push-up cards around what this tired. Say what you logged in a short line."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["set", "session"]},
                "exercise": {"type": "string", "description": "for a set: one of " + ", ".join(EXERCISES)},
                "did": {"type": "integer", "description": "for a set: reps done, or seconds for a hold, 1 to 1000"},
                "activity": {"type": "string", "description": "for a session: one of " + ", ".join(ACTIVITIES) + ", or its own name"},
                "minutes": {"type": "integer", "description": "for a session: how long, 1 to 1440"},
                "effort": {"type": "string", "enum": list(EFFORTS)},
                "minutes_ago": {"type": "integer", "description": "when it ended, in minutes before now; 0 or left out for just now"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "workout_stats",
        "description": (
            "Marcus's training right now: today's sets, the push-up challenge, his streak, the last seven days, the "
            "muscles he worked least this week and anything logged from chat that pushup has not counted yet. "
            "The Workout panel in his app shows the same, so answer with the one or two numbers he asked about."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _dir(key, default):
    return Path(os.path.expanduser(settings.get(key) or os.environ.get(key.upper().replace(".", "_")) or default))


def state_dir():
    return _dir("workout.dir", "~/.local/state/pushup")


def inbox_dir():
    return _dir("workout.inbox", "~/.local/state/pushup-inbox")


def own_file():
    return inbox_dir() / f"diablo-{socket.gethostname()}.jsonl"


def read_overview():
    try:
        text = (state_dir() / "overview.json").read_text()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None
    try:
        data = json.loads(text)
    except ValueError:
        return "unreadable", None
    stamp = data.get("generated") if isinstance(data, dict) else None
    if isinstance(data, dict) and data.get("schema") == 1 and isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and stamp > 0:
        return "ok", data
    return "unreadable", None


def overview():
    return read_overview()[1]


def pending(since):
    out = []
    folder = inbox_dir()
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("diablo-*.jsonl")):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("ts"), (int, float)):
                continue
            logged = entry.get("logged")
            if (logged if isinstance(logged, (int, float)) else entry["ts"]) > since:
                out.append(entry)
    return sorted(out, key=lambda e: e["ts"])


def describe(entry):
    if entry.get("result") == "session":
        name = entry.get("label") or entry.get("activity") or "training"
        return f"{name}, {entry.get('minutes')} min, {entry.get('effort') or 'normal'}"
    return f"{entry.get('did')} {entry.get('exercise')}"


def entry_for(arguments, now):
    kind = str(arguments.get("kind") or "").strip()
    ago = arguments.get("minutes_ago") or 0
    if isinstance(ago, bool) or not isinstance(ago, (int, float)) or not 0 <= ago <= 7 * 24 * 60:
        raise ValueError("minutes_ago must be a number of minutes, at most a week")
    entry = {"id": f"diablo-{uuid.uuid4().hex[:12]}", "ts": round(now - ago * 60, 3), "logged": round(now, 3), "source": "diablo"}
    if kind == "set":
        exercise = " ".join(str(arguments.get("exercise") or "").lower().split())
        did = arguments.get("did")
        if not exercise:
            raise ValueError("a set needs the exercise")
        if isinstance(did, bool) or not isinstance(did, int) or not 1 <= did <= 1000:
            raise ValueError("did must be a whole number from 1 to 1000")
        entry.update(result="manual", exercise=exercise, did=did)
        return entry
    if kind == "session":
        activity = " ".join(str(arguments.get("activity") or "").lower().split())
        minutes = arguments.get("minutes")
        effort = str(arguments.get("effort") or "normal").lower()
        if not activity:
            raise ValueError("a session needs the activity")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 1440:
            raise ValueError("minutes must be from 1 to 1440")
        entry.update(result="session", activity=activity, minutes=int(round(minutes)),
                     effort=effort if effort in EFFORTS else "normal")
        if activity not in ACTIVITIES:
            entry["label"] = activity
        return entry
    raise ValueError("kind must be set or session")


def log(arguments, now=None):
    entry = entry_for(arguments, time.time() if now is None else now)
    path = own_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def day_of(ts):
    return (dt.datetime.fromtimestamp(ts) - dt.timedelta(hours=DAY_ROLL_H)).date().isoformat()


def plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def day_name(date, today):
    try:
        day = dt.date.fromisoformat(date)
        now = dt.date.fromisoformat(today)
    except (TypeError, ValueError):
        return date or ""
    gap = (now - day).days
    if gap == 0:
        return "Today"
    if gap == 1:
        return "Yesterday"
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def push_ups_today(data, waiting):
    day = data.get("day")
    return sum(
        e.get("did", 0) for e in waiting
        if e.get("result") == "manual" and day_of(e["ts"]) == day
        and "".join(ch for ch in str(e.get("exercise", "")).lower() if ch.isalnum()) in PUSH_UPS
    )


def stats_text(data, waiting):
    if data is None:
        lines = ["No training overview on this hub yet: pushup's overview.json has not synced here."]
    else:
        today = data.get("today") or {}
        lines = [f"Today ({data.get('day')}): {today.get('line') or 'no sets yet'}."]
        challenge = data.get("challenge")
        if challenge:
            lines.append(
                f"Push-up challenge day {challenge.get('day')} of {challenge.get('days')}:"
                f" {challenge.get('done')} of {challenge.get('target')} push-ups, {challenge.get('left')} left."
            )
        lines.append(f"Streak: {data.get('streak', 0)} days.")
        if today.get("trained"):
            lines.append("Trained today: " + ", ".join(map(str, today["trained"])) + ".")
        recent = (data.get("timeline") or {}).get("recent") or []
        for day in recent[1:7]:
            lines.append(f"{day.get('date')}: {'; '.join(day.get('lines') or []) or 'nothing'}.")
        neglected = (data.get("week") or {}).get("neglected") or []
        if neglected:
            lines.append("Least worked this week: " + ", ".join(neglected) + ".")
        age = time.time() - float(data.get("generated") or 0)
        if age > STALE_S:
            lines.append(f"This overview is {round(age / 3600)} h old; banditbox has not updated it since.")
    if waiting:
        lines.append("Logged from chat, not counted by pushup yet: " + "; ".join(describe(e) for e in waiting) + ".")
    return "\n".join(lines)


def call(name, arguments):
    if name == "workout_log":
        try:
            entry = log(arguments)
        except ValueError as error:
            return f"not logged: {error}"
        except OSError as error:
            return f"not logged, the inbox is not writable here: {error}"
        return f"logged {describe(entry)}; pushup counts it when it next reads its inbox"
    if name == "workout_stats":
        data = overview()
        return stats_text(data, pending(float((data or {}).get("generated") or 0)))
    raise LookupError(name)


def _bars(data):
    timeline = data.get("timeline") or {}
    sets = timeline.get("sets") or {}
    days = int(timeline.get("days") or 0)
    if not days or not sets:
        return None
    start = max(0, days - BAR_DAYS)
    first = timeline.get("first")
    labels, dates = [], []
    try:
        base = dt.date.fromisoformat(first)
        labels = [(base + dt.timedelta(days=i)).strftime("%d") for i in range(start, days)]
        dates = [(base + dt.timedelta(days=i)).isoformat() for i in range(start, days)]
    except (TypeError, ValueError):
        labels = [str(i + 1) for i in range(start, days)]
        dates = [""] * len(labels)
    def padded(values):
        values = [float(v) if isinstance(v, (int, float)) else 0.0 for v in (values or [])][:days]
        return values + [0.0] * (days - len(values))

    series = [
        {"name": group, "color": GROUP_COLORS[group], "tone": GROUP_TONES[group], "values": padded(sets.get(group))[start:days]}
        for group in GROUPS
    ]
    return {"kind": "bars", "title": "Sets a day", "caption": "Last two weeks", "icon": "trend",
            "labels": labels, "dates": dates, "unit": "set", "series": series, "stacked": True}


BODY = (
    ("Upper body", ("chest", "shoulders", "triceps", "biceps", "forearms", "traps", "lats")),
    ("Core", ("abs", "obliques", "lower back", "hip flexors")),
    ("Legs", ("glutes", "quads", "hamstrings", "adductors", "calves")),
)


def day_start(day):
    try:
        return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(DAY_ROLL_H)).timestamp()
    except (TypeError, ValueError):
        return None


def pace(challenge, extra, now=None, day=None):
    now = time.time() if now is None else now
    done = int(challenge.get("done") or 0) + extra
    target = int(challenge.get("target") or 0)
    if target and done >= target:
        return "Done for today. Anything more is a bonus."
    deadline = float(challenge.get("deadline") or 0)
    begin = day_start(day) or deadline - 20 * 3600
    if not deadline or now <= begin:
        return f"{max(0, target - done)} to go"
    share = min(1.0, (now - begin) / (deadline - begin))
    expected = round(target * share)
    gap = done - expected
    if gap >= 0:
        return f"On pace, {target - done} to go"
    return f"{-gap} behind pace, {target - done} to go"


def muscles(data):
    week = data.get("week") or {}
    work = week.get("work") or {}
    stretch = week.get("stretch") or {}
    if not work:
        return None
    groups = []
    for name, parts in BODY:
        items = [{"label": part.capitalize(), "work": round(float(work.get(part, 0) or 0), 3),
                  "stretch": round(float(stretch.get(part, 0) or 0), 3)} for part in parts]
        groups.append({"name": name, "items": items})
    neglected = week.get("neglected") or []
    block = {"kind": "muscles", "title": "Muscles this week", "groups": groups}
    if neglected:
        block["caption"] = "Least worked: " + ", ".join(neglected)
        block["neglected"] = [str(name) for name in neglected]
    return block


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} min ago"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} days ago"


def updated(data, now):
    stamp = float(data.get("generated") or 0)
    return {"age": max(0, int(now - stamp)), "at": time.strftime("%H:%M", time.localtime(stamp)), "every": REFRESH_MIN}


def stale_line(data, now):
    return f"The log has not refreshed since {updated(data, now)['at']}, so these numbers may be behind."


def empty_block(state):
    if state == "unreadable":
        return {
            "kind": "notice", "state": "unreadable", "tone": "fail", "retry": True, "title": "I cannot read the training log",
            "text": "The latest update came in a form I cannot read. Every update replaces it, so the next set or the next "
                    f"{REFRESH_MIN} minute refresh should put it right.",
        }
    return {
        "kind": "notice", "state": "waiting", "retry": True, "title": "No training logged yet",
        "text": f"This fills in after your first counted set. The log refreshes after every set and at least every {REFRESH_MIN} "
                "minutes, so it should appear soon after you start. A set you tell me about in chat is counted at the next refresh.",
    }


def last_push_ups(data):
    last = data.get("last") or {}
    exercise = "".join(ch for ch in str(last.get("exercise", "")).lower() if ch.isalnum())
    did = last.get("did")
    if exercise in PUSH_UPS and isinstance(did, int) and not isinstance(did, bool) and 1 <= did <= 1000:
        return did
    return None


def actions_for(data):
    reps = last_push_ups(data) if data else None
    if reps:
        first = {"label": f"Log {reps} push-ups", "icon": "add", "say": f"Log {reps} push-ups", "tip": "The size of your last set"}
    else:
        first = {"label": "Log push-ups", "icon": "add", "say": "Log push-ups: ", "send": False, "tip": "Type how many"}
    return {"kind": "actions", "items": [
        first,
        {"label": "I trained today", "icon": "run", "say": "I trained today: ", "send": False},
        {"label": "How am I doing?", "icon": "insights", "say": "How is my training going this week?"},
    ]}


def week_rows(data, now_day):
    timeline = data.get("timeline") or {}
    recent = timeline.get("recent") or []
    sets = timeline.get("sets") or {}
    try:
        base = dt.date.fromisoformat(timeline.get("first"))
    except (TypeError, ValueError):
        base = None
    rows = []
    for day in recent[:7]:
        date = day.get("date", "")
        groups = {}
        if base:
            try:
                index = (dt.date.fromisoformat(date) - base).days
            except (TypeError, ValueError):
                index = -1
            for group in GROUPS:
                values = sets.get(group) or []
                if 0 <= index < len(values) and isinstance(values[index], (int, float)) and values[index]:
                    groups[group] = int(values[index])
        lines = [str(line) for line in day.get("lines") or []]
        rows.append({"title": day_name(date, now_day), "date": date, "detail": "; ".join(lines) or ("" if day.get("sets") else "Rest day"),
                     "lines": lines, "groups": groups, "trailing": plural(day.get("sets", 0), "set")})
    return rows


def panel(section):
    state, data = read_overview()
    now = time.time()
    waiting = pending(float((data or {}).get("generated") or 0))
    if data is None:
        return [empty_block(state), actions_for(None)]
    blocks = []
    today = data.get("today") or {}
    challenge = data.get("challenge")
    extra = push_ups_today(data, waiting)
    streak = int(data.get("streak", 0) or 0)
    stale = stale_line(data, now) if now - float(data.get("generated") or 0) > STALE_S else None
    if challenge:
        blocks.append({
            "kind": "goal", "title": "Push-ups today", "icon": "dumbbell",
            "value": int(challenge.get("done") or 0) + extra, "max": int(challenge.get("target") or 1),
            "caption": stale or pace(challenge, extra, day=data.get("day")),
            "updated": updated(data, now), "stale": bool(stale),
            "badges": [
                {"icon": "flame", "label": f"{streak} day streak"},
                {"icon": "done", "label": plural(today.get("sets", 0), "set") + " today"},
                {"icon": "calendar", "label": f"Day {challenge.get('day')} of {challenge.get('days')}"},
            ],
        })
    blocks.append(actions_for(data))
    trained = len(today.get("trained") or [])
    if not challenge:
        items = [{"label": "Sets today", "value": str(today.get("sets", 0)), "icon": "done"},
                 {"label": "Streak", "value": str(streak), "unit": "days", "icon": "flame"}]
        if trained:
            items.append({"label": "Trained today", "value": str(trained), "icon": "run"})
        blocks.append({"kind": "stats", "items": items, "updated": updated(data, now), "stale": bool(stale),
                       **({"caption": stale} if stale else {})})
    elif trained:
        blocks.append({"kind": "stats", "items": [{"label": "Trained today", "value": str(trained), "icon": "run"}]})
    body = muscles(data)
    if body:
        blocks.append(body)
    bars = _bars(data)
    if bars:
        blocks.append(bars)
    rows = week_rows(data, data.get("day"))
    if rows:
        blocks.append({"kind": "list", "link": "days", "title": "This week", "icon": "calendar", "items": rows})
    if waiting:
        blocks.append({"kind": "list", "link": "queue", "title": "Not counted yet", "icon": "clock",
                       "caption": "Logged in chat. These count at the next refresh.",
                       "items": [{"title": describe(e), "trailing": time.strftime("%H:%M", time.localtime(e["ts"]))} for e in waiting]})
    if not body and not bars and not rows:
        blocks.append({"kind": "notice", "state": "quiet", "title": "Nothing trained this week",
                       "text": "Muscles, the daily chart and the week fill in once a set is counted."})
    return blocks


def summary():
    data = overview()
    if data is None:
        return None
    challenge = data.get("challenge")
    if challenge:
        return {"label": "Push-ups", "value": str(challenge.get("done") or 0), "unit": f"/ {challenge.get('target')}", "icon": "dumbbell"}
    return {"label": "Sets today", "value": str((data.get("today") or {}).get("sets", 0)), "icon": "dumbbell"}
