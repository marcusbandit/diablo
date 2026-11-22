"""The central card: Diablo opened up in the middle of the screen (presence/Panel.qml).

Quick actions, no model turn: "open up" or "go to your settings" opens it on Settings, "train
your voice" on Settings at Your voice, "show my history" on History, "show my tasks" on Tasks
(the task cards, a part of Now), "enlarge this" moves the
newest card into it, "close" or "make it small again" puts him back by the cursor.
presence/shell.qml reads panel.json and writes it back when the card is closed there. `page` in
panel.json asks the card to scroll Settings to the top ("settings") or to Your voice ("voice");
shell.qml clears it once a tab is clicked.
"""

import json
import re
import time

from . import presence

FILE = presence.STATE_DIR / "panel.json"
SHUT = {"open": False, "view": "settings", "card": None, "page": ""}
PAGES = ("settings", "voice")

_LEAD = r"^\W*(?:diablo\W+)?(?:(?:please|can\s+you|could\s+you|would\s+you)\W+)?"
_TAIL = r"(?:\W+(?:please|for\s+me|now))*\W*$"
OPEN = re.compile(
    _LEAD + r"(?:open\s+(?:up|yourself)|expand(?:\s+yourself)?|come\s+to\s+the\s+(?:middle|centre|center)|go\s+big)" + _TAIL,
    re.I,
)
SETTINGS = re.compile(
    _LEAD + r"(?:i\s+(?:want|would\s+like|wanna)\s+(?:to\s+)?)?"
    r"(?:(?:open|show)\s+(?:me\s+)?(?:(?:your|the|diablo'?s)\s+)?settings"
    r"|(?:go\s+to|take\s+me\s+to|bring\s+up|pull\s+up|let\s+me\s+see)\s+(?:your|diablo'?s)\s+settings)" + _TAIL,
    re.I,
)
TRAIN = re.compile(
    _LEAD + r"(?:(?:re)?train\s+(?:your|the)\s+(?:voice|ears|wake\s+word)(?:\s+(?:model|again))?"
    r"|(?:re)?teach\s+(?:you|yourself|him|diablo)\s+my\s+voice(?:\s+again)?"
    r"|(?:re)?train\s+(?:you|yourself|him|diablo)\s+(?:on|to)\s+(?:my\s+voice|recogni[sz]e\s+me)"
    r"|learn\s+my\s+voice(?:\s+again)?)" + _TAIL,
    re.I,
)
HISTORY = re.compile(
    _LEAD + r"(?:(?:open|show|bring\s+up|pull\s+up|go\s+to)\s+(?:me\s+)?(?:up\s+)?(?:my|our|the|your)?\s*"
    r"(?:(?:whole|full|chat|conversation|message)\s+)?(?:history|chat\s+log|conversation\s+log)"
    r"|(?:open|show)\s+(?:me\s+)?(?:everything|all)\s+(?:i(?:'ve|\s+have)?|we(?:'ve|\s+have)?)\s+(?:said|talked\s+about))"
    r"(?:\s+(?:with\s+you|with\s+diablo|to\s+you))?" + _TAIL,
    re.I,
)
TASKS = re.compile(
    _LEAD + r"(?:(?:open|show|bring\s+up|pull\s+up|go\s+to|let\s+me\s+see)\s+(?:me\s+)?(?:up\s+)?(?:my|our|the|your|diablo'?s)?\s*"
    r"(?:(?:running|background|open|live)\s+)?(?:tasks|task\s+list|task\s+cards|jobs))" + _TAIL,
    re.I,
)
ENLARGE = re.compile(
    _LEAD + r"(?:enlarge|expand|zoom\s+in\s+on|blow\s+up|make\s+(?:it|this|that)\s+(?:big|bigger|larger)"
    r"|show\s+(?:it|this|that)\s+(?:big|bigger|larger))(?:\s+(?:it|this|that|the\s+card))?" + _TAIL,
    re.I,
)
CLOSE = re.compile(
    _LEAD + r"(?:close(?:\s+(?:it|this|that|up|yourself|the\s+card|settings|your\s+settings))?|shrink(?:\s+(?:it|this|back|yourself))?"
    r"|make\s+(?:it|this|that|yourself)\s+small(?:er)?(?:\s+again)?|go\s+(?:back\s+)?small|put\s+(?:it|this|that)\s+away)" + _TAIL,
    re.I,
)


def intent(text, state=None, cards=None):
    """open, settings, voice, history, tasks, enlarge or close for a line that asks for one, else None.
    "Close it" and "make it bigger" mean the window to every other command, so they only count
    here while the central card is open or a card is on screen. "Expand" alone opens him up;
    "expand it" enlarges."""
    text = str(text or "")
    state = read() if state is None else state
    cards = presence._read() if cards is None else cards
    if HISTORY.match(text):
        return "history"
    if TASKS.match(text):
        return "tasks"
    if ENLARGE.match(text) and not re.search(r"\bexpand(?:\s+yourself)?\W*$", text, re.I):
        return "enlarge" if cards or state.get("card") else None
    if TRAIN.match(text):
        return "voice"
    if SETTINGS.match(text):
        return "settings"
    if OPEN.match(text):
        return "open"
    if CLOSE.match(text):
        return "close" if state.get("open") else None
    return None


def read():
    try:
        state = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return dict(SHUT)
    return state if isinstance(state, dict) else dict(SHUT)


def write(**changes):
    state = {**read(), **changes, "at": time.time()}
    try:
        presence.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(FILE)
    except OSError:
        pass
    return state


def newest_card():
    cards = presence._read()
    return cards[-1] if cards else None


def apply(kind):
    """Does it and returns his words: short, the screen shows the rest."""
    if kind == "open":
        write(open=True, view="settings", page="")
        return ""
    if kind in PAGES:
        write(open=True, view="settings", page=kind, page_at=time.time())
        return ""
    if kind == "history":
        write(open=True, view="history", page="")
        return ""
    if kind == "tasks":
        write(open=True, view="tasks", page="")
        return ""
    if kind == "enlarge":
        card = newest_card()
        if card is None:
            state = read()
            if state.get("card"):
                write(open=True, view="now", page="")
                return ""
            return "There is nothing on screen to enlarge, sir."
        write(open=True, view="now", card=card, page="")
        return ""
    if kind == "close":
        if not read().get("open"):
            return ""
        write(open=False, page="")
        return ""
    return None
