"""Questions Diablo asks Marcus with choices: the ask_user tool.

A question has a short header, the question, two to four options (a label and a one line
description each), the option Diablo recommends, whether more than one may be picked, and
whether Marcus may answer in his own words instead. It lives in the questions table with an
id, in one conversation, and is in one of these states:

    pending      shown and waiting; at most one per conversation, a new one supersedes it
    answered     he picked; the answer is kept on it
    expired      nobody answered within questions.ttl_s (an hour by default)
    cancelled    Diablo withdrew it (cancel_question), or a client dismissed it
    superseded   Diablo asked a newer question in the same conversation

The turn that asked claims it (claim), so it rides in that reply's meta.objects as a block of
kind question, and the reply's message id and threads are kept on it (link). Every change
patches that stored block and publishes a question event, so every client sees the same state.

An answer is delivered to Diablo as a new turn in the same conversation, filed under the same
threads, that starts with a structured line (answer_line):

    [answer to question q1a2b3c4d: Ship it tonight]

Answers come from a tap (the desktop card, Telegram's keyboard, the phone and web cards) or
from a typed or spoken reply that names an option or its number (match_reply).
"""

import json
import logging
import re
import secrets
import time

from . import db, events, settings

logger = logging.getLogger(__name__)

STATES = ("pending", "answered", "expired", "cancelled", "superseded")
TTL_S = 3600
QUESTION_CHARS = 300
HEADER_CHARS = 24
LABEL_CHARS = 48
DESCRIPTION_CHARS = 140
TEXT_CHARS = 1000
OPTIONS_MIN, OPTIONS_MAX = 2, 4
REPLY_WORDS = 8

listeners = []


class Problem(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _conn():
    return db._conn


def ttl():
    try:
        return max(60.0, float(settings.get("questions.ttl_s") or TTL_S))
    except (TypeError, ValueError):
        return float(TTL_S)


def new_id():
    return "q" + secrets.token_hex(4)


def _flat(value, limit):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def clean(arguments):
    """The question as stored, from ask_user's arguments. Raises ValueError with a reason Diablo can act on."""
    arguments = arguments if isinstance(arguments, dict) else {}
    question = _flat(arguments.get("question"), QUESTION_CHARS)
    if not question:
        raise ValueError("the question is missing")
    raw = arguments.get("options")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, list):
        raise ValueError("options must be a list of two to four {label, description}")
    options, seen = [], set()
    for entry in raw:
        if isinstance(entry, str):
            entry = {"label": entry}
        if not isinstance(entry, dict):
            continue
        label = _flat(entry.get("label"), LABEL_CHARS)
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        options.append({"label": label, "description": _flat(entry.get("description"), DESCRIPTION_CHARS)})
    if not OPTIONS_MIN <= len(options) <= OPTIONS_MAX:
        raise ValueError(f"give two to four options with distinct labels, not {len(options)}")
    recommended = recommended_index(arguments.get("recommended"), options)
    header = _flat(arguments.get("header"), HEADER_CHARS) or "Question"
    return {
        "header": header,
        "question": question,
        "options": options,
        "recommended": recommended,
        "multi": _truthy(arguments.get("multi_select") or arguments.get("multi")),
        "allow_text": _truthy(arguments.get("allow_text", True)),
        "harmless": _truthy(arguments.get("harmless")),
    }


def recommended_index(value, options):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        number = int(value)
        return number - 1 if 1 <= number <= len(options) else None
    wanted = str(value).strip().lower()
    for index, option in enumerate(options):
        if option["label"].lower() == wanted:
            return index
    return None


def _row(row):
    if row is None:
        return None
    data = json.loads(row["data"] or "{}")
    data.update(id=row["id"], conv=row["conv"], state=row["state"], created=row["created"], updated=row["updated"])
    return data


def _put(question, now=None, expect=None):
    """Store a question. With expect, only when its stored state is still that one; then whether it was."""
    question["updated"] = time.time() if now is None else now
    data = {k: v for k, v in question.items() if k not in ("id", "conv", "state", "created", "updated")}
    conn = _conn()
    with db._lock:
        if expect is not None:
            done = conn.execute(
                "UPDATE questions SET state = ?, updated = ?, data = ? WHERE id = ? AND state = ?",
                (question["state"], question["updated"], json.dumps(data), question["id"], expect),
            ).rowcount
            conn.commit()
            return done > 0
        conn.execute(
            "INSERT INTO questions (id, conv, state, created, updated, data) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET state = excluded.state, updated = excluded.updated, data = excluded.data",
            (question["id"], question["conv"], question["state"], question["created"], question["updated"], json.dumps(data)),
        )
        conn.commit()
    return question


def get(question_id, now=None):
    conn = _conn()
    with db._lock:
        row = conn.execute("SELECT * FROM questions WHERE id = ?", (str(question_id or ""),)).fetchone()
    question = _row(row)
    if question is not None and question["state"] == "pending" and is_stale(question, now):
        question = _change(question, "expired", now)
    return question


def is_stale(question, now=None):
    return (time.time() if now is None else now) >= question.get("expires", float("inf"))


def listed(state=None, conv=None, limit=50):
    query, args = "SELECT * FROM questions", []
    where = []
    if state:
        where.append(f"state IN ({','.join('?' for _ in state)})")
        args.extend(state)
    if conv:
        where.append("conv = ?")
        args.append(conv)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY created DESC LIMIT ?"
    args.append(limit)
    conn = _conn()
    with db._lock:
        rows = conn.execute(query, args).fetchall()
    return [_row(r) for r in rows]


def pending(conv=None, now=None):
    """The pending questions, newest first, with any that ran out of time expired on the way."""
    found = []
    for question in listed(("pending",), conv):
        if is_stale(question, now):
            _change(question, "expired", now)
        else:
            found.append(question)
    return found


def public(question):
    """The block clients draw: kind question."""
    out = {
        "kind": "question",
        "id": question["id"],
        "header": question.get("header") or "Question",
        "question": question.get("question") or "",
        "options": list(question.get("options") or []),
        "recommended": question.get("recommended"),
        "multi": bool(question.get("multi")),
        "allow_text": bool(question.get("allow_text")),
        "harmless": bool(question.get("harmless")),
        "state": question.get("state") or "pending",
        "created": question.get("created"),
        "expires": question.get("expires"),
    }
    if question.get("answer"):
        out["answer"] = question["answer"]
    return out


turns = {}


def ask(conv, arguments, now=None, turn=None):
    """Store a new pending question in conv; any pending one there is superseded. Raises ValueError.

    turn names the brain turn that asked, so only that turn's reply carries it; in the hub process it
    comes from turns, which the brain fills while a turn holds the conversation."""
    now = time.time() if now is None else now
    body = clean(arguments)
    conv = str(conv or "main")
    turn = turn if turn is not None else turns.get(conv)
    for old in pending(conv, now):
        _change(old, "superseded", now, expect="pending")
    question = dict(body, id=new_id(), conv=conv, state="pending", created=now, updated=now, turn=turn,
                    expires=now + ttl(), claimed=False, message=None, section=None, threads=[])
    _put(question, now)
    _announce(question)
    return question


def note(question_id, **fields):
    """Keep client bookkeeping on a question, like the Telegram message that shows it, without an event."""
    question = get(question_id)
    if question is None:
        return None
    question.update(fields)
    return question if _put(question, expect=question["state"]) else get(question_id)


def unsent(conv, surface, within=900, now=None):
    """Pending questions in conv asked in the last `within` seconds that `surface` has not shown yet."""
    now = time.time() if now is None else now
    return [q for q in pending(conv, now) if not q.get(surface) and now - q["created"] <= within]


def unlinked(conv, within=900, now=None):
    """Pending questions in conv asked in the last `within` seconds that hang under no stored reply yet."""
    now = time.time() if now is None else now
    return [q for q in pending(conv, now) if not q.get("message") and now - q["created"] <= within]


def claim(conv, since, turn=None):
    """The question blocks a turn asked, each handed to one turn only: the ones tagged with its turn, or,
    for an untagged question, any asked in conv since the turn began."""
    blocks = []
    for question in listed(("pending",), str(conv or "main"), limit=10):
        if question.get("claimed"):
            continue
        mine = question.get("turn") == turn if question.get("turn") else question["created"] >= since - 1
        if not mine:
            continue
        question["claimed"] = True
        if not _put(question, expect="pending"):
            continue
        blocks.append(public(question))
    return list(reversed(blocks))


def link(message_id, source, meta):
    """db listener: a stored reply that carries a question block names the message the card hangs under."""
    if not isinstance(meta, dict):
        return
    for obj in meta.get("objects") or []:
        if not isinstance(obj, dict) or obj.get("kind") != "question" or not obj.get("id"):
            continue
        question = get(obj["id"])
        if question is None or question.get("message"):
            continue
        question["message"] = message_id
        question["source"] = source
        question["section"] = meta.get("section") or "diablo"
        question["threads"] = [t for t in meta.get("threads") or [] if isinstance(t, str)]
        _put(question, expect=question["state"])


def _change(question, state, now=None, expect=None, **fields):
    """Move a question to state and tell everyone. With expect, a question no longer in that state is left
    alone and None comes back."""
    question = dict(question, state=state, **fields)
    if expect is not None:
        if not _put(question, now, expect=expect):
            return None
    else:
        _put(question, now)
    _patch_message(question)
    _announce(question)
    return question


def _patch_message(question):
    message_id = question.get("message")
    if not message_id:
        return
    stored = db.get_message(message_id)
    if stored is None:
        return
    meta = stored.get("meta") or {}
    objects = meta.get("objects") or []
    changed = False
    for index, obj in enumerate(objects):
        if isinstance(obj, dict) and obj.get("kind") == "question" and obj.get("id") == question["id"]:
            objects[index] = public(question)
            changed = True
    if not changed:
        return
    meta["objects"] = objects
    with db._lock:
        db._conn.execute("UPDATE messages SET meta = ? WHERE id = ?", (json.dumps(meta), message_id))
        db._conn.commit()


def _announce(question):
    event = {"kind": "question", "question": public(question)}
    if question.get("message"):
        event["message"] = question["message"]
        event["section"] = question.get("section") or "diablo"
    try:
        events.publish(event)
    except Exception:
        logger.exception("could not publish the question event")
    for listener in list(listeners):
        try:
            listener(question)
        except Exception:
            logger.exception("question listener failed")


def cancel(conv=None, question_id=None, now=None):
    """Withdraw one pending question, or the newest pending one in conv. The question, or None."""
    if question_id:
        question = get(question_id, now)
        if question is None or question["state"] != "pending":
            return None
        if conv and question["conv"] != conv:
            return None
    else:
        found = pending(conv, now)
        if not found:
            return None
        question = found[0]
    return _change(question, "cancelled", now)


def sweep(now=None):
    """Expire every pending question past its time. The expired ones."""
    now = time.time() if now is None else now
    gone = []
    for question in listed(("pending",), limit=200):
        if is_stale(question, now):
            gone.append(_change(question, "expired", now))
    return gone


def choose(question, choices=None, text="", source="", now=None):
    """Record an answer on a pending question. Raises Problem with an HTTP status when it cannot count."""
    now = time.time() if now is None else now
    if question is None:
        raise Problem(404, "no such question")
    if question["state"] == "pending" and is_stale(question, now):
        question = _change(question, "expired", now)
    if question["state"] != "pending":
        raise Problem(410 if question["state"] == "expired" else 409, f"that question is {question['state']}")
    options = question.get("options") or []
    picked = []
    for value in choices or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            raise Problem(400, "choices are option numbers from 0")
        if not 0 <= index < len(options):
            raise Problem(400, f"there is no option {index}")
        if index not in picked:
            picked.append(index)
    text = _flat(text, TEXT_CHARS)
    if text and not question.get("allow_text"):
        raise Problem(400, "this question takes one of its options only")
    if len(picked) > 1 and not question.get("multi"):
        raise Problem(400, "this question takes one option")
    if not picked and not text:
        raise Problem(400, "pick an option or write an answer")
    answer = {"choices": picked, "labels": [options[i]["label"] for i in picked], "text": text,
              "source": str(source or ""), "at": now}
    fresh = get(question["id"], now) or question
    answered = _change(dict(fresh, picked=[]), "answered", now, expect="pending", answer=answer)
    if answered is None:
        current = get(question["id"], now) or question
        raise Problem(410 if current["state"] == "expired" else 409, f"that question is {current['state']}")
    return answered


def reopen(question, now=None):
    """Put an answer back to pending when the turn that carried it failed, unless its time ran out."""
    current = get(question["id"], now)
    if current is None or current["state"] != "answered":
        return current
    current.pop("answer", None)
    state = "expired" if is_stale(current, now) else "pending"
    return _change(current, state, now, expect="answered") or get(question["id"], now)


def answer_line(question):
    """The structured line Diablo gets as the answer turn."""
    answer = question.get("answer") or {}
    labels = "; ".join(answer.get("labels") or [])
    text = answer.get("text") or ""
    if labels and text:
        return f"[answer to question {question['id']}: {labels}] He added: {text}"
    if labels:
        return f"[answer to question {question['id']}: {labels}]"
    return f"[answer to question {question['id']}, in his own words: {text}]"


def shown_text(question):
    """What the chat shows as his message for a tapped answer."""
    answer = question.get("answer") or {}
    labels = ", ".join(answer.get("labels") or [])
    text = answer.get("text") or ""
    if labels and text:
        return f"{labels}. {text}"
    return labels or text


NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "first": 1, "second": 2, "third": 3, "fourth": 4,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "en": 1, "to": 2, "tre": 3, "fire": 4,
}
SPOKEN_NUMBERS = ("One", "Two", "Three", "Four")
FILLER = set("""the a an option number choice pick go with i'll ill take let's lets do say
choose please it one thanks thank you that's thats is yes ok okay sure and or i want would like
diablo sir answer""".split())
WORD_RE = re.compile(r"[a-z0-9æøå']+")


def _words(text):
    return WORD_RE.findall(str(text or "").lower())


def parse_reply(question, text):
    """The option indices a short typed or spoken reply names, or None when it is not an answer."""
    options = question.get("options") or []
    words = _words(text)
    if not words or len(words) > REPLY_WORDS:
        return None
    normal = " ".join(words)
    for index, option in enumerate(options):
        if " ".join(_words(option["label"])) == normal:
            return [index]
    numbers = []
    counted = [w for w in words if w != "one"] if any(w.isdigit() or (w in NUMBER_WORDS and w != "one") for w in words) else words
    for word in counted:
        value = int(word) if word.isdigit() else NUMBER_WORDS.get(word)
        if word in ("to", "en", "fire") and len(words) > 1:
            value = None
        if value is not None:
            if not 1 <= value <= len(options):
                return None
            if value - 1 not in numbers:
                numbers.append(value - 1)
    rest = [w for w in words if not (w.isdigit() or w in NUMBER_WORDS or w in FILLER)]
    if numbers and not rest:
        if len(numbers) > 1 and not question.get("multi"):
            return None
        return numbers
    said = [w for w in words if w not in FILLER]
    for index, option in enumerate(options):
        label = [w for w in _words(option["label"]) if w not in FILLER]
        if label and said == label:
            return [index]
    return None


def match_reply(conv, text, now=None):
    """The newest pending question in conv and the options this reply names, or (None, None)."""
    found = pending(conv, now)
    if not found:
        return None, None
    choices = parse_reply(found[0], text)
    if choices is None:
        return None, None
    return found[0], choices


def spoken_text(question):
    """The question with numbered options, to say aloud."""
    parts = [question.get("question") or ""]
    options = question.get("options") or []
    for index, option in enumerate(options):
        parts.append(f"{SPOKEN_NUMBERS[index]}, {option['label']}.")
    recommended = question.get("recommended")
    if isinstance(recommended, int) and 0 <= recommended < len(options):
        parts.append(f"I would pick {SPOKEN_NUMBERS[recommended].lower()}.")
    return " ".join(p.strip() for p in parts if p.strip())


def numbered_text(question):
    """The question as plain text, for a client that cannot draw the card."""
    lines = [question.get("question") or ""]
    recommended = question.get("recommended")
    for index, option in enumerate(question.get("options") or []):
        mark = " (recommended)" if index == recommended else ""
        detail = f": {option['description']}" if option.get("description") else ""
        lines.append(f"{index + 1}. {option['label']}{mark}{detail}")
    lines.append("Reply with the number" + (", or in your own words." if question.get("allow_text") else "."))
    return "\n".join(lines)


def tool_text(question):
    return (f"Asked as question {question['id']}. The hub shows it to him with its options: a card in the apps, "
            "a keyboard on Telegram, read aloud with numbers on a spoken turn. End your turn now with at most one "
            "short line; do not repeat the question or the options. His answer comes as a new turn starting "
            f"[answer to question {question['id']}: ...]. It expires in {int(ttl() // 60)} minutes.")


def call(name, arguments, conv):
    """ask_user and cancel_question, for the api brain and for the MCP server when the hub is away."""
    if name == "ask_user":
        try:
            return tool_text(ask(conv, arguments))
        except ValueError as error:
            return f"not asked: {error}"
    if name == "cancel_question":
        done = cancel(conv, str(arguments.get("id") or "").strip() or None)
        return f"question {done['id']} withdrawn" if done else "no pending question to withdraw"
    return f"unknown tool {name}"


ASK_TOOL = {
    "name": "ask_user",
    "description": (
        "Ask Marcus one question with two to four choices and wait for his pick, the way a form does, instead of "
        "asking in prose. Use it when a choice is genuinely his. The hub shows it as buttons in his apps and a "
        "keyboard on Telegram and reads it aloud on a spoken turn; his answer comes back as a new turn "
        "[answer to question <id>: <label>]. One question at a time: a new one replaces any still open. After "
        "calling it, end your turn with at most one short line."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "the question, one sentence ending in a question mark"},
            "header": {"type": "string", "description": "a label of one to three words, like Ship it or Model"},
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "one to five words he can tap"},
                        "description": {"type": "string", "description": "one line on what this choice does"},
                    },
                    "required": ["label"],
                },
            },
            "recommended": {"type": "integer", "description": "the number of the option you would pick, from 1"},
            "multi_select": {"type": "boolean", "description": "true when he may pick more than one"},
            "allow_text": {"type": "boolean", "description": "false when only the options make sense; by default he may also answer in his own words"},
            "harmless": {"type": "boolean", "description": "true only when every option is safe to take from a locked banditbox, like which colour or which song; never for anything that spends, sends, deletes, ships or runs work"},
        },
        "required": ["question", "header", "options", "recommended"],
    },
}
CANCEL_TOOL = {
    "name": "cancel_question",
    "description": "Withdraw the question you asked with ask_user when it no longer matters, like when he answered it in other words. Leave id out for the newest one.",
    "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
}
