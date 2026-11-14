"""One spoken reply for a burst of spoken turns.

Marcus often says several things in a row. The ears hold the replies of such a burst and,
once he stops, send them here oldest first; Haiku folds them into one short spoken reply,
the newest order first and the rest briefly. Each reply is already in the chat on its own,
so nothing is stored here.
"""

import asyncio
import logging
import re
import time

from . import threads

logger = logging.getLogger("diablo.hub.voicemerge")

MERGE_S = 20
MAX_TURNS = 12
MAX_CHARS = 1500

MERGE_SYSTEM = (
    "You write what Diablo says aloud after Marcus gave him several spoken orders in a row. "
    "You get each order as Marcus said it and Diablo's reply to it, oldest first. "
    "Write one short spoken reply that covers every order: the newest one first and most fully, "
    "the earlier ones briefly after it. Keep what each reply said was done or found, and keep any "
    "question he was asked. Never add facts, and never say something worked when its reply did "
    "not say so. When several orders were about one thing, say it once. "
    "Diablo is a refined, courteous demon butler with a faint smugness; he never says sir. "
    "Plain spoken sentences only, four at most, no lists, no markdown, no quotes, no dashes. "
    "Answer in the language Marcus used. Reply with the words to speak and nothing else."
)

quick_merge = threads.Quick(MERGE_SYSTEM)


def prompt(turns):
    lines = []
    for n, turn in enumerate(turns[-MAX_TURNS:], 1):
        lines.append(f"{n}. Marcus said: {threads.clip(turn.get('heard'), 300)}")
        lines.append(f"   Diablo replied: {threads.clip(turn.get('reply'), MAX_CHARS)}")
    return "\n".join(lines)


def tidy(text):
    text = re.sub(r"[*_#>`|]+", "", str(text or ""))
    text = text.replace(chr(8212), ", ").replace(chr(8211), ", ")
    text = " ".join(text.split()).strip().strip('"').strip()
    return text


async def merge(turns):
    """{"say", "via", "ms"}; via is haiku, or empty with say empty when it failed."""
    started = time.monotonic()
    turns = [t for t in turns if isinstance(t, dict) and str(t.get("reply") or "").strip()]
    if not turns:
        return {"say": "", "via": "", "ms": 0}
    if len(turns) == 1:
        return {"say": tidy(turns[0]["reply"]), "via": "single", "ms": 0}
    try:
        found = await asyncio.wait_for(quick_merge.ask(prompt(turns)), MERGE_S)
    except Exception as error:
        logger.warning("merging %d spoken replies failed: %s", len(turns), error)
        found = ""
    say = tidy(found)
    ms = round((time.monotonic() - started) * 1000)
    logger.info("merged %d spoken replies in %d ms", len(turns), ms)
    return {"say": say, "via": "haiku" if say else "", "ms": ms}
