"""Words speech to text gets wrong, and what they should be.

hearing.words holds one rule per line, the way the gvoice vocabulary writes them:
`Right = wrong, other wrong`. Every wrong form is replaced by the right one wherever it stands as
whole words, in any case. The phone reads the same rules from GET /v1/hearing, biases its own
recognizer towards the right forms and fixes what it heard the same way.
"""

import re

from . import settings


def parse(raw):
    rules = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        right, _, wrongs = line.partition("=")
        right = right.strip()
        wrong = [w.strip() for w in wrongs.split(",") if w.strip() and w.strip().lower() != right.lower()]
        if right and wrong:
            rules.append({"right": right, "wrong": wrong})
    return rules


def rules():
    return parse(settings.get("hearing.words"))


def fix(text, table=None):
    if not text:
        return text
    for rule in rules() if table is None else table:
        for wrong in sorted(rule["wrong"], key=len, reverse=True):
            pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in wrong.split()) + r"(?!\w)"
            text = re.sub(pattern, lambda _match, right=rule["right"]: right, text, flags=re.IGNORECASE)
    return text
