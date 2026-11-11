"""Control words he obeys without thinking: voice on or off, ears on or off.

These never reach the model. They are matched on the transcript and acted on
in the loop, so they cost nothing and land as fast as the ear can hear them.
"""

import re

NAME = re.compile(r"\bdiablo\b", re.I)

# Order matters: the first family whose pattern matches wins.
FAMILIES = [
    ("deafen", re.compile(
        r"\b(stop listening|stop hearing|deafen|go deaf|cover your ears|close your ears|"
        r"(turn|switch|shut) off (the |your )?(mic|microphone)|(mic|microphone) off|no more (mic|microphone|listening)|"
        r"don'?t listen|stop the (mic|microphone)|ears off)\b", re.I)),
    ("listen", re.compile(
        r"\b(start listening|listen (to me|now|up|again|in)|listening now|(turn|switch) on (the |your )?(mic|microphone)|"
        r"(mic|microphone) on|wake up|hear me|open your ears|ears on|you can listen|(?<!don't )(?<!dont )(?<!not )(?<!never )listen(?!\s+(to|for)\b))\b", re.I)),
    ("mute", re.compile(
        r"\b(shut up|be quiet|hush|silence|stop (speaking|talking)|(turn|switch|shut) off (your|the) voice|"
        r"voice off|no more (talking|speaking)|mute yourself|go silent|not another word|zip it)\b", re.I)),
    ("unmute", re.compile(
        r"\b((you (can|may) )?(speak|talk) (now|again|up)|(turn|switch) on (your|the) voice|voice on|"
        r"unmute yourself|use your voice|your voice back|(speak|talk)\W*$)", re.I)),
]

# Whole utterance forms: just his name and a word.
BARE = [
    ("listen", re.compile(r"^\W*(diablo\W*)?(listen|on|turn on|wake)\W*$", re.I)),
    ("deafen", re.compile(r"^\W*(diablo\W*)?(off|turn off|deaf)\W*$", re.I)),
    ("mute", re.compile(r"^\W*(diablo\W*)?(quiet|silence|shush|shh+)\W*$", re.I)),
    ("unmute", re.compile(r"^\W*(diablo\W*)?(speak|talk)\W*$", re.I)),
]


def match(text):
    """(intents, named): every control word in the sentence, voice before ears.

    intents is a list such as ["mute", "deafen"], empty when nothing matched.
    Voice comes first so a deafen line is never spoken after a mute order.
    """
    named = bool(NAME.search(text))
    found = [intent for intent, pattern in FAMILIES if pattern.search(text)]
    if not found:
        found = [intent for intent, pattern in BARE if pattern.match(text)][:1]
    # Opposites in one breath: the later word wins.
    for a, b in (("mute", "unmute"), ("deafen", "listen")):
        if a in found and b in found:
            later = max((a, b), key=lambda w: _last_index(text, w))
            found.remove(a if later == b else b)
    order = {"mute": 0, "unmute": 0, "deafen": 1, "listen": 1}
    found.sort(key=lambda w: order.get(w, 2))
    return found, named


def _last_index(text, intent):
    pattern = dict(FAMILIES).get(intent) or dict(BARE).get(intent)
    last = -1
    for hit in pattern.finditer(text):
        last = hit.start()
    return last
