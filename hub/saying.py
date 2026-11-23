"""Words the speech voice says wrong in Latin letters, rewritten just before they go to the TTS.

Marcus asked on 2026-11-23 that his machines with Japanese names reach the TTS in katakana, so the
voice says them as Japanese words instead of an English guess. Only the text sent to speech
changes; chat, cards and logs keep the plain names. The words are box values, not repo values:
DIABLO_SAY_AS in ~/.config/diablo/local.env, as word=reading pairs split by commas.
"""

import re
from functools import lru_cache

from . import localenv


@lru_cache(maxsize=4)
def _table(raw):
    pairs = {}
    for part in str(raw or "").split(","):
        word, _, reading = part.partition("=")
        word, reading = word.strip(), reading.strip()
        if word and reading:
            pairs[word.lower()] = reading
    if not pairs:
        return pairs, None
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(pairs, key=len, reverse=True)) + r")\b",
                         re.IGNORECASE)
    return pairs, pattern


def table():
    """localenv read local.env at import, so a change to the list takes a restart of the hub or ears."""
    return _table(localenv.get("DIABLO_SAY_AS"))


def for_speech(text):
    """The text as the TTS should get it."""
    if not text:
        return text
    pairs, pattern = table()
    if pattern is None:
        return text
    return pattern.sub(lambda match: pairs[match.group(1).lower()], str(text))
