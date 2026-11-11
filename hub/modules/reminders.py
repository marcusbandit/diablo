"""Reminders: the remind CLI from reminders/ in this repo, which keeps them on disk and sends them from its own unit."""

import datetime as dt
import json
import os
import subprocess

REMIND = os.environ.get("DIABLO_REMIND_BIN", os.path.expanduser("~/.local/bin/remind"))
from .common import ActFailed, Cache, unavailable

ID = "reminders"
TITLE = "Reminders"
NAMES = ("add_reminder", "list_reminders", "cancel_reminder")
WRITES = ("add_reminder", "cancel_reminder")
_cache = Cache(10)
CACHES = (_cache,)

TOOLS = [
    {
        "name": "add_reminder",
        "description": (
            "Set a reminder for Marcus. It is stored on disk and the reminder service sends it to him on Telegram "
            "in your voice at the due time, even across hub restarts and Pi reboots; one missed while the Pi was "
            "down arrives late, never not at all. Use this for every remind me order, never a session cron job, "
            "which dies with the hub. Weekly repeats on the weekday of due. Confirm the time the tool returns."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "what to remind him of, written as you want him to read it"},
                "due": {
                    "type": "string",
                    "description": "when, as local Copenhagen time in ISO 8601 like 2026-11-08T13:00 (an offset is honoured if given), or relative like +20m or +2h",
                },
                "recurrence": {"type": "string", "enum": ["none", "daily", "weekly", "weekdays"], "description": "defaults to none"},
                "task": {
                    "type": "boolean",
                    "description": (
                        "true when Marcus wants you to DO something at that time rather than be reminded: the text is "
                        "then your own instruction, run as a turn of yours at the due time, and Marcus gets one line "
                        "with the result. Write it so you can act on it cold, with every name, address and detail."
                    ),
                },
            },
            "required": ["text", "due"],
        },
    },
    {
        "name": "list_reminders",
        "description": "The reminders that are set, with their ids and next times. include_past also shows fired and cancelled ones with how their last delivery went.",
        "inputSchema": {
            "type": "object",
            "properties": {"include_past": {"type": "boolean"}},
        },
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a reminder by its id from list_reminders. A repeating one stops for good.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
]


def _argv(name, arguments):
    if name == "add_reminder":
        repeat = str(arguments.get("recurrence") or "none")
        task = ["--task"] if arguments.get("task") else []
        return ["add", "--repeat", repeat, *task, "--source", "diablo", "--",
                str(arguments.get("due") or ""), str(arguments.get("text") or "")]
    if name == "list_reminders":
        return ["list", "--all"] if arguments.get("include_past") else ["list"]
    if name == "cancel_reminder":
        raw = str(arguments.get("id") or "").strip().lstrip("#")
        if not raw.isdigit():
            raise ValueError(f"the reminder id must be a number from list_reminders, not {arguments.get('id')!r}")
        return ["cancel", raw]
    raise LookupError(name)


def run(argv):
    try:
        done = subprocess.run([REMIND, "--json", *argv], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {"ok": False, "error": f"the reminder service is not installed here ({REMIND} is missing)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "the reminder store did not answer in time"}
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": f"remind failed: {(done.stderr or done.stdout).strip()[:300]}"}


def call(name, arguments):
    if name == "add_reminder" and (arguments or {}).get("task") and \
            os.environ.get("DIABLO_CONVERSATION", "").startswith("mail-task-"):
        return "not set: a turn started by a mail cannot schedule tasks of its own; ask Marcus"
    try:
        argv = _argv(name, arguments or {})
    except ValueError as error:
        return f"not set: {error}"
    reply = run(argv)
    if name in WRITES:
        _cache.clear()
    if not reply.get("ok"):
        return f"not set: {reply.get('error')}"
    return reply.get("message") or "done"


def upcoming():
    return _cache.get(_upcoming)


def _upcoming():
    reply = run(["list"])
    if not reply.get("ok"):
        return None
    return [row for row in reply.get("result") or [] if isinstance(row, dict)]


def panel(section):
    rows = upcoming()
    if rows is None:
        return [unavailable("The reminder service did not answer, so I cannot list or cancel reminders.",
                            title="Reminders", source="the reminder service")]
    items = [{
        "title": row.get("text") or "", "detail": row.get("next") or "",
        "trailing": "" if row.get("repeats") == "once" else row.get("repeats", ""),
        **({"actions": [{"label": "Cancel", "danger": True,
                         "act": {"module": ID, "action": "cancel", "args": {"id": row["id"]}}}]} if row.get("id") is not None else {}),
    } for row in rows[:8]]
    blocks = [{"kind": "list", "title": "Reminders", "icon": "alarm", "items": items,
               "empty": "Nothing is set.", "hint": "Ask me to remind you of something."}]
    blocks.append({"kind": "actions", "items": [{"label": "Remind me", "icon": "alarm", "say": "Remind me ", "send": False}]})
    return blocks


def act(action, args):
    if action == "cancel":
        said = call("cancel_reminder", {"id": args.get("id")})
        if said.startswith("not set"):
            raise ActFailed(said)
        return said
    raise ValueError(f"reminders has no action {action}")


def summary():
    rows = upcoming()
    if not rows:
        return None
    row = rows[0]
    try:
        due = dt.datetime.fromisoformat(str(row.get("next_local")))
    except ValueError:
        when = row.get("next") or ""
        return {"label": "Next reminder", "value": when, "icon": "alarm"}
    today = dt.date.today()
    if due.date() == today:
        label = "Today"
    elif due.date() == today + dt.timedelta(days=1):
        label = "Tomorrow"
    elif due.year == today.year:
        label = f"{due.strftime('%a')} {due.day} {due.strftime('%b')}"
    else:
        label = f"{due.day} {due.strftime('%b')} {due.year}"
    return {"label": label, "value": due.strftime("%H:%M"), "icon": "alarm"}
