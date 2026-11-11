"""Every turn the hub is running right now, and what it is doing in a word or two.

brains.ask registers each turn here, whatever started it (a spoken line, a typed message, Telegram,
a T3 watcher), and each tool step updates its words. Every change goes out as a `busy` event and
GET /v1/busy returns the same snapshot, so a screen that just connected knows at once; the orb's
feed asks it again every half minute. A turn older than MAX_AGE_S is dropped from the snapshot
even if its coroutine never ended, so nothing reads as busy forever. An event goes out only when
the words change, so a run of file reads is one event.
"""

import itertools
import time

from . import events

MAX_AGE_S = 30 * 60

running: dict = {}
_ids = itertools.count(1)

LAUNCH = {"t3_thread_launch", "create_threads", "delegate_task", "t3_thread_send"}
WORDS = {
    "WebSearch": "searching the web", "WebFetch": "reading the web", "Read": "reading files",
    "Glob": "looking through files", "Grep": "searching files", "Write": "writing files", "Edit": "editing files",
    "Task": "asking a helper", "Agent": "asking a helper", "ToolSearch": "finding a tool",
    "hue_light": "the lights", "lights": "the lights", "notify": "messaging you", "save_fact": "remembering",
    "recall_facts": "remembering", "add_reminder": "setting a reminder", "list_reminders": "checking reminders",
    "cancel_reminder": "cancelling a reminder", "watch_t3_thread": "watching T3", "update_task": "updating a task",
    "emit_object": "making a card", "ask_user": "asking you", "send_voice": "speaking",
    "brain_search": "searching the vault", "brain_read": "reading the vault", "brain_log": "writing the vault",
}


def words(label):
    """A step label (`name on host: detail`) in the words the cursor pill shows."""
    name, _, detail = str(label or "").partition(": ")
    name, _, host = name.partition(" on ")
    name = name.strip()
    if name in LAUNCH:
        return f"launching on {host}" if host else "launching on T3"
    if name.startswith("t3_"):
        return f"T3 on {host}" if host else "checking T3"
    if "mail" in name or "jorbis" in name:
        return "checking mail"
    if name in WORDS:
        return WORDS[name]
    plain = events.plain_step(label)
    if plain.startswith("ssh "):
        return f"on {plain[4:]}"
    if name in ("Bash", "run_shell"):
        return "running a command"
    return plain


def snapshot():
    now = time.time()
    for key in [key for key, turn in running.items() if now - turn["since"] >= MAX_AGE_S]:
        running.pop(key, None)
    turns = [dict(turn, id=key) for key, turn in running.items()]
    turns.sort(key=lambda turn: turn["since"])
    return {"kind": "busy", "turns": turns, "at": now}


def _publish():
    events.publish(snapshot())


def start(conv, origin):
    key = next(_ids)
    running[key] = {"conv": conv, "origin": str(origin or ""), "since": time.time(), "words": "thinking", "step": ""}
    _publish()
    return key


def step(key, label):
    turn = running.get(key)
    if turn is None:
        return
    turn["step"] = str(label or "")[:120]
    said = words(label)
    if said != turn["words"]:
        turn["words"] = said
        _publish()


def end(key):
    if running.pop(key, None) is not None:
        _publish()
