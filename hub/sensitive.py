"""Text that must never be kept: secrets, card numbers, personal id numbers.

Learned skills and learned facts pass through problem() before they are written, so a key
pasted into a chat or a card number read out loud never ends up in a skill file or the facts
table.
"""

import re

PATTERNS = (
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an API key", re.compile(r"\bsk-(?:ant-|proj-|or-)?[A-Za-z0-9_-]{16,}")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("an AWS key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a Google key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("a bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("a web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("a bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("a password or key", re.compile(
        r"(?i)\b(?:api[ _-]?key|secret|token|password|passwd|pwd|passphrase|kodeord|adgangskode|pin ?code)s?\b"
        r"(?:\s*[=:]\s*[\"']?(?![$<{(\[])[^\s\"']{6,}|\s+(?:is|er)\s+[\"']?(?=[^\s\"']*[0-9!@#%^&*_+=-])[^\s\"']{6,})")),
    ("a personal id number", re.compile(r"\b[0-3]\d[01]\d\d\d-\d{4}\b")),
)
TOKENISH = re.compile(r"[A-Za-z0-9_+/=-]{32,}")
CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")


def luhn(digits):
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def card_number(text):
    for match in CARD.finditer(str(text or "")):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and luhn(digits) and len(set(digits)) > 1:
            return True
    return False


def tokenish(word):
    return (any(c.islower() for c in word) and any(c.isupper() for c in word) and any(c.isdigit() for c in word)
            and "/" not in word.strip("/"))


def problem(text):
    """What secret-looking thing the text holds, in a few words, or None when it is clean."""
    text = str(text or "")
    for name, pattern in PATTERNS:
        if pattern.search(text):
            return name
    if card_number(text):
        return "a card number"
    if any(tokenish(word) for word in TOKENISH.findall(text)):
        return "a token"
    return None
