"""Asking to be shown what he can do is spotted in the words before the brain runs, so a tour is never lost to
a desktop act or a quick answer. Asking only what he can do gets a one line answer and an offer;
a yes to that offer within OFFER_SECONDS starts the tour."""

import re
import time

OFFER_SECONDS = 120

SHOW = re.compile(
    r"\b(?:show|demo|demonstrate|walk me through)\b[^.?!]{0,30}\b(?:what you can do|what you(?:'re| are) "
    r"(?:capable of|able to do)|your (?:tricks|abilities|skills|powers|features))\b"
    r"|\bgive me (?:a|the) (?:demo|tour|showcase)\b|\bshow (?:me )?(?:a|the) (?:demo|tour|showcase)\b"
    r"|\bshow off\b|\bvis mig hvad du kan\b",
    re.I,
)
ASK = re.compile(
    r"\bwhat (?:can|could) you do\b|\bwhat (?:else )?are you (?:capable of|able to do|good at)\b"
    r"|\bwhat are your (?:abilities|skills|powers|features)\b|\bwhat do you do\b|\bhvad kan du\b",
    re.I,
)
YES = re.compile(
    r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please|go on|go ahead|do it|show me|let'?s see|"
    r"go for it|absolutely|of course|ja|gerne)\b",
    re.I,
)

SHOW_NOTE = "(He asked you to show him what you can do: run the showcase skill now, the whole tour.)"
ASK_NOTE = ("(He asked what you can do. The tour is the answer, so do not list anything: reply with one short "
            "spoken sentence that offers to show him, for example \"Easier shown than told, sir: shall I give you the "
            "thirty second tour?\" No ask_user, no numbered choices, no tour yet. He answers by voice without your name.)")

_offered = {}


def note(conv, text, now=None):
    """The note for this line, or None: the tour, the offer, or a yes to an offer made just now."""
    now = time.time() if now is None else now
    words = " ".join(str(text or "").split())
    if not words:
        return None
    if SHOW.search(words):
        _offered.pop(conv, None)
        return SHOW_NOTE
    if ASK.search(words):
        _offered[conv] = now
        return ASK_NOTE
    offered = _offered.pop(conv, None)
    if offered is not None and now - offered <= OFFER_SECONDS and YES.search(words) and len(words.split()) <= 8:
        return SHOW_NOTE
    return None
