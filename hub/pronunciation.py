"""Words and phrases the voice says in another language, the pronunciation lexicon.

The owner asked on 2026-11-28 that listed words go to the TTS in their own language, so a Danish
place name is said with Danish sounds inside an English reply. The lexicon is a box value, never
a repo value: ~/.config/diablo/pronunciation.toml on the hub, one table per language code with a
`words` list (hub/pronunciation.example.toml). It is read again whenever the file changes, so an
edit needs no restart; a missing or broken file changes nothing.

Cartesia takes one language per request and no tag inside the transcript, so a reply is cut into
runs by language here and hub/tts.py renders each run with its own language field.
"""

import logging
import os
import re
import threading
import tomllib
from pathlib import Path

log = logging.getLogger("diablo.pronunciation")

PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "diablo" / "pronunciation.toml"

_LETTER = re.compile(r"[^\W_]")
_lock = threading.Lock()
_cache = {"key": None, "words": {}, "pattern": None}


def _spaced(phrase):
    return " ".join(str(phrase).split())


def _norm(phrase):
    return _spaced(phrase).casefold()


def parse(data):
    """({folded phrase: language}, [phrases as listed]) from the parsed toml, the first language
    listing a phrase keeping it."""
    words = {}
    for language, entry in (data or {}).items():
        code = str(language).strip()
        if not code:
            continue
        listed = entry.get("words") if isinstance(entry, dict) else entry
        if isinstance(listed, str):
            listed = [listed]
        if not isinstance(listed, list):
            continue
        for phrase in listed:
            key = _norm(phrase) if isinstance(phrase, str) else ""
            if key and key not in words:
                words[key] = (code, _spaced(phrase))
    return {key: code for key, (code, spelled) in words.items()}, [spelled for code, spelled in words.values()]


def compile_words(phrases):
    if not phrases:
        return None
    alternatives = []
    for phrase in sorted(phrases, key=len, reverse=True):
        alternatives.append(r"\s+".join(re.escape(part) for part in phrase.split(" ")))
    return re.compile(r"(?<![^\W_])(" + "|".join(alternatives) + r")(?![^\W_])", re.IGNORECASE)


def _load(path):
    try:
        stat = path.stat()
    except OSError:
        return {}, None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _cache["key"] == key:
            return _cache["words"], _cache["pattern"]
        try:
            words, phrases = parse(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            log.warning("pronunciation lexicon %s not read: %s", path, error)
            words, phrases = {}, []
        pattern = compile_words(phrases)
        _cache.update(key=key, words=words, pattern=pattern)
        log.info("pronunciation lexicon loaded: %d entries in %s", len(words),
                 ", ".join(sorted(set(words.values()))) or "no languages")
        return words, pattern


def segments(text, language="en", path=None):
    """The text as [(language, run)] in order, listed phrases in their own language and the rest in
    `language`. Runs with no letters or digits ride on a neighbour, so no request is only a comma."""
    text = "" if text is None else str(text)
    words, pattern = _load(Path(path) if path else PATH)
    if pattern is None or not text:
        return [(language, text)]
    parts = []
    at = 0
    for match in pattern.finditer(text):
        if match.start() > at:
            parts.append([language, text[at:match.start()]])
        parts.append([words.get(_norm(match.group(1)), language), match.group(1)])
        at = match.end()
    if at < len(text):
        parts.append([language, text[at:]])
    runs = []
    for part in parts:
        if runs and (runs[-1][0] == part[0] or not _LETTER.search(part[1])):
            runs[-1][1] += part[1]
        elif runs and not _LETTER.search(runs[-1][1]):
            part[1] = runs.pop()[1] + part[1]
            runs.append(part)
        else:
            runs.append(part)
    return [(code, run) for code, run in runs] or [(language, text)]
