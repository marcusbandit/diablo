import re
import threading

import httpx

from . import settings, usage

# One kept-open connection for every Jev call: a fresh one per call cost about 80 ms of
# handshake (median 274 ms against 195 ms kept, 8 calls each, 2026-11-11).
_client = None
_client_lock = threading.Lock()


def _http():
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=10, limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120))
        return _client


def warm():
    """Opens the connection ahead of the first call, so that one pays no handshake either."""
    try:
        _http().head("https://openrouter.ai/api/v1/models", timeout=5)
    except httpx.HTTPError:
        pass


THREAD_WORDS = re.compile(r"\b(thread|threads|chat|conversation|t3)\b", re.I)


def wants_threads(text, desktop):
    """Whether the thread question is worth Jev's small input: the line names a thread or a
    chat, or T3 Code has the focus."""
    if THREAD_WORDS.search(str(text or "")):
        return True
    windows = (desktop or {}).get("windows") or []
    focused = next((w for w in windows if isinstance(w, dict) and w.get("focused")), {})
    return "t3" in str(focused.get("class") or "").lower()

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "~typesafe/jev-latest"
DEFAULT_THRESHOLD = 0.7


def _key():
    key = settings.get("provider.openrouter.api_key")
    return str(key or "").strip()


def route(text, workspaces, threshold=None):
    """One batched jev call: where does this line belong, what kind of work is it.

    Returns {"workspace": id-or-"new", "tool_family": str, "confidence": float,
    "reason": str}. Fails open to the stream: jev never blocks a message.
    """
    threshold = threshold or float(settings.get("jev_threshold") or DEFAULT_THRESHOLD)
    key = _key()
    rows = [
        (w["id"], c)
        for w in workspaces
        for c in w["chats"]
    ]
    if not key or not rows:
        return {"workspace": "new", "tool_family": "other", "confidence": 1.0, "reason": "no jev key or no workspaces"}
    import time

    now = time.time()
    lines = []
    for ws_id, c in rows:
        age = int((now - (c["last_touched"] or now)) / 60)
        lines.append(
            f"workspace {ws_id}: chat {c['chat_id']} ({c['title']}), state {c['state']}, last touched {age} minutes ago"
        )
    state = (
        "The user typed one line in a stream. His workspaces and their state:\n"
        + "\n".join(lines)
        + "\n\nThe line: "
        + text
    )
    questions = {
        "workspace": {
            "type": "choice",
            "instructions": (
                "Which workspace does this line belong to, weighing how recently each"
                " was touched. A vague reference like 'the button' or 'make it bigger'"
                " with a stale trail of hours is NOT enough to file it anywhere; a"
                " clear mention of the workspace, its subject or its work is."
            ),
            "criteria": {
                **{
                    w["id"]: f"{w['name']}: "
                    + ", ".join(f"chat {c['chat_id']} ({c['title']}), state {c['state']}" for c in w["chats"])
                    for w in workspaces
                },
                "new": "none of these; the line is fresh talk or a brand new thing",
            },
        },
        "tool_family": {
            "type": "choice",
            "instructions": "What kind of work is this line",
            "criteria": {
                "build": "making or changing something in a project or app",
                "research": "reading, finding out, understanding something",
                "house": "the flat: lights, music, physical things",
                "question": "just asking, wants words back",
                "other": "anything else",
            },
        },
    }
    try:
        try:
            r = _http().post(
                DECISIONS_URL,
                json={"model": JEV_MODEL, "state": state, "questions": questions},
                headers={"Authorization": f"Bearer {key}"},
                timeout=12,
            )
        except httpx.HTTPError:
            usage.record("openrouter", JEV_MODEL, origin="jev", ok=False)
            raise
        usage.record("openrouter", JEV_MODEL, origin="jev", ok=r.is_success)
        r.raise_for_status()
        answers = r.json().get("answers", {})
        ws_answer = answers.get("workspace", {})
        family = answers.get("tool_family", {})
        return {
            "workspace": str(ws_answer.get("choice") or "new"),
            "tool_family": str(family.get("choice") or "other"),
            "confidence": float(ws_answer.get("confidence") or 0.0),
            "reason": "jev",
        }
    except Exception:
        return {"workspace": "new", "tool_family": "other", "confidence": 0.0, "reason": "jev unreachable, staying in the stream"}



TIER_CRITERIA = {
    "fast": "chit-chat, thanks, a greeting, or one simple light command",
    "voice": "a quick lookup, status check, reminder or short question",
    "standard": "mail or calendar, math, an explanation, an order, a longer question",
    "chat": "fix, build or change something, or a hard problem; needs real thinking",
    "deep": "a long multi-step build or in-depth research",
}
ACTION_CRITERIA = {
    "lights_on": "switch lights on",
    "lights_off": "switch lights off",
    "dim": "make lights darker or dimmer, or less bright",
    "brighten": "make lights brighter",
    "color": "give lights a named color",
    "warmer": "make the light warmer, more yellow or orange white",
    "cooler": "make the light cooler, colder, bluer white",
    "again": "do the last light change once more",
    "undo": "put the lights back how they were before the last change",
    "none": "anything else: a question, several different things, or not about lights",
}
TARGET_CRITERIA = {
    "all": "all the lights, or the room as a whole",
    "corner lamp": "the corner lamp, the left lamp",
    "ceiling panel": "the ceiling panel, the top light, the room light",
    "wood lamp": "the wood lamp, the right lamp",
    "last": "it, that, the one just used: the lamp in the most recent event",
}
AMOUNT_CRITERIA = {
    "small": "a bit, a little, slightly",
    "normal": "no amount given",
    "large": "a lot, much, way more, a ton",
}
WORK_CRITERIA = {
    "none": "not work for a coding agent: chat, a lookup, the lights, mail, a question Diablo can answer himself",
    "small_change": "a small fix or tweak to an existing app or project",
    "app_update": "update, rebuild or release an existing app",
    "big_work": "a larger feature, a redesign or several steps in an existing project",
    "new_project": "start a new app or project from scratch",
    "no_project": "a one-off script, experiment or in-depth research that needs a worker but no project",
}
COLOR_CRITERIA = {name: name for name in ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "magenta", "white")}
COLOR_CRITERIA["none"] = "no color named"

# Voice from banditbox carries the desktop as it was at the wake, so jev can pick a plain
# desktop command in the same call and the ears run it there, with no model in between.
DESKTOP_CRITERIA = {
    "none": "anything else: a question, the lights, several things at once, or more than one plain desktop command",
    "close_window": "close, quit or kill a window or an app",
    "reopen_window": "reopen, open it again, bring back the window or app that was just closed",
    "focus_window": "switch to, show me, bring up, go to or open a window or app that is already open",
    "workspace": "go to or switch to a workspace by its number",
    "move_to_workspace": "move the focused window to a workspace by its number",
    "play": "play, resume or unpause music or a video, or play one of the open players by name, like play Spotify",
    "pause": "pause or stop music or a video, music off, stop the music, silence the song",
    "next": "next song or track, skip this one",
    "previous": "previous song or track, the one before",
    "volume_up": "louder, turn the sound up, volume up",
    "volume_down": "quieter, turn the sound down, volume down",
    "mute": "mute the sound",
    "unmute": "unmute the sound, sound back on",
    "again": "again, more, once more, a bit more, keep going, do that again: repeat what was just done",
    "open_thread": "go to, open or show a T3 Code thread or chat, named or described by what it is about",
    "focus_left": "go left, the window to the left, previous window",
    "focus_right": "go right, the window to the right, next window",
    "focus_up": "go up, the window above",
    "focus_down": "go down, the window below",
    "fullscreen": "fullscreen this window, or leave fullscreen",
    "new_window": "a new window of this app",
    "terminal": "open a terminal, new terminal",
    "enter": "press enter, send it, submit, confirm",
    "escape": "press escape, cancel, get out of this",
    "tab": "press tab, next field",
    "backspace": "backspace, delete the last character",
    "delete_word": "delete the last word",
    "copy": "copy, copy that, copy this",
    "paste": "paste",
    "cut": "cut",
    "undo": "undo that, go back, take that back, put it back how it was",
    "redo": "redo",
    "select_all": "select all",
    "save": "save",
    "find": "find, search on this page",
    "new_tab": "new tab",
    "close_tab": "close this tab",
    "next_tab": "next tab",
    "previous_tab": "previous tab",
    "reopen_tab": "reopen the tab just closed",
    "reload": "reload or refresh the page",
    "scroll_up": "scroll up",
    "scroll_down": "scroll down",
    "page_up": "page up",
    "page_down": "page down",
    "top": "go to the top",
    "bottom": "go to the bottom",
    "click": "click, click this, click here",
    "double_click": "double click",
    "right_click": "right click",
}
WORKSPACE_CRITERIA = {str(n): f"workspace {n}" for n in range(1, 11)}
WORKSPACE_CRITERIA["none"] = "no workspace number said"
WINDOWS_MAX = 15
PLAYERS_MAX = 8


def desktop_questions(desktop):
    """The extra jev questions for a line spoken at banditbox: the command and its target.

    desktop is what the ears saw at the wake: {"windows": [{address, class, title,
    workspace, focused}], "players": [names]}. Window choices are w1, w2, ... in order.
    """
    windows = [w for w in (desktop or {}).get("windows") or [] if isinstance(w, dict)][:WINDOWS_MAX]
    window_criteria = {"focused": "this window, this app, it, or no window named"}
    for index, w in enumerate(windows):
        label = f"{w.get('class') or '?'}: {' '.join(str(w.get('title') or '').split())[:80]}"
        if w.get("focused"):
            label += " (focused now)"
        window_criteria[f"w{index + 1}"] = label
    player_criteria = {"any": "no player named"}
    for name in [str(p) for p in (desktop or {}).get("players") or [] if p][:PLAYERS_MAX]:
        player_criteria[name] = name.split(".")[0]
    threads = [t for t in (desktop or {}).get("threads") or [] if isinstance(t, dict)]
    extra = {}
    if threads:
        thread_criteria = {"none": "no thread named or described"}
        for index, t in enumerate(threads):
            thread_criteria[f"t{index + 1}"] = f"{t.get('title')} ({t.get('status')})"
        extra["thread"] = {"type": "choice", "criteria": thread_criteria, "instructions": (
            "Which T3 Code thread the new line means, by its title or what it is about; "
            "working means running")}
    return extra | {
        "desktop": {"type": "choice", "criteria": DESKTOP_CRITERIA, "instructions": (
            "Is the new line one plain command for his computer that Diablo can do at once without thinking")},
        "window": {"type": "choice", "criteria": window_criteria,
                   "instructions": "Which open window or app the new line means"},
        "workspace": {"type": "choice", "criteria": WORKSPACE_CRITERIA,
                      "instructions": "The workspace number the new line says, if any"},
        "player": {"type": "choice", "criteria": player_criteria,
                   "instructions": "The music or video player the new line names, if any"},
    }


# Whisper over music writes "play Spotify" as "Chase, Spotify." (2026-11-13). Told how the
# line was heard and whether each player plays, Jev reads that as play: 0.45 to 0.71 in a probe.
HEARD = ("He said it out loud and Whisper wrote it down, often over music, so a word can come out wrong:"
         " a short line naming a player or the music with one odd word in front (Chase, Pulse, Pace, Lay,"
         " Place, Paws) is almost always play or pause.")


def desktop_note(desktop):
    """How the line was heard and what each player is doing, for Jev's state."""
    status = (desktop or {}).get("status") or {}
    playing = [f"{str(name).split('.')[0]} is {state}" for name, state in status.items()
               if isinstance(state, str) and state][:PLAYERS_MAX]
    return HEARD + (" Players now: " + ", ".join(playing) + "." if playing else "")


def refers_criteria(labels):
    criteria = {"none": "a fresh line, not about anything on the card"}
    for label in labels:
        criteria[label] = f"about event {label} on the card"
    criteria["earlier"] = ("about something said or done before the card or not on it, like "
                           "'that document we talked about earlier': Diablo needs more context")
    return criteria


RUNNING_MAX = 4


def after_question(running):
    """For a line said while Diablo is still on earlier requests: does it build on one of them
    and so wait for it, or is it its own thing that can start at once ("separate")?"""
    running = [" ".join(str(r).split())[:160] for r in running if str(r).strip()][-RUNNING_MAX:]
    if not running:
        return None
    criteria = {"separate": ("a new thing of its own that does not need any of these to finish first, "
                             "even if it is the same kind of task, like opening a different program")}
    for index, said in enumerate(running):
        criteria[f"r{index + 1}"] = (f"builds on or points back to this one, which is still running: \"{said}\". "
                                     "It, that, there, the program, the result: it has to wait for this one")
    return {"after": {"type": "choice", "criteria": criteria, "instructions": (
        "Diablo is still working on earlier requests. Must the new line wait for one of them, "
        "or can it start at once")}}


def control(text, desktop, timeout=None):
    """Jev on a speech control line: only the desktop questions, so the input stays small.

    Returns the choices with confidences and "ms", or None when Jev has no key, is slow or
    fails. Nothing is written to any conversation.
    """
    key = _key()
    if not key or not str(text or "").strip():
        return None
    questions = desktop_questions(desktop)
    recent = [" ".join(str(r).split())[:90] for r in (desktop or {}).get("recent") or [] if str(r).strip()][-4:]
    state = "Marcus is driving his computer by voice. " + desktop_note(desktop)
    if recent:
        state += " Just before this, Diablo did, oldest first: " + "; ".join(recent) + "."
    state += " New line he said: " + str(text)[:600]
    return ask(state, questions, key, timeout, origin="jev control")


def ask(state, questions, key, timeout=None, origin="jev"):
    """One Jev call: the choices with confidences and "ms", or None when it fails or is slow."""
    import time

    started = time.time()
    try:
        try:
            r = _http().post(
                DECISIONS_URL,
                json={"model": JEV_MODEL, "state": state, "questions": questions},
                headers={"Authorization": f"Bearer {key}"},
                timeout=timeout or float(settings.get("jev.timeout") or 2.5),
            )
        except httpx.HTTPError:
            usage.record("openrouter", JEV_MODEL, origin=origin, ok=False)
            raise
        usage.record("openrouter", JEV_MODEL, origin=origin, ok=r.is_success)
        r.raise_for_status()
        answers = r.json().get("answers", {})
    except Exception:
        return None
    out = {"ms": round((time.time() - started) * 1000)}
    for name in questions:
        answer = answers.get(name) or {}
        out[name] = str(answer.get("choice") or "")
        out[f"{name}_confidence"] = float(answer.get("confidence") or 0.0)
    return out


def decide(text, card="", labels=(), timeout=None, extra=None):
    """One jev call for a message: tier, a direct light action if one fits, and its details.

    card is the short context from hub.context (room state and recent events with
    their age), so "dim it a bit" can be read against the last thing he did.
    Returns a dict of choices with confidences and "ms", or None when jev has no
    key, is slow or fails.
    """
    key = _key()
    if not key or not str(text or "").strip():
        return None

    questions = {
        "tier": {"type": "choice", "instructions": "How much brain does Diablo need for this new line", "criteria": TIER_CRITERIA},
        "action": {"type": "choice", "instructions": "Is the new line one direct light change Diablo can do without thinking", "criteria": ACTION_CRITERIA},
        "target": {"type": "choice", "instructions": "Which lights the new line means, using the recent events for it or that", "criteria": TARGET_CRITERIA},
        "amount": {"type": "choice", "instructions": "How big a change the new line asks for", "criteria": AMOUNT_CRITERIA},
        "color": {"type": "choice", "instructions": "The color the new line names, if any", "criteria": COLOR_CRITERIA},
        "work": {"type": "choice", "instructions": "Is this work to hand to a coding agent in T3 Code, and what kind", "criteria": WORK_CRITERIA},
        "refers_to": {"type": "choice", "instructions": (
            "Which earlier event the new line is about. Weigh the age: a minute ago is fresh, hours ago "
            "only counts when he clearly points back to it"), "criteria": refers_criteria(labels)},
    }
    questions.update(extra or {})
    state = (card.strip() + "\n\n" if card else "") + "New line Marcus said to his assistant Diablo: " + str(text)[:1200]
    out = ask(state, questions, key, timeout)
    if out is None:
        return None
    if out["tier"] not in TIER_CRITERIA:
        return None
    return out
