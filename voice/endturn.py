"""When has Marcus finished talking? A pause is not an end.

A breath or a moment to think is 1 to 2 seconds of quiet in the middle of a sentence. The
ears used to close the turn after 1.2 s, so he rushed. Now a short quiet only counts once
the words so far read as a finished sentence; if they trail off, the ears hold longer, and
a hard cap on the whole turn keeps them from waiting forever.

    ears.silence_s         quiet that ends a turn whose words are finished (default 2.0)
    ears.silence_open_s    quiet that ends a turn whose words trail off (default 3.5)
    ears.silence_short_s   quiet that ends a short command like "next song" (default 1.0)
    ears.turn_max_s        longest turn in seconds (default 90)
    ears.adaptive          1 to read the words, 0 for the plain base quiet always (default 1)

They are hub settings, read each minute by voice/ears.py, and fall back to the same names
in ~/.config/diablo/config.toml, flat or as an [ears] table. The old silence_tail_s sets the
base quiet only while ears.silence_s is not set, and the hub always sends that one.
"""

import re
from dataclasses import dataclass

DEFAULTS = {
    "ears.silence_s": "2.0",
    "ears.silence_open_s": "3.5",
    "ears.silence_short_s": "1.0",
    "ears.turn_max_s": "90",
    "ears.adaptive": "1",
}

OFF = ("0", "false", "no", "off")

# Words nobody ends a sentence on, even when the transcript put a full stop after them.
OPEN_WORDS = frozenset("""
    and but because or nor if unless though although whether than the a an of to with without
    uh um uhh umm er erm ah hm hmm
""".split())

# Short closings that stand as a full turn even when the transcript has no full stop.
CLOSINGS = frozenset("please thanks thank you sir now again".split())

# A command is this many words at most: "next song", "workspace 3", "lights off". Measured
# 2026-11-11: such lines waited 2.1 s, and 3.4 s when Whisper left the full stop off.
COMMAND_WORDS = 4
# ...and takes this long to say at most. Longer talk is a sentence, so its pauses are first
# read at the base quiet, as before, and a breath costs no extra speech to text.
COMMAND_SPEECH_S = 1.8

ENDING_PUNCT = ".?!"
TRAILING_MARKS = (",", ";", ":", "-", "...", "\u2026", "\u2014", "\u2013")


@dataclass(frozen=True)
class Policy:
    base_s: float = 2.0
    open_s: float = 3.5
    max_s: float = 90.0
    short_s: float = 1.0
    adaptive: bool = True

    @classmethod
    def from_cfg(cls, cfg):
        def lookup(key):
            if key in cfg:
                return cfg[key]
            group, _, name = key.partition(".")
            nested = cfg.get(group)
            return nested.get(name) if name and isinstance(nested, dict) else None

        def number(keys, default, low, high):
            for key in keys:
                raw = lookup(key)
                if raw in (None, ""):
                    continue
                try:
                    return min(max(float(raw), low), high)
                except (TypeError, ValueError):
                    continue
            return default

        base = number(("ears.silence_s", "silence_tail_s"), cls.base_s, 0.3, 10.0)
        longer = number(("ears.silence_open_s",), cls.open_s, 0.3, 15.0)
        flag = str(lookup("ears.adaptive") if lookup("ears.adaptive") is not None else "1").strip().lower()
        return cls(
            base_s=base,
            open_s=max(longer, base),
            short_s=min(number(("ears.silence_short_s",), cls.short_s, 0.3, 10.0), base),
            max_s=number(("ears.turn_max_s",), cls.max_s, 5.0, 600.0),
            adaptive=flag not in OFF,
        )


def words_of(text):
    return re.findall(r"[\w']+", text.lower())


def trails_off(text):
    """True when the words so far read like the middle of a thought, not its end.

    Empty text is not a trail: nothing was heard, so there is nothing to wait for.
    """
    text = text.strip()
    if not text:
        return False
    words = words_of(text)
    if not words:
        return False
    if text.endswith(TRAILING_MARKS):
        return True
    last = words[-1]
    if text.rstrip("\"')]}").endswith(tuple(ENDING_PUNCT)):
        return last in OPEN_WORDS
    return last not in CLOSINGS


def is_command(text):
    """A short line that stands on its own: a few words, not ending on an open word or mark.

    No full stop needed, since Whisper often leaves it off a two word line. Numbers count as
    an ending here ("workspace 3"), unlike in trails_off.
    """
    text = (text or "").strip()
    words = words_of(text)
    if not words or len(words) > COMMAND_WORDS or text.endswith(TRAILING_MARKS):
        return False
    return words[-1] not in OPEN_WORDS


def quiet_needed(text, policy):
    """Seconds of quiet that end a turn whose words so far are `text`."""
    if not policy.adaptive:
        return policy.base_s
    if is_command(text):
        return policy.short_s
    return policy.open_s if trails_off(text) else policy.base_s


def first_check_s(policy, heard_s=0.0):
    """The quiet at which the words are first read: soon enough for a short command to end.

    heard_s is the talk so far; past COMMAND_SPEECH_S it is no command, and the base decides.
    """
    if not policy.adaptive or heard_s > COMMAND_SPEECH_S:
        return policy.base_s
    return min(policy.short_s, policy.base_s)


def decide(quiet_s, text, policy):
    """'end' or 'wait' for this much quiet after these words.

    text None means the words are not known (the check failed or was not run): the base
    quiet decides.
    """
    if text is None:
        return "end" if quiet_s >= policy.base_s else "wait"
    return "end" if quiet_s >= quiet_needed(text, policy) else "wait"
