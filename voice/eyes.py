"""Diablo's eyes while he speaks: one look per clause, picked by Jev, timed to the clause's
first word.

A sentence is split at every comma, colon, semicolon and full stop. Jev reads the whole
sentence once and answers one question per clause (which look fits it), so a reply of four
clauses costs one call of about a quarter second. It runs beside the speech fetch, so the
reply never waits for it. When Jev is not reachable every clause keeps the neutral look; the
face still blinks, so it is never dead.

The looks are Marcus's drawings in assets/faces/eyes/<look>/, cut from his moods
(design/brand/faces/sheet.py rig) or drawn on their own.
"""
import logging
import random
import re
import threading

log = logging.getLogger("diablo.eyes")

# What each look says, in the words Jev chooses by. The keys are folder names.
LOOKS = {
    "neutral": "a bare fact with no feeling in it at all",
    "expectant": "a question or a nudge to Marcus: waiting on him, telling him what to do next",
    "happy": "friendly and warm: a greeting, helpful news, thanks, a cheerful line",
    "proud": "a job done well, praise for Marcus, a win",
    "wide": "eager attention, something interesting, curious",
    "surprised": "something unexpected, a big number, wow",
    "angry": "something is broken or blocked, a firm no",
    "annoyed": "mild irritation, a nag, something he has said before",
    "judging": "dry teasing, raising an eyebrow at Marcus, sarcasm",
    "worried": "a warning, a risk, something might go wrong",
    "sorry": "an apology, bad news, something failed",
    "thinking": "reasoning out loud, weighing options, not sure yet",
    "confused": "he did not understand, something does not add up",
    "relaxed": "easy and unhurried: small talk, a plan, all is fine",
    "bored": "a dull detail, routine, a long list read out",
    "glance": "pointing at something on screen or elsewhere, an aside",
    "relieved": "trouble is over, it worked after all",
}
NEUTRAL = "neutral"
TIMEOUT = 1.5
_SPLIT = re.compile(r"(?<=[,;:.!?、。！？])\s+")


# a full stop after these is part of the word, not the end of a clause
_TITLES = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "e.g.", "i.e.", "jr.", "sr.", "prof."}


def _parts(text):
    parts = []
    for part in _SPLIT.split(str(text or "").strip()):
        if parts and parts[-1].split() and parts[-1].split()[-1].lower() in _TITLES:
            parts[-1] += " " + part
        else:
            parts.append(part)
    return parts


def clauses(text):
    """[(clause, index of its first word in text.split())] for a sentence."""
    out, word = [], 0
    for part in _parts(text):
        words = part.split()
        if words:
            out.append((part, word))
            word += len(words)
    return out


def tag(text):
    """The look for each clause of the sentence, in order. Neutral when Jev cannot answer."""
    parts = clauses(text)
    if not parts:
        return []
    looks = [NEUTRAL] * len(parts)
    try:
        from . import jevfast

        questions = {
            f"c{i + 1}": {
                "type": "choice",
                "instructions": f"Which look should Diablo's eyes have while he says clause {i + 1}: \"{part}\"",
                "criteria": LOOKS,
            }
            for i, (part, _) in enumerate(parts)
        }
        state = ("Diablo, a small hand-drawn demon assistant, is saying this sentence out loud to Marcus: "
                 + str(text)[:600] + " Pick the expression of his eyes for each clause, the way an animator"
                 " acts a line: his eyes should keep moving with what he says. Use neutral only for a"
                 " clause with no feeling at all.")
        answer = jevfast.ask(state, questions, timeout=TIMEOUT, origin="jev eyes")
        if not answer:
            return looks
        for i in range(len(parts)):
            choice = answer.get(f"c{i + 1}")
            if choice == "glance":
                looks[i] = random.choice(("glance-left", "glance-right"))
            elif choice in LOOKS:
                looks[i] = choice
    except Exception:
        log.exception("tagging eyes failed, neutral eyes")
    return looks


def tag_soon(text):
    """Starts tagging on a thread; the returned call waits for the answer (at most TIMEOUT more)."""
    box = {"looks": None}
    done = threading.Event()

    def run():
        try:
            box["looks"] = tag(text)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True, name="eyes-tag").start()

    def result(wait=TIMEOUT + 0.5):
        """The looks, waiting at most `wait` seconds more; neutral if Jev is not back by then."""
        done.wait(wait)
        return box["looks"] or [NEUTRAL] * len(clauses(text))

    return result


def cues(text, looks, word_starts=None, duration=0.0):
    """[(seconds, look)]: each clause's look from its first word on. Word starts come from the
    aligner; without them a clause starts where its share of the letters says."""
    parts = clauses(text)
    if not parts:
        return []
    total = sum(len(p) for p, _ in parts) or 1
    out, letters = [], 0
    for (part, first), look in zip(parts, looks):
        at = None
        if word_starts and first < len(word_starts):
            at = word_starts[first]
        if at is None:
            at = duration * letters / total
        letters += len(part)
        at = 0.0 if not out else round(max(at, out[-1][0]), 3)
        if not out or out[-1][1] != look:
            out.append((at, look))
    return out
