"""Where a message Diablo sends on his own lands: the app first, Telegram only when it answers Telegram.

Every proactive message (a T3 event, a scheduled task's result, a reminder, mail, the notify tool) goes
through deliver(). It is stored in the main conversation as an assistant message with meta pushed and
kind, so the phone and web apps show it and the db listener publishes the message event the phone's
listener turns into a notification. Telegram gets it too only when the turn came from Telegram, or when
the delivery.telegram setting is "all", the old behaviour.

Buttons that used to be a Telegram keyboard are question objects here (choice()): the app draws them as
a question card, and an answer runs the question's action (answer_action()) instead of a brain turn.
"""

import asyncio
import html
import logging
import re

from . import db, questions, sections, settings

logger = logging.getLogger(__name__)

SOURCE = "hub"
MODES = ("replies", "all")
ACTION_TTL_S = 7 * 24 * 3600
SPAN_RE = re.compile(r"<a\s+href=\"([^\"]*)\"\s*>(.*?)</a>|</?b>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
MARKDOWN_RE = re.compile(r"([\\`*\[\]<>])")


def mode():
    value = str(settings.get("delivery.telegram") or "replies").strip().lower()
    return value if value in MODES else "replies"


def from_telegram(origin):
    return str(origin or "").strip().lower().startswith("telegram")


def to_telegram(origin=""):
    return mode() == "all" or from_telegram(origin)


def _words(text, markdown):
    """Text between the spans: anything that came from a mail stays words, never Markdown."""
    text = html.unescape(TAG_RE.sub("", text))
    return MARKDOWN_RE.sub(r"\\\1", text) if markdown else text


def plain(text, markdown=True):
    """Telegram HTML as the Markdown the apps draw, or as bare text. Only the <b> and <a href> spans the
    hub wrote become Markdown; every other character is escaped, so a sender name or summary that reads
    like a Markdown link stays text."""
    text = str(text or "")
    out, at = [], 0
    for found in SPAN_RE.finditer(text):
        out.append(_words(text[at:found.start()], markdown))
        if found.group(1) is not None:
            label = _words(found.group(2), markdown)
            url = html.unescape(found.group(1))
            for raw, safe in (("(", "%28"), (")", "%29"), (" ", "%20"), ("<", "%3C"), (">", "%3E")):
                url = url.replace(raw, safe)
            out.append(f"[{label}]({url})" if markdown else label)
        elif markdown:
            out.append("**")
        at = found.end()
    out.append(_words(text[at:], markdown))
    return "".join(out).strip()


def store(text, kind, objects=None, client_id=None, source=SOURCE, meta=None):
    """The app copy: an assistant message in the main conversation. Its id. meta may set pushed false
    for a message that answers a stored line, like a scheduled task's result, so the two stay a pair.
    A client_id already stored is not stored again."""
    if client_id:
        _, earlier = db.by_client_id(client_id, sections.MAIN_ID)
        if earlier is not None:
            return earlier["id"]
    stored = {"pushed": True, **(meta or {}), "section": sections.MAIN_ID, "kind": kind}
    if objects:
        stored["objects"] = objects
    if client_id:
        stored["client_id"] = str(client_id)
    return db.add_message(source, "assistant", text, stored)


async def deliver(text, kind="report", objects=None, origin="", client_id=None, buttons=None, markup="plain",
                  source=SOURCE, meta=None, telegram_text=None):
    """Put text in front of Marcus. Returns {"id", "telegram", "repeat"}, or None for nothing to say.

    markup "html" is Telegram's HTML, shown in the app as Markdown. buttons are Telegram keyboard rows of
    (label, callback data), sent only when Telegram gets the message; the app gets the same choice as a
    question block in objects. A client_id already delivered is not delivered again."""
    shown = plain(text) if markup == "html" else str(text or "").strip()
    if not shown:
        return None
    if client_id:
        _, earlier = db.by_client_id(client_id, sections.MAIN_ID)
        if earlier is not None:
            return {"id": earlier["id"], "telegram": False, "repeat": True}
    message_id = store(shown, kind, objects, client_id, source, meta)
    sent = False
    if to_telegram(origin):
        try:
            sent = await _telegram(telegram_text or text, markup, buttons, objects)
        except Exception:
            logger.exception("telegram copy of a %s message failed", kind)
    return {"id": message_id, "telegram": sent, "repeat": False}


async def _telegram(text, markup, buttons, objects):
    from . import bot

    if markup == "html" or buttons:
        sent = await bot.push_card(text, buttons)
    else:
        sent = await bot.push(text)
    if sent and not buttons and any(isinstance(o, dict) and o.get("kind") == "question" for o in objects or []):
        await bot.push_questions()
    return sent


def choice(conv, header, question, labels, action):
    """A question block whose answer runs action instead of a brain turn. Its own conv, so it never
    replaces a question Diablo asked, and he cannot answer it from a typed reply."""
    asked = questions.ask(conv, {"header": header, "question": question,
                                 "options": [{"label": label} for label in labels], "allow_text": False},
                          ttl_s=ACTION_TTL_S, action=action)
    return questions.public(asked)


class Refused(Exception):
    """The action would not take the answer, and nothing was written: the line to show him."""


async def run_action(question):
    """Hand an answered action question to what it was for. The line to show Marcus. A mail card goes
    through mailbox.queue_decision, the Mail panel's own path, so the two can never contradict each other;
    the mail loop carries it out within seconds and says so in the app if it fails."""
    action = question.get("action") or {}
    picked = (question.get("answer") or {}).get("choices") or []
    decisions = action.get("decisions") or []
    if not picked or not 0 <= picked[0] < len(decisions):
        raise Refused("That did nothing.")
    if action.get("kind") == "mail":
        from . import mailbox

        try:
            return await asyncio.to_thread(mailbox.queue_decision, int(action["id"]), decisions[picked[0]])
        except (LookupError, ValueError) as error:
            raise Refused(str(error)) from None
    raise Refused("That did nothing.")


def answered_before(question, client_id):
    """The stored outcome of an earlier tap with this client_id on this question, else None."""
    if client_id is None:
        return None
    earlier, reply = db.by_client_id(client_id, sections.MAIN_ID)
    if reply is None or (reply["meta"] or {}).get("answers") != question["id"]:
        return None
    return {"reply": reply["text"], "id": reply["id"], "section": sections.MAIN_ID,
            "user_id": earlier["id"] if earlier else None, "objects": [], "answered": question["id"]}


async def answer_action(question, choices, text="", source="fleet", client_id=None):
    """A tap on an action question: record it, hand it on, keep both lines in the main conversation.
    A retried tap (the same client_id) gets the stored reply. Raises questions.Problem when the answer
    cannot count. An action that refuses leaves the card answered with its reason; only a failure before
    anything was written puts the question back."""
    stored = answered_before(question, client_id)
    if stored is not None:
        return stored
    answered = questions.choose(question, choices, text, source)
    kind = (answered.get("action") or {}).get("kind") or "action"
    base = {"section": sections.MAIN_ID, "answers": answered["id"]}
    if client_id is not None:
        base["client_id"] = client_id
    user_id = db.add_message(source, "user", questions.shown_text(answered), base)
    try:
        said = await run_action(answered)
    except Refused as refused:
        said = str(refused)
    except Exception:
        questions.reopen(answered)
        db.delete_message(user_id)
        raise
    reply_id = db.add_message(source, "assistant", said, dict(base, kind=kind))
    return {"reply": said, "id": reply_id, "section": sections.MAIN_ID, "user_id": user_id, "objects": [],
            "answered": answered["id"]}
