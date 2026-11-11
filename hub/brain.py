import datetime
import os
from pathlib import Path

DEFAULT_ROOT = Path("Obsidian") / "Agent"
SKIP_DIRS = {".obsidian", ".trash", ".stversions"}
SKIP_NAME = "sync-conflict"
SEARCH_LIMIT = 3000
LINE_LIMIT = 200
LINES_PER_NOTE = 5
READ_LIMIT = 6000
NOTHING = "nothing found"
MISSING = "no such note"
LOG_FRONTMATTER = "---\ntype: log\nstatus: current\nupdated: {date}\n---\n"


def root():
    chosen = os.environ.get("DIABLO_BRAIN")
    if chosen:
        return Path(chosen).expanduser()
    return Path.home() / DEFAULT_ROOT


def _skipped(rel):
    return any(part in SKIP_DIRS or SKIP_NAME in part for part in rel.parts)


def notes(base=None):
    base = base or root()
    if not base.is_dir():
        return []
    found = []
    for path in sorted(base.rglob("*.md")):
        if path.is_file() and not _skipped(path.relative_to(base)):
            found.append(path)
    return found


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def search(query, limit=8):
    needle = (query or "").strip().lower()
    if not needle:
        return NOTHING
    entries = []
    hits = 0
    for path in notes():
        lines = [line.strip() for line in _read_text(path).splitlines() if needle in line.lower()]
        if not lines:
            continue
        hits += 1
        for line in lines[:LINES_PER_NOTE]:
            entries.append(f"{path.stem}: {line[:LINE_LIMIT]}")
        if hits >= limit:
            break
    if not entries:
        return NOTHING
    return "\n".join(entries)[:SEARCH_LIMIT]


def _resolve(name):
    base = root()
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    candidates = [cleaned]
    if not cleaned.endswith(".md"):
        candidates.append(cleaned + ".md")
    for candidate in candidates:
        path = (base / candidate).resolve()
        if path.is_file() and path.is_relative_to(base.resolve()):
            return path
    stem = cleaned.lower()
    for path in notes(base):
        if path.stem.lower() == stem:
            return path
    return None


def read(name):
    path = _resolve(name)
    if path is None:
        return MISSING
    text = _read_text(path)
    if len(text) > READ_LIMIT:
        return text[:READ_LIMIT] + "\n[truncated]"
    return text


def log(text):
    now = datetime.datetime.now()
    line = " ".join((text or "").split())
    if not line:
        return "nothing to log"
    path = root() / "Log" / f"{now:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_text(path) if path.exists() else ""
    if not existing:
        existing = LOG_FRONTMATTER.format(date=f"{now:%Y-%m-%d}")
    elif not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + f"- {now:%H:%M} hub: {line}\n", encoding="utf-8")
    return "logged"
