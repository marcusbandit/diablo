"""The options of a question drawn on his desktop as cards, one per option, while it is read out.

When ask_user is asked on a turn from his desk (spoken there or sent from the desktop app there), the
question still goes to every app, Telegram and the spoken turn as before, and the same options also
come up over the real screen in the hand-drawn look of draw_diagram: the question written as a
title, then one card per option with its number, label and description, the recommended one
marked. Each piece is its own beat of a marks show with `keep`, paced by the line that names it,
so the cards come in one by one as he says them. A question answered, withdrawn or expired takes
its cards down, unless another show has replaced them since.

plan() needs only diagram and pointing, so the preview can lay the sample out without the hub.
"""

import logging
import threading
import uuid

from . import diagram, pointing

logger = logging.getLogger(__name__)

TITLE = 60
LABEL = 48
DETAIL = 140
LINGER = pointing.MAX_LINGER
FONT = 20
CARD_W = 400
MIN_W = 300
PAD = 18
HEAD_H = 66
LINE_H = 25
CHAR = 0.45
GAP = 40
SPOKEN = ("One", "Two", "Three", "Four")

_lock = threading.Lock()
_current = {"show": "", "question": ""}


def from_desk(question):
    """Whether the turn that asked came from his desk: spoken at the desk machine, or sent from the
    desktop app there. Unknown is no."""
    from . import lockguard

    origin = str(question.get("origin") or "")
    try:
        kind = lockguard.kind_of(origin)
        if kind == "voice":
            return lockguard.host_of(origin).lower() == lockguard.host().lower()
        return kind == "desktop"
    except Exception:
        return False


def title_of(question):
    text = diagram.words(question.get("question"), 300)
    if len(text) <= TITLE:
        return text
    return diagram.words(question.get("header"), TITLE) or text[: TITLE - 3].rstrip() + "..."


def lines_of(text, width):
    """About how many lines a description takes in a card of that width, at most four."""
    if not text:
        return 0
    per_line = max(1, int((width - 2 * PAD) / (FONT * 1.2 * CHAR)))
    return min(4, -(-len(text) // per_line))


def height_for(details, width):
    """The card's height: its number and label, then room for the longest description."""
    lines = max([lines_of(d, width) for d in details] or [0])
    return round(HEAD_H + lines * LINE_H + PAD, 1)


def layout(count, region, details=()):
    """Each card's box: one row while every card keeps MIN_W, else two columns, all as tall as
    the longest description needs."""
    cols = count if count * MIN_W + GAP * (count - 1) <= region["w"] else 2
    rows = -(-count // cols)
    w = min(CARD_W, (region["w"] - GAP * (cols - 1)) / cols)
    h = height_for(details, w)
    total_w = w * cols + GAP * (cols - 1)
    total_h = h * rows + GAP * (rows - 1)
    x0 = region["x"] + (region["w"] - total_w) / 2
    y0 = region["y"] + (region["h"] - total_h) / 2
    boxes = []
    for i in range(count):
        row, col = divmod(i, cols)
        in_row = min(cols, count - row * cols)
        shift = (cols - in_row) * (w + GAP) / 2
        boxes.append({"x": round(x0 + shift + col * (w + GAP), 1), "y": round(y0 + row * (h + GAP), 1),
                      "w": round(w, 1), "h": round(h, 1)})
    return boxes, round(y0, 1)


def plan(question, screen=None, show=None):
    """The beats to send for a question: the title first, then one card per option."""
    options = [o for o in question.get("options") or [] if isinstance(o, dict) and o.get("label")][:len(SPOKEN)]
    if len(options) < 2:
        return []
    region = diagram.region_of(screen)
    boxes, top = layout(len(options), region, [diagram.words(o.get("description"), DETAIL) for o in options])
    title = title_of(question)
    show = show or "ch" + uuid.uuid4().hex[:10]
    recommended = question.get("recommended")
    count = len(options) + 1
    head_w = len(title) * diagram.TITLE_FONT * 0.56 + 40
    head = {"x": round(region["x"] + region["w"] / 2 - head_w / 2, 1), "y": round(top - diagram.TITLE_FONT - 64, 1),
            "w": round(head_w, 1), "h": round(diagram.TITLE_FONT + 20, 1)}
    beats = [{"show": show, "n": 0, "count": count, "say": title,
              "marks": [{"kind": "title", "text": title, "box": head, "size": diagram.TITLE_FONT}], "keep": True, "linger": LINGER}]
    for i, (option, box) in enumerate(zip(options, boxes)):
        card = {"kind": "choice", "text": diagram.words(option["label"], LABEL), "box": box, "size": FONT, "num": i + 1,
                "pick": recommended == i}
        detail = diagram.words(option.get("description"), DETAIL)
        if detail:
            card["detail"] = detail
        beats.append({"show": show, "n": i + 1, "count": count, "say": f"{SPOKEN[i]}, {card['text']}.", "marks": [card],
                      "keep": True, "linger": LINGER})
    return beats


def send(beats, publish=None):
    """Every beat cleaned first, then all of them published, or none when one is refused."""
    from . import events

    cleaned = [pointing.clean_payload(beat) for beat in beats]
    refused = next((c for c in cleaned if isinstance(c, str)), None)
    if refused:
        logger.info("choice beats refused: %s", refused)
        return False
    for beat in cleaned:
        (publish or events.publish)({"kind": "screen", "marks": beat})
    return True


def mine(question_id):
    with _lock:
        return bool(question_id) and _current["question"] == question_id


def draw(question, screen_of=None, publish=None):
    """Lays the question's cards out on the focused monitor and sends them, as long as the question
    is still the one in flight: reading the monitor takes an ssh round trip, and an answer or a
    newer question in the meantime means nothing is drawn."""
    from . import events

    publish = publish or events.publish
    if screen_of is None:
        from .mcp_server import desktop_screen as screen_of
    try:
        screen = screen_of()
    except Exception:
        screen = None
    beats = plan(question, screen)
    if not beats or not mine(question["id"]):
        return
    if not send(beats, publish):
        return
    with _lock:
        if _current["question"] == question["id"]:
            _current["show"] = beats[0]["show"]
            return
    publish({"kind": "screen", "marks": {"clear": True}})


def on_question(question, start=None, publish=None):
    """questions listener, called on every change: a pending question asked from his desk is drawn
    as cards beside the apps, once, and its cards come down once it is answered, withdrawn or gone."""
    if question.get("state") != "pending":
        settle(question, publish)
        return
    if question.get("action") or not from_desk(question):
        return
    with _lock:
        if _current["question"] == question.get("id"):
            return
        _current.update(question=question["id"], show="")
    start = start or (lambda job: threading.Thread(target=job, daemon=True, name="choice-cards").start())
    start(lambda: draw(question, publish=publish))


def settle(question, publish=None):
    """The cards of a question that is no longer pending come down, unless another show replaced
    them; one still being laid out is dropped before it goes up."""
    with _lock:
        if not _current["question"] or _current["question"] != question.get("id"):
            return
        up = bool(_current["show"])
        _current.update(show="", question="")
    if up:
        from . import events

        (publish or events.publish)({"kind": "screen", "marks": {"clear": True}})


def replaced(show):
    """Another marks show went up, so the cards on screen are no longer the question's."""
    with _lock:
        if _current["question"] and (not show or show != _current["show"]):
            _current.update(show="", question="")


SAMPLE = {
    "id": "qsample", "header": "Venue", "question": "Where should the team demo happen on Friday?",
    "options": [
        {"label": "Hall A", "description": "Seats forty, close to the kitchen"},
        {"label": "Online", "description": "Everyone joins from home, no room to book"},
        {"label": "The roof terrace", "description": "Only if the weather holds"},
    ],
    "recommended": 0,
}
