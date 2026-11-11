"""What Diablo shows on screen: cards for the orb (presence/, a Quickshell instance) to draw.

The orb reads ~/.local/state/diablo/presence.json and his state from ears.json; this module
writes the cards. A card is {id, kind, title, big, lines, at, ttl}: a short title, one big
figure, and a few lines, gone after ttl seconds or a click.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "diablo"
FILE = STATE_DIR / "presence.json"
KEEP = 3
TTL_S = 10.0
_lock = threading.Lock()


def _read():
    try:
        cards = json.loads(FILE.read_text()).get("cards") or []
    except (OSError, ValueError, AttributeError):
        return []
    now = time.time()
    return [c for c in cards if isinstance(c, dict) and now < float(c.get("at") or 0) + float(c.get("ttl") or TTL_S)]


def show(title="", big="", lines=(), kind="text", ttl=TTL_S, video=None):
    """Puts a card beside the orb. Returns its id. With `video` (a file path) the card plays
    it once and offers copy, save and open."""
    card = {"id": uuid.uuid4().hex[:10], "kind": kind, "title": str(title or "")[:60], "big": str(big or "")[:24],
            "lines": [str(line)[:240] for line in lines if str(line).strip()][:6], "at": time.time(), "ttl": float(ttl)}
    if video:
        card["video"] = str(video)
    with _lock:
        cards = (_read() + [card])[-KEEP:]
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cards": cards}))
            tmp.replace(FILE)
        except OSError:
            pass
    return card["id"]


def show_reply(words, ttl=None):
    """What he says, to read as well: a card that stays about as long as it takes to say."""
    words = " ".join(str(words or "").split())
    if not words:
        return None
    seconds = ttl if ttl is not None else max(TTL_S, min(40.0, len(words) / 14 + 6))
    return show("Diablo", "", [words[:600]], kind="reply", ttl=seconds)
