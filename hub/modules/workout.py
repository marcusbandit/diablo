"""Workout: training logged as sets and reps in short sessions, read and added to from chat and the app.

pushup on the desktop owns the log. It writes overview.json, which Syncthing carries to the standby
and the hub. Diablo never edits the log: a set or a session said in chat or entered in the app
goes to this hub's own file in the pushup inbox, and pushup counts it the next time it reads the
inbox. Each set is one manual line, so pushup counts it as a set; session_id and set_no group the
sets of one session, and a close line ends a session early. The formats are in pushup/docs/sync.md.
"""

import datetime as dt
import json
import os
import socket
import statistics
import time
import uuid
from pathlib import Path

from .. import settings
from . import exercises

ID = "workout"
TITLE = "Workout"

EFFORTS = ("easy", "normal", "hard")
ACTIVITIES = ("climbing", "bouldering", "weights", "running", "cycling", "swimming", "football", "hiit", "yoga", "walking", "other")
GROUPS = ("push", "pull", "legs", "core")
GROUP_COLORS = {"push": "#2d81ff", "pull": "#3fd6b8", "legs": "#f2a516", "core": "#e5484d"}
GROUP_TONES = {"push": "accent", "pull": "ok", "legs": "warn", "core": "ink"}
REFRESH_MIN = 15
STALE_S = 3600
BAR_DAYS = 14
DAY_ROLL_H = 7
PUSH_UPS = ("pushups", "declinepushups")
WRITES = ("workout_log",)
MAX_SETS = 20
SET_SPACING_S = 90
LOG_TAIL = 256 * 1024
PLAN_GROUPS = ("push", "legs", "pull", "core")
PLAN_DEFAULT = {"push": "push-ups", "legs": "squats", "pull": "inverted rows", "core": "plank"}
PLAN_TASK = "Plan Marcus's next"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

TOOLS = [
    {
        "name": "workout_log",
        "description": (
            "Log training Marcus did into his pushup log, as sets and reps. A session is a few sets of one exercise and "
            "stays open until he says he is done or about 20 minutes pass without a set. kind set is one set: did is "
            "the reps (seconds for a hold like plank). Leave exercise out, or say these, for \"I did 10 of these\": "
            "it goes on the open session, or the exercise he logged last. kind sets is several at once, reps a list "
            "with one number a set (\"3 sets of 10\" is [10, 10, 10]). kind close ends the open session. kind "
            "session is training like climbing or running, with minutes and effort. Exercise names resolve to one "
            "correct name (pushup, Push-up and push ups are all Push-ups); an unknown one is refused with the closest "
            "names, so pick one of those or search with workout_exercises. pushup counts it the next time it reads "
            "its inbox. Say what you logged in a short line."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["set", "sets", "close", "session"]},
                "exercise": {"type": "string", "description": "for set and sets: the exercise; left out or these means the open session or the last one logged"},
                "did": {"type": "integer", "description": "for set: reps done, or seconds for a hold, 1 to 1000"},
                "reps": {"type": "array", "items": {"type": "integer"}, "description": "for sets: reps of each set in order, 1 to 1000 each, at most 20 sets"},
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
            "Marcus's training right now: the open session, today's sessions with their sets and reps, the push-up "
            "challenge, his streak, the last seven days, the muscles he worked least this week and anything logged "
            "that pushup has not counted yet. The Workout panel in his app shows the same, so answer with the one or "
            "two numbers he asked about."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "workout_exercises",
        "description": (
            "Find exercises by their correct names. With a query, the best matches, spelling slips forgiven; without "
            "one, his recent exercises and the defaults. Use it before workout_log when a name is unclear."
        ),
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "workout_plan",
        "description": (
            "Plan short sessions spread through the day and the week instead of piled into the night: a few slots a "
            "day inside his training window, rotating push, legs, pull and core, sized from his recent sets, with a "
            "rest day. Without schedule it only proposes the plan; tell him in a line or two. With schedule true, "
            "after he agrees, each slot is set as a reminder through add_reminder, plus one task at the end that "
            "plans the next stretch, so they survive restarts. Never use session cron jobs for this."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "how many days ahead, 1 to 14, default 7"},
                "per_day": {"type": "integer", "description": "sessions a day, 1 to 6; default the workout.per_day setting"},
                "schedule": {"type": "boolean", "description": "true sets the reminders; false or left out only proposes"},
            },
        },
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


def _number(key, default, low, high):
    try:
        value = int(float(settings.get(key) or default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def gap_s():
    return _number("workout.session_gap_min", 20, 5, 120) * 60


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


def inbox_lines():
    """Every line the Diablo hubs wrote to the inbox, oldest first."""
    out = []
    folder = inbox_dir()
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("diablo-*.jsonl")):
        try:
            lines = path.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and isinstance(entry.get("ts"), (int, float)) and not isinstance(entry.get("ts"), bool):
                out.append(entry)
    return sorted(out, key=lambda e: e["ts"])


def pending(since, lines=None):
    out = []
    for entry in inbox_lines() if lines is None else lines:
        if entry.get("result") not in ("manual", "session"):
            continue
        logged = entry.get("logged")
        if (logged if isinstance(logged, (int, float)) else entry["ts"]) > since:
            out.append(entry)
    return out


_tail_cache = {"key": None, "lines": []}


def log_tail():
    """The newest part of pushup's own log, for recent exercises, set sizes and the desktop's
    sessions. Read only, and read again only when the file changed."""
    path = state_dir() / "log.jsonl"
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _tail_cache["key"] == key:
        return list(_tail_cache["lines"])
    lines = _read_tail(path)
    _tail_cache.update(key=key, lines=lines)
    return list(lines)


def _read_tail(path):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL))
            raw = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = raw.splitlines()
    if size > LOG_TAIL and lines:
        lines = lines[1:]
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("ts"), (int, float)) and not isinstance(entry.get("ts"), bool):
            out.append(entry)
    return out


def _did(entry):
    did = entry.get("did")
    return did if isinstance(did, int) and not isinstance(did, bool) and did > 0 else None


def done_sets(lines=None, tail=None):
    """Every set with reps in it that is known here, oldest first: pushup's own log and the inbox."""
    out = []
    for entry in (log_tail() if tail is None else tail) + (inbox_lines() if lines is None else lines):
        if entry.get("result") in ("manual", "done") and _did(entry) and isinstance(entry.get("exercise"), str):
            out.append(entry)
    return sorted(out, key=lambda e: e["ts"])


def sessions(lines, tail=None):
    """The sets grouped into sessions, oldest first: the ones logged here, and the desktop card's
    sessions from pushup's own log (its sets carry a session_id too). A line with no session is its own."""
    desk = [e for e in (log_tail() if tail is None else tail) if e.get("session_id") and e.get("result") == "done"]
    lines = sorted([*lines, *desk], key=lambda e: e["ts"]) if desk else lines
    closed = {e.get("session_id") for e in lines if e.get("result") == "close"}
    out, by_id = [], {}
    for entry in lines:
        if entry.get("result") not in ("manual", "done") or not _did(entry):
            continue
        sid = entry.get("session_id") or entry.get("id") or f"line-{entry['ts']}"
        if sid not in by_id:
            ident = exercises.resolve(entry.get("exercise"))
            by_id[sid] = {"id": sid, "exercise": ident or str(entry.get("exercise") or ""), "name": exercises.name_of(entry.get("exercise")),
                          "timed": exercises.timed(entry.get("exercise")), "sets": [], "start": entry["ts"], "end": entry["ts"],
                          "closed": sid in closed or not entry.get("session_id"), "source": entry.get("source") or "diablo"}
            out.append(by_id[sid])
        session = by_id[sid]
        session["sets"].append(entry["did"])
        session["end"] = max(session["end"], entry["ts"])
    return out


def current(lines, now):
    """The session still open: the newest, unless it was closed or went quiet for longer than the gap."""
    found = sessions(lines)
    if not found:
        return None
    session = max(found, key=lambda s: s["end"])
    if session["closed"] or now - session["end"] > gap_s():
        return None
    return session


def last_exercise(lines, data, tail=None):
    """The exercise logged last anywhere, as (id or old name, reps)."""
    sets = done_sets(lines, tail)
    newest = sets[-1] if sets else None
    last = (data or {}).get("last") or {}
    if isinstance(last.get("ts"), (int, float)) and _did(last) and isinstance(last.get("exercise"), str):
        if newest is None or last["ts"] > newest["ts"]:
            newest = last
    if newest is None:
        return None
    return exercises.resolve(newest["exercise"]) or newest["exercise"], newest["did"]


def recent(lines=None, tail=None, limit=8):
    """The exercises he logged lately, newest first, by their correct names."""
    seen, out = set(), []
    for entry in reversed(done_sets(lines, tail)):
        ident = exercises.resolve(entry["exercise"])
        if ident and ident not in seen:
            seen.add(ident)
            out.append(exercises.entry(ident))
        if len(out) >= limit:
            break
    return out


def usual_reps(ident, lines=None, tail=None):
    history = [e["did"] for e in done_sets(lines, tail) if exercises.resolve(e["exercise"]) == ident][-6:]
    if not history:
        return 30 if exercises.BY_ID.get(ident, {}).get("timed") else 10
    return max(1, int(round(statistics.median(history))))


def describe(entry):
    if entry.get("result") == "session":
        name = entry.get("label") or entry.get("activity") or "training"
        return f"{name}, {entry.get('minutes')} min, {entry.get('effort') or 'normal'}"
    return f"{entry.get('did')} {exercises.name_of(entry.get('exercise')).lower()}"


def _ago(arguments):
    ago = arguments.get("minutes_ago") or 0
    if isinstance(ago, bool) or not isinstance(ago, (int, float)) or not 0 <= ago <= 7 * 24 * 60:
        raise ValueError("minutes_ago must be a number of minutes, at most a week")
    return ago


def _base(now, ts, source):
    return {"id": f"diablo-{uuid.uuid4().hex[:12]}", "ts": round(ts, 3), "logged": round(now, 3), "source": source}


def _reps(value, what="did"):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise ValueError(f"{what} must be a whole number from 1 to 1000")
    return value


def pick_exercise(raw, lines, data, now):
    """The id a set goes on: the name given, or for these and none the open session, then the last one logged."""
    text = " ".join(str(raw or "").split())
    if text and not exercises.is_deictic(text):
        ident = exercises.resolve(text)
        if ident:
            return ident
        near = exercises.closest(text)
        hint = f"; closest: {', '.join(near)}" if near else "; search with workout_exercises"
        raise ValueError(f"I do not know the exercise {text!r}{hint}")
    open_now = current(lines, now)
    if open_now:
        return open_now["exercise"]
    last = last_exercise(lines, data)
    if last:
        return last[0]
    raise ValueError("there is no open session and nothing logged yet, so say which exercise")


def entries_for(arguments, now, lines=None, data=None, source="diablo"):
    """The inbox lines one workout_log call writes."""
    lines = inbox_lines() if lines is None else lines
    kind = str(arguments.get("kind") or "").strip()
    ago = _ago(arguments)
    ts = round(now - ago * 60, 3)
    if kind == "session":
        activity = " ".join(str(arguments.get("activity") or "").lower().split())
        minutes = arguments.get("minutes")
        effort = str(arguments.get("effort") or "normal").lower()
        if not activity:
            raise ValueError("a session needs the activity")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 1440:
            raise ValueError("minutes must be from 1 to 1440")
        entry = _base(now, ts, source)
        entry.update(result="session", activity=activity, minutes=int(round(minutes)), effort=effort if effort in EFFORTS else "normal")
        if activity not in ACTIVITIES:
            entry["label"] = activity
        return [entry]
    if kind == "close":
        open_now = current(lines, now)
        if open_now is None:
            raise ValueError("no session is open")
        entry = _base(now, now, source)
        entry.update(result="close", session_id=open_now["id"])
        return [entry]
    if kind == "set":
        reps = [_reps(arguments.get("did"))]
    elif kind == "sets":
        raw = arguments.get("reps")
        if not isinstance(raw, list) or not raw:
            raise ValueError("reps must be a list with one number a set")
        if len(raw) > MAX_SETS:
            raise ValueError(f"at most {MAX_SETS} sets at once")
        reps = [_reps(value, "each set's reps") for value in raw]
    else:
        raise ValueError("kind must be set, sets, close or session")
    ident = pick_exercise(arguments.get("exercise"), lines, data, now)
    open_now = current(lines, now)
    first_ts = ts - SET_SPACING_S * (len(reps) - 1)
    step = SET_SPACING_S
    if open_now and open_now["exercise"] == ident and first_ts - open_now["end"] <= gap_s() and ts + 1 >= open_now["start"]:
        sid, start_no = open_now["id"], len(open_now["sets"]) + 1
        if first_ts <= open_now["end"]:
            first_ts = open_now["end"] + 0.001
            step = max(0.001, (ts - first_ts) / (len(reps) - 1)) if len(reps) > 1 else 0
    else:
        sid, start_no = f"s-{uuid.uuid4().hex[:10]}", 1
    out = []
    for index, did in enumerate(reps):
        entry = _base(now, first_ts + step * index, source)
        entry.update(result="manual", exercise=exercises.stored(ident), did=did, session_id=sid, set_no=start_no + index)
        out.append(entry)
    return out


def entry_for(arguments, now):
    """One line, for the calls that write one: a single set or a training session."""
    return entries_for(arguments, now)[0]


def log(arguments, now=None, source="diablo"):
    now = time.time() if now is None else now
    entries = entries_for(arguments, now, data=overview(), source=source)
    path = own_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return entries


def reps_text(session):
    unit = " s" if session["timed"] else ""
    return ", ".join(f"{n}{unit}" for n in session["sets"])


def logged_text(entries, lines_after, now):
    first = entries[0]
    if first.get("result") == "session":
        return f"logged {describe(first)}; pushup counts it when it next reads its inbox"
    if first.get("result") == "close":
        done = next((s for s in sessions(lines_after) if s["id"] == first["session_id"]), None)
        if done:
            return f"closed the {done['name'].lower()} session: {plural(len(done['sets']), 'set')}, {reps_text(done)}"
        return "closed the session"
    session = next((s for s in sessions(lines_after) if s["id"] == first["session_id"]), None)
    name = exercises.name_of(first["exercise"])
    if len(entries) == 1:
        said = f"logged set {first['set_no']} of {name.lower()}: {first['did']}{' s' if session and session['timed'] else ''}"
    else:
        said = f"logged {plural(len(entries), 'set')} of {name.lower()}: " + ", ".join(str(e["did"]) for e in entries)
    if session and len(session["sets"]) > len(entries):
        said += f" (this session: {reps_text(session)})"
    return said + "; pushup counts it when it next reads its inbox"


def plan_slots(now=None, days=7, per_day=None, lines=None, tail=None):
    """Short sessions at sensible times: a few a day inside the window, a rest day, groups in turn."""
    now_dt = dt.datetime.fromtimestamp(time.time() if now is None else now)
    per_day = per_day or _number("workout.per_day", 3, 1, 6)
    lines = inbox_lines() if lines is None else lines
    tail = log_tail() if tail is None else tail
    window = str(settings.get("workout.window") or "10:00-20:00")
    try:
        begin, end = (dt.datetime.strptime(part.strip(), "%H:%M").time() for part in window.split("-", 1))
    except ValueError:
        begin, end = dt.time(10), dt.time(20)
    rest = str(settings.get("workout.rest_day") or "sun").strip().lower()[:3]
    span = (end.hour * 60 + end.minute) - (begin.hour * 60 + begin.minute)
    if span < 60:
        begin, span = dt.time(10), 600
    have = recent(lines, tail, limit=20)
    choices = {group: [e["id"] for e in have if e["group"] == group][:2] or [PLAN_DEFAULT[group]] for group in PLAN_GROUPS}
    turns = {group: 0 for group in PLAN_GROUPS}
    slots, order = [], 0
    for offset in range(days):
        day = now_dt.date() + dt.timedelta(days=offset)
        if WEEKDAYS[day.weekday()] == rest:
            continue
        for index in range(per_day):
            minute = begin.hour * 60 + begin.minute + round(span * (index + 0.5) / per_day / 15) * 15
            at = dt.datetime.combine(day, dt.time(minute // 60 % 24, minute % 60))
            if at < now_dt + dt.timedelta(minutes=30):
                continue
            group = PLAN_GROUPS[order % len(PLAN_GROUPS)]
            order += 1
            pool = choices[group]
            ident = pool[turns[group] % len(pool)]
            turns[group] += 1
            info = exercises.entry(ident)
            reps = usual_reps(ident, lines, tail)
            slots.append({"at": at.strftime("%Y-%m-%dT%H:%M"), "exercise": ident, "name": info["name"], "group": group,
                          "sets": 3, "reps": reps, "timed": info["timed"]})
    return slots


def slot_text(slot):
    amount = f"{slot['reps']} seconds" if slot["timed"] else f"{slot['reps']}"
    return f"{slot['name']}: 3 sets of {amount}"


def plan(arguments, now=None):
    from . import reminders

    days = arguments.get("days") or 7
    per_day = arguments.get("per_day") or None
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 14:
        return "not planned: days must be from 1 to 14"
    if per_day is not None and (isinstance(per_day, bool) or not isinstance(per_day, int) or not 1 <= per_day <= 6):
        return "not planned: per_day must be from 1 to 6"
    slots = plan_slots(now, days, per_day)
    if not slots:
        return "nothing to plan: no slot is left in the window for those days"
    lines = [f"{s['at'].replace('T', ' ')}  {slot_text(s)}" for s in slots]
    if not arguments.get("schedule"):
        return (f"proposed {plural(len(slots), 'session')} over {plural(days, 'day')}:\n" + "\n".join(lines) +
                "\nnothing is set yet; call again with schedule true once he agrees")
    pending_plan = [row for row in reminders.upcoming() or [] if str(row.get("text") or "").startswith(PLAN_TASK)
                    and float(row.get("next_due") or 0) > (time.time() if now is None else now) + 600]
    if pending_plan:
        return (f"not planned: a plan is already scheduled, and reminder #{pending_plan[0].get('id')} plans the next stretch; "
                "cancel that plan's reminders with cancel_reminder first if he wants a new one")
    failed = []
    for slot in slots:
        said = reminders.call("add_reminder", {"text": slot_text(slot), "due": slot["at"]})
        if said.startswith("not set"):
            failed.append(f"{slot['at']}: {said}")
    from .. import events

    events.publish({"kind": "panel", "modules": ["reminders", "today"]})
    set_count = len(slots) - len(failed)
    head = f"set {set_count} of {plural(len(slots), 'reminder')}"
    if set_count:
        last = dt.datetime.fromisoformat(slots[-1]["at"]) + dt.timedelta(hours=1)
        said = reminders.call("add_reminder", {
            "text": f"{PLAN_TASK} {days} days of workouts: call workout_plan with days {days} and schedule true, then tell him in one line.",
            "due": last.strftime("%Y-%m-%dT%H:%M"), "task": True,
        })
        head += (f", and the task that plans the next stretch was not set ({said})" if said.startswith("not set")
                 else f", and a task at {last.strftime('%a %H:%M')} to plan the next stretch")
    else:
        head += ", so nothing will plan the next stretch"
    return head + (":\n" + "\n".join(failed) if failed else "")


def exercises_text(query):
    if str(query or "").strip():
        hits = exercises.search(query)
        if not hits:
            return f"no exercise matches {query!r}"
        return "matches: " + ", ".join(h["name"] for h in hits)
    have = recent()
    defaults = [exercises.entry(i) for i in exercises.DEFAULTS if i not in {e["id"] for e in have}]
    return "recent: " + (", ".join(e["name"] for e in have) or "none yet") + "\ndefaults: " + ", ".join(e["name"] for e in defaults)


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


def today_sessions(lines, now):
    day = day_of(now)
    return [s for s in sessions(lines) if day_of(s["end"]) == day]


def session_line(session):
    return f"{session['name']}, {plural(len(session['sets']), 'set')}: {reps_text(session)}"


def stats_text(data, waiting, lines=(), now=None):
    now = time.time() if now is None else now
    if data is None:
        out = ["No training overview on this hub yet: pushup's overview.json has not synced here."]
    else:
        today = data.get("today") or {}
        out = [f"Today ({data.get('day')}): {today.get('line') or 'no sets yet'}."]
        challenge = data.get("challenge")
        if challenge:
            out.append(
                f"Push-up challenge day {challenge.get('day')} of {challenge.get('days')}:"
                f" {challenge.get('done')} of {challenge.get('target')} push-ups, {challenge.get('left')} left."
            )
        out.append(f"Streak: {data.get('streak', 0)} days.")
        if today.get("trained"):
            out.append("Trained today: " + ", ".join(map(str, today["trained"])) + ".")
        days = (data.get("timeline") or {}).get("recent") or []
        for day in days[1:7]:
            out.append(f"{day.get('date')}: {'; '.join(day.get('lines') or []) or 'nothing'}.")
        neglected = (data.get("week") or {}).get("neglected") or []
        if neglected:
            out.append("Least worked this week: " + ", ".join(neglected) + ".")
        age = now - float(data.get("generated") or 0)
        if age > STALE_S:
            out.append(f"This overview is {round(age / 3600)} h old; the desktop has not updated it since.")
    open_now = current(list(lines), now)
    if open_now:
        out.append(f"Open session: {session_line(open_now)}, last set {ago(now - open_now['end'])}.")
    done = [s for s in today_sessions(list(lines), now) if not open_now or s["id"] != open_now["id"]]
    if done:
        out.append("Sessions today: " + "; ".join(f"{session_line(s)} at {time.strftime('%H:%M', time.localtime(s['end']))}" for s in done) + ".")
    if waiting:
        out.append("Logged here, not counted by pushup yet: " + "; ".join(describe(e) for e in waiting) + ".")
    return "\n".join(out)


def call(name, arguments):
    if name == "workout_log":
        try:
            entries = log(arguments)
        except ValueError as error:
            return f"not logged: {error}"
        except OSError as error:
            return f"not logged, the inbox is not writable here: {error}"
        return logged_text(entries, inbox_lines(), time.time())
    if name == "workout_stats":
        data = overview()
        lines = inbox_lines()
        return stats_text(data, pending(float((data or {}).get("generated") or 0), lines), lines)
    if name == "workout_exercises":
        return exercises_text((arguments or {}).get("query"))
    if name == "workout_plan":
        return plan(arguments or {})
    raise LookupError(name)


def act(action, args):
    """A tap in the app: log sets of one exercise, end the open session, or search."""
    args = dict(args or {})
    if action in ("set", "sets", "close"):
        try:
            entries = log({**args, "kind": action}, source="app")
        except OSError as error:
            from .common import ActFailed
            raise ActFailed(f"The log could not be written here: {error}")
        return logged_text(entries, inbox_lines(), time.time())
    raise ValueError(f"workout has no action {action}")


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


def actions_for(data, open_now=None, last=None):
    if open_now:
        reps = open_now["sets"][-1]
        unit = " s" if open_now["timed"] else ""
        return {"kind": "actions", "items": [
            {"label": f"Add a set of {reps}{unit}", "icon": "add",
             "act": {"module": ID, "action": "set", "args": {"did": reps}}},
            {"label": f"Finish {open_now['name'].lower()}", "icon": "done", "act": {"module": ID, "action": "close", "args": {}}},
            {"label": "How am I doing?", "icon": "insights", "say": "How is my training going this week?"},
        ]}
    if last and exercises.resolve(last[0]):
        name = exercises.name_of(last[0])
        first = {"label": f"Log {last[1]} {name.lower()}", "icon": "add",
                 "act": {"module": ID, "action": "set", "args": {"exercise": exercises.resolve(last[0]), "did": last[1]}}}
    else:
        first = {"label": "Log a set", "icon": "add", "say": "I did ", "send": False}
    return {"kind": "actions", "items": [
        first,
        {"label": "I trained today", "icon": "run", "say": "I trained today: ", "send": False},
        {"label": "How am I doing?", "icon": "insights", "say": "How is my training going this week?"},
    ]}


def session_block(session, now):
    return {"id": session["id"], "exercise": session["exercise"], "name": session["name"], "timed": session["timed"],
            "sets": list(session["sets"]), "start": session["start"], "end": session["end"],
            "at": time.strftime("%H:%M", time.localtime(session["end"])), "open": False}


def sets_block(lines, data, now, tail=None):
    """What the app's logger needs: the open session, today's sessions, recent and default exercises, the full list."""
    open_now = current(lines, now)
    have = recent(lines, tail)
    seen = {e["id"] for e in have}
    last = last_exercise(lines, data, tail)
    today = [dict(session_block(s, now), open=bool(open_now and s["id"] == open_now["id"])) for s in today_sessions(lines, now)]
    block = {
        "kind": "sets", "title": "Sets and reps", "gap_min": gap_s() // 60,
        "current": dict(session_block(open_now, now), open=True) if open_now else None,
        "last": {"exercise": exercises.resolve(last[0]), "name": exercises.name_of(last[0]), "did": last[1],
                 "timed": exercises.timed(last[0])} if last else None,
        "today": list(reversed(today)),
        "sets_today": sets_today(lines, data, now),
        "recent": have,
        "defaults": [exercises.entry(i) for i in exercises.DEFAULTS if i not in seen],
        "library": exercises.library(),
    }
    if block["current"]:
        block["current"]["suggest"] = usual_reps(open_now["exercise"], lines, tail) if open_now["exercise"] in exercises.BY_ID else open_now["sets"][-1]
    return block


def sets_today(lines, data, now):
    """Every set today: what pushup counted, plus what it has not read from the inbox yet."""
    day = day_of(now)
    counted = int(((data or {}).get("today") or {}).get("sets", 0) or 0) if (data or {}).get("day") == day else 0
    waiting = pending(float((data or {}).get("generated") or 0), lines)
    return counted + sum(1 for e in waiting if e.get("result") == "manual" and day_of(e["ts"]) == day)


def sessions_list(lines, now):
    found = today_sessions(lines, now)
    if not found:
        return None
    open_now = current(lines, now)
    items = [{"title": s["name"], "detail": reps_text(s), "trailing": "Now" if open_now and s["id"] == open_now["id"] else
              time.strftime("%H:%M", time.localtime(s["end"]))} for s in reversed(found)]
    return {"kind": "list", "title": "Sessions today", "icon": "dumbbell", "items": items}


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
    lines = inbox_lines()
    tail = log_tail()
    waiting = pending(float((data or {}).get("generated") or 0), lines)
    open_now = current(lines, now)
    last = last_exercise(lines, data, tail)
    logger = sets_block(lines, data, now, tail)
    today_list = sessions_list(lines, now)
    if data is None:
        return [empty_block(state), logger, *([today_list] if today_list else []), actions_for(None, open_now, last)]
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
    blocks.append(logger)
    blocks.append(actions_for(data, open_now, last))
    if today_list:
        blocks.append(today_list)
    trained = len(today.get("trained") or [])
    if not challenge:
        items = [{"label": "Sets today", "value": str(logger["sets_today"]), "icon": "done"},
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
