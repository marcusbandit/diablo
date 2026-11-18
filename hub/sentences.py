"""Sentences out of text as it streams, so each one can be spoken the moment it ends.

A sentence ends at . ! or ? followed by a space, or near LIMIT characters for a run-on, at a
comma or a space but never inside a word or a link. Code fences, list items, table rows and
headings are for reading, not for the ear: their text never comes out.
"""

import re

LIMIT = 220
END = re.compile(r"[.!?]+[\"')\]”’]*(?=\s)")
ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "st", "approx", "cf"}
FENCES = ("```", "~~~")
LIST = re.compile(r"(?:[-*+•]|\d{1,3}[.)])\s")
LIST_START = re.compile(r"(?:[-*+•]|\d{1,3}[.)]?)")
HEADING = re.compile(r"#{1,6}\s")
HEADING_START = re.compile(r"#{1,6}")
BREAKS = (", ", "; ", ": ")
LINK = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.)", re.I)


def _kind(line, ended):
    """What a line is from its start: prose, fence, skip (a list item, table row or heading), or
    None while too little of it has arrived to tell."""
    head = line.lstrip()
    if not head:
        return "blank" if ended else None
    if head.startswith(FENCES):
        return "fence"
    if head.startswith("|") or LIST.match(head) or HEADING.match(head):
        return "skip"
    if not ended and (any(f.startswith(head) for f in FENCES) or LIST_START.fullmatch(head)
                      or HEADING_START.fullmatch(head)):
        return None
    return "prose"


def _speakable(text):
    text = " ".join(text.split())
    return text if re.search(r"\w", text) else ""


class Splitter:
    """Feed it text as it streams; it hands back each sentence once it has ended."""

    def __init__(self, limit=LIMIT):
        self.limit = limit
        self._reset()

    def _reset(self):
        self.line = ""
        self.kind = None
        self.fenced = False
        self.prose = ""

    def _classify(self, head, ended):
        if not self.fenced:
            return _kind(head, ended)
        stripped = head.lstrip()
        if stripped.startswith(FENCES):
            return "fence"
        if not ended and len(stripped) < 3 and any(f.startswith(stripped) for f in FENCES):
            return None
        return "skip"

    def feed(self, text):
        out = []
        self.line += text
        while self.line:
            if self.kind is None:
                cut = self.line.find("\n")
                kind = self._classify(self.line[:cut] if cut >= 0 else self.line, cut >= 0)
                if kind is None:
                    break
                self.kind = kind
                if kind != "prose":
                    out += self._flush_prose()
            cut = self.line.find("\n")
            if self.kind == "prose":
                if cut < 0:
                    self.prose, self.line = self.prose + self.line, ""
                    out += self._sentences()
                    break
                self.prose += self.line[:cut] + " "
                out += self._sentences()
            elif cut < 0:
                break
            elif self.kind == "fence":
                self.fenced = not self.fenced
            self.line = self.line[cut + 1:]
            self.kind = None
        return out

    def flush(self):
        """Everything still held, as the last sentence; then a fresh start for the next block."""
        if not self.fenced and self.line.strip() and (self.kind or _kind(self.line, True)) == "prose":
            self.prose += self.line
        out = self._flush_prose()
        self._reset()
        return out

    def _flush_prose(self):
        out = self._sentences()
        rest = _speakable(self.prose)
        self.prose = ""
        return out + ([rest] if rest else [])

    def _boundary(self):
        for found in END.finditer(self.prose):
            before = self.prose[:found.start()]
            word = re.split(r"[\s(\[]", before)[-1].lower() if before else ""
            if word in ABBREVIATIONS or before.count("`") % 2:
                continue
            return found.end()
        return None

    def _runon(self):
        window = self.prose[:self.limit]
        best = max(window.rfind(mark) for mark in BREAKS)
        if best >= self.limit // 2:
            return best + 1
        space = window.rfind(" ")
        straddling = self.prose[space + 1:].split(None, 1)[0] if self.prose[space + 1:].strip() else ""
        if LINK.match(straddling):
            return None
        if space > 0:
            return space
        later = re.search(r"\s", self.prose[self.limit:])
        return self.limit + later.start() if later else None

    def _sentences(self):
        out = []
        while True:
            end = self._boundary()
            if len(self.prose) >= self.limit and (end is None or end > self.limit):
                end = self._runon() or end
            if end is None:
                return out
            piece, self.prose = self.prose[:end], self.prose[end:].lstrip()
            piece = _speakable(piece)
            if piece:
                out.append(piece)


def split(text):
    """The spoken sentences of a whole text, the same ones a stream of it would give."""
    splitter = Splitter()
    return splitter.feed(text or "") + splitter.flush()


def key(sentence):
    return " ".join(re.sub(r"[^\w\s]", " ", str(sentence or "").lower()).split())


def unsaid(text, said):
    """The sentences of text not among those already said, joined for speaking."""
    gone = {key(line) for line in said or []}
    gone.discard("")
    if not gone:
        return " ".join(split(text))
    joined = " " + " ".join(gone) + " "
    kept = [line for line in split(text) if key(line) and key(line) not in gone and f" {key(line)} " not in joined]
    return " ".join(kept)
