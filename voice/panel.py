"""The central card: Diablo opened up in the middle of the screen (presence/Panel.qml).

Quick actions, no model turn: "open up" or "show your settings" opens it on Settings, "enlarge
this" moves the newest card into it, "close" or "make it small again" puts him back by the
cursor. presence/shell.qml reads panel.json and writes it back when the card is closed there.
"""

import json
import re
import time

from . import presence

FILE = presence.STATE_DIR / "panel.json"

_LEAD = r"^\W*(?:diablo\W+)?(?:(?:please|can\s+you|could\s+you|would\s+you)\W+)?"
_TAIL = r"(?:\W+(?:please|for\s+me|now))*\W*$"
OPEN = re.compile(
    _LEAD + r"(?:open\s+(?:up|yourself|your\s+settings|the\s+settings|settings)|expand(?:\s+yourself)?"
    r"|show\s+(?:me\s+)?(?:your|the)\s+settings|come\s+to\s+the\s+(?:middle|centre|center)|go\s+big)" + _TAIL,
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
    """open, enlarge or close for a line that asks for one, else None. "Close it" and "make it
    bigger" mean the window to every other command, so they only count here while the central
    card is open or a card is on screen. "Expand" alone opens him up; "expand it" enlarges."""
    text = str(text or "")
    state = read() if state is None else state
    cards = presence._read() if cards is None else cards
    if ENLARGE.match(text) and not re.search(r"\bexpand(?:\s+yourself)?\W*$", text, re.I):
        return "enlarge" if cards or state.get("card") else None
    if OPEN.match(text):
        return "open"
    if CLOSE.match(text):
        return "close" if state.get("open") else None
    return None


def read():
    try:
        state = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {"open": False, "view": "settings", "card": None}
    return state if isinstance(state, dict) else {"open": False, "view": "settings", "card": None}


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
        write(open=True, view="settings")
        return ""
    if kind == "enlarge":
        card = newest_card()
        if card is None:
            state = read()
            if state.get("card"):
                write(open=True, view="now")
                return ""
            return "There is nothing on screen to enlarge, sir."
        write(open=True, view="now", card=card)
        return ""
    if kind == "close":
        if not read().get("open"):
            return ""
        write(open=False)
        return ""
    return None
