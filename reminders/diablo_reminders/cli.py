"""remind: add, list and cancel Diablo's reminders, and run the scheduler.

  remind add 2026-11-08T13:00 "Time to go home and make dinner"
  remind add 08:30 "Stand up" --repeat weekdays
  remind add +20m "Check the oven"
  remind list [--all]
  remind cancel 3
  remind run          the scheduler loop, run by diablo-reminders.service
"""

import argparse
import json
import logging
import sys

from . import schedule
from .store import Store, now_utc


def _view(row, store, now):
    view = {
        "id": row["id"],
        "text": row["text"],
        "status": row["status"],
        "recurrence": row["recurrence"],
        "repeats": schedule.describe_recurrence(row["recurrence"], schedule.parse_wall(row["anchor_local"])),
        "next_local": row["next_local"],
        "next_due_utc": None,
        "next": None,
        "timezone": row["tz"],
        "kind": row["kind"],
    }
    if row["next_local"]:
        wall = schedule.parse_wall(row["next_local"])
        view["next"] = schedule.describe(wall, now)
        view["next_due_utc"] = schedule.to_utc(wall).strftime("%Y-%m-%dT%H:%M:%SZ")
    last = store.last_delivery(row["id"])
    if last:
        view["last_delivery"] = {
            "due_local": last["due_local"],
            "status": last["status"],
            "late": bool(last["late"]),
            "attempts": last["attempts"],
            "error": last["last_error"],
        }
    return view


def _line(view):
    if view["status"] == "active":
        when = view["next"]
        repeat = "" if view["recurrence"] == "none" else f", {view['repeats']}"
        task = "task, " if view.get("kind") == "task" else ""
        return f"#{view['id']} {task}{when}{repeat}: {view['text']}"
    last = view.get("last_delivery") or {}
    detail = view["status"]
    if last:
        detail += f", last delivery {last['status']}"
        if last.get("late"):
            detail += " late"
    return f"#{view['id']} {detail}: {view['text']}"


def cmd_add(args, store, now):
    wall = schedule.parse_due(args.due, now)
    row = store.add(" ".join(args.text), wall, args.repeat, source=args.source, now=now,
                    kind="task" if args.task else "remind")
    view = _view(row, store, now)
    repeat = "once" if row["recurrence"] == "none" else view["repeats"]
    noun = "Task" if args.task else "Reminder"
    message = f"{noun} #{row['id']} set for {schedule.describe(schedule.parse_wall(row['next_local']), now, with_year=True)} (Copenhagen time), {repeat}: {row['text']}"
    return view, message


def cmd_list(args, store, now):
    views = [_view(row, store, now) for row in store.list(include_past=args.all)]
    if not views:
        return views, "No reminders are set." if not args.all else "No reminders, past or present."
    return views, "\n".join(_line(view) for view in views)


def cmd_cancel(args, store, now):
    before = store.get(args.id)
    if before is None:
        raise ValueError(f"there is no reminder #{args.id}")
    row = store.cancel(args.id, now=now)
    view = _view(row, store, now)
    if before["status"] != "active":
        return view, f"Reminder #{args.id} was already {before['status']}: {row['text']}"
    return view, f"Reminder #{args.id} cancelled: {row['text']}"


def parser():
    top = argparse.ArgumentParser(prog="remind", description="Diablo's durable reminders, delivered on Telegram.")
    top.add_argument("--db", help="reminders database (default $REMIND_DB or ~/.local/state/diablo/reminders.db)")
    top.add_argument("--json", action="store_true", help="print JSON instead of text")
    sub = top.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="set a reminder")
    add.add_argument("due", help="2026-11-08T13:00, 13:00, or +20m; Copenhagen time unless an offset is given")
    add.add_argument("text", nargs="+", help="what to remind about")
    add.add_argument("--repeat", "-r", default="none", choices=schedule.RECURRENCES, help="recurrence, default none")
    add.add_argument("--task", "-t", action="store_true", help="Diablo does this himself at that time instead of reminding")
    add.add_argument("--source", default="cli", help=argparse.SUPPRESS)
    listing = sub.add_parser("list", help="show reminders")
    listing.add_argument("--all", "-a", action="store_true", help="include done and cancelled ones")
    cancel = sub.add_parser("cancel", help="cancel a reminder by id")
    cancel.add_argument("id", type=int)
    sub.add_parser("run", help="run the scheduler loop")
    return top


COMMANDS = {"add": cmd_add, "list": cmd_list, "cancel": cmd_cancel}


def main(argv=None, out=None):
    out = out or sys.stdout
    args = parser().parse_args(argv)
    if args.command == "run":
        from . import scheduler

        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
        return scheduler.run(args.db)
    store = Store(args.db)
    try:
        now = now_utc()
        try:
            data, message = COMMANDS[args.command](args, store, now)
        except ValueError as error:
            if args.json:
                print(json.dumps({"ok": False, "error": str(error)}), file=out)
            else:
                print(f"remind: {error}", file=sys.stderr)
            return 2
    finally:
        store.close()
    if args.json:
        print(json.dumps({"ok": True, "result": data, "message": message}, ensure_ascii=False), file=out)
    else:
        print(message, file=out)
    return 0
