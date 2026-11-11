import os
from pathlib import Path

DEFAULT_PERSONA_MD = Path("Projects") / "diablo" / "PERSONA.md"
SECTIONS = ("Tongues", "Memory")

_cache = {}


def persona_path(path=None):
    chosen = path or os.environ.get("DIABLO_PERSONA_MD") or os.environ.get("DIABLO_AGENTS_MD")
    if chosen:
        return Path(chosen).expanduser()
    return Path.home() / DEFAULT_PERSONA_MD


def _is_heading(line):
    level = len(line) - len(line.lstrip("#"))
    return 0 < level <= 2 and line[level:].startswith(" ")


def _heading_title(line):
    return line.lstrip("#").strip()


def extract_sections(text, wanted=SECTIONS):
    bodies = []
    current = None
    lines = []
    for line in text.splitlines():
        if _is_heading(line):
            if current is not None:
                bodies.append("\n".join(lines).strip())
            title = _heading_title(line)
            current = title if line.startswith("## ") and title in wanted else None
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        bodies.append("\n".join(lines).strip())
    return "\n\n".join(body for body in bodies if body)


def persona_text(path=None):
    target = persona_path(path)
    try:
        mtime = target.stat().st_mtime
    except OSError:
        _cache.pop(str(target), None)
        return ""
    key = str(target)
    cached = _cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return ""
    result = extract_sections(text)
    _cache[key] = (mtime, result)
    return result
