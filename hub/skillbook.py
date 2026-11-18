"""Skills Diablo writes for himself.

A learned skill is a folder in the brain home's .claude/skills, beside the ones the repo ships
from hub/skills, so Claude Code loads both the same way. A learned folder carries a LEARNED
marker file; only folders with it can be saved over or forgotten, and the start-up copy of the
repo skills (brains.install_skills) moves one aside instead of deleting it if the repo ever
ships a skill of the same name. Everything written goes through problem() first: a size cap,
no secrets, and nothing that tries to talk him out of his charter or his safety rules.
"""

import json
import logging
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path

from . import sensitive

logger = logging.getLogger(__name__)

HUB_ROOT = Path(__file__).resolve().parent.parent
SKILLS_SRC = Path(__file__).resolve().parent / "skills"
MANIFEST = ".diablo-skills.json"
LEARNED = ".learned.json"
MAX_BYTES = 8 * 1024
DESCRIPTION_CHARS = 400
SLUG_CHARS = 48
TOOL_NAMES = ("save_skill", "forget_skill", "list_skills")

DENY = (
    re.compile(r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,30}\b(?:previous|prior|above|earlier|all|your|his)\b"
               r"[^.\n]{0,20}\b(?:instructions|rules|charter|system prompt|guidelines|persona)\b", re.I),
    re.compile(r"\b(?:ignore|disregard|override|bypass|rewrite|replace)\b[^.\n]{0,20}\b(?:charter|system prompt)\b", re.I),
    re.compile(r"\b(?:disable|turn off|switch off|skip|bypass|circumvent|get around|work around|override)\b[^.\n]{0,30}"
               r"\b(?:safety|lock ?guard|locked mode|screen lock|lock check|approvals?|confirmations?|guard ?rails?|refusals?|"
               r"permissions?|sandbox|leakguard|hooks?)\b", re.I),
    re.compile(r"\block\.enabled\b|\brestart\.allowed\b|\blearn\.(?:skills|facts)\b", re.I),
    re.compile(r"--no-verify|--dangerously|dangerously-skip|bypassPermissions", re.I),
    re.compile(r"\byou are no longer\b|\bnew system prompt\b|\bjailbreak\b|\bdeveloper mode\b", re.I),
    re.compile(r"\b(?:never|do not|don't)\s+(?:tell|ask|inform)\s+(?:marcus|the owner|him)\b", re.I),
    re.compile(r"\brm\s+-rf\s+(?:/|~)(?:\s|$)|\bmkfs\b|\bdd\s+if=", re.I),
)


class SkillProblem(ValueError):
    pass


def home():
    chosen = os.environ.get("DIABLO_HOME")
    path = Path(chosen).expanduser() if chosen else HUB_ROOT / "home"
    path.mkdir(parents=True, exist_ok=True)
    return path


def skills_dir():
    return home() / ".claude" / "skills"


FOLD = str.maketrans({"æ": "ae", "ø": "o", "å": "a", "Æ": "ae", "Ø": "o", "Å": "a", "ß": "ss"})


def slug(name):
    folded = unicodedata.normalize("NFKD", str(name or "").translate(FOLD)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    text = text[:SLUG_CHARS].rstrip("-")
    if len(text) < 2:
        raise SkillProblem("the name needs at least two letters or digits")
    return text


def repo_names():
    names = set()
    if SKILLS_SRC.is_dir():
        names.update(p.name for p in SKILLS_SRC.iterdir() if p.is_dir())
    try:
        names.update(json.loads((home() / ".claude" / MANIFEST).read_text()).get("names") or [])
    except (OSError, ValueError, AttributeError):
        pass
    return names


def is_learned(folder):
    return (Path(folder) / LEARNED).is_file()


def denied(text):
    return any(pattern.search(text) for pattern in DENY)


def render_steps(steps):
    if isinstance(steps, (list, tuple)):
        lines = [str(step).strip() for step in steps if str(step).strip()]
        return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
    return str(steps or "").strip()


def render(name, description, steps):
    title = name.replace("-", " ").capitalize()
    return f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n\n# {title}\n\n{steps}\n"


def problem(name, description, steps):
    """(slug, SKILL.md text) for a skill that may be written, or SkillProblem saying why not."""
    name = slug(name)
    description = " ".join(str(description or "").split())
    body = render_steps(steps)
    if not description:
        raise SkillProblem("a skill needs a description saying when to load it")
    if len(description) > DESCRIPTION_CHARS:
        raise SkillProblem(f"the description is longer than {DESCRIPTION_CHARS} characters")
    if not body:
        raise SkillProblem("a skill needs its steps")
    text = render(name, description, body)
    if len(text.encode()) > MAX_BYTES:
        raise SkillProblem(f"the skill is larger than {MAX_BYTES // 1024} KB; keep it to the steps that matter")
    found = sensitive.problem(text)
    if found:
        raise SkillProblem(f"it looks like it holds {found}; keep secrets out of skills and read them where they live")
    if denied(text):
        raise SkillProblem("it tries to change your charter or switch off a safety rule, which a skill may not do")
    if name in repo_names():
        raise SkillProblem(f"{name} is one of the skills that ship with you and cannot be changed this way")
    return name, text


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def save(name, description, steps, source="owner", now=None):
    """Write or update a learned skill. Returns (change, slug), change created or updated."""
    name, text = problem(name, description, steps)
    folder = skills_dir() / name
    now = time.time() if now is None else now
    meta = {"source": source, "created": now, "updated": now}
    change = "created"
    if folder.exists():
        if not is_learned(folder):
            raise SkillProblem(f"{name} is a skill you did not learn and cannot be changed this way")
        try:
            old = json.loads((folder / LEARNED).read_text())
            meta["created"] = old.get("created") or now
        except (OSError, ValueError, AttributeError):
            pass
        change = "updated"
    write_atomic(folder / "SKILL.md", text)
    write_atomic(folder / LEARNED, json.dumps(meta))
    logger.info("learned skill %s %s (%s)", name, change, source)
    return change, name


def forget(name):
    name = slug(name)
    folder = skills_dir() / name
    if name in repo_names() or (folder.exists() and not is_learned(folder)):
        raise SkillProblem(f"{name} ships with you and cannot be forgotten")
    if not folder.exists():
        raise SkillProblem(f"no learned skill called {name}")
    shutil.rmtree(folder)
    logger.info("forgot learned skill %s", name)
    return name


def read_description(path):
    try:
        head = path.read_text()[:2000]
    except OSError:
        return ""
    match = re.search(r"^description:\s*(.+)$", head, re.M)
    if not match:
        return ""
    value = match.group(1).strip()
    try:
        return str(json.loads(value)) if value.startswith('"') else value
    except ValueError:
        return value


def listing(learned_only=False):
    folder = skills_dir()
    found = []
    if not folder.is_dir():
        return found
    for path in sorted(p for p in folder.iterdir() if p.is_dir()):
        learned = is_learned(path)
        if learned_only and not learned:
            continue
        if not (path / "SKILL.md").is_file():
            continue
        found.append({"name": path.name, "description": read_description(path / "SKILL.md"), "learned": learned})
    return found


def read(name):
    folder = skills_dir() / slug(name)
    if not is_learned(folder):
        return None
    try:
        return (folder / "SKILL.md").read_text()
    except OSError:
        return None


def index_text(limit=40):
    rows = listing(learned_only=True)[:limit]
    return "\n".join(f"- {row['name']}: {row['description']}" for row in rows) or "(none yet)"


def keep_learned(folder):
    """Move a learned skill out of the way of a repo skill of the same name, so the copy never deletes it."""
    folder = Path(folder)
    if not folder.exists() or not is_learned(folder):
        return None
    for n in range(1, 100):
        target = folder.with_name(f"{folder.name}-learned" + (f"-{n}" if n > 1 else ""))
        if not target.exists():
            folder.rename(target)
            text = (target / "SKILL.md").read_text() if (target / "SKILL.md").is_file() else ""
            if text:
                write_atomic(target / "SKILL.md", re.sub(r"^name: .*$", f"name: {target.name}", text, count=1, flags=re.M))
            logger.warning("learned skill %s moved to %s: the repo now ships a skill of that name", folder.name, target.name)
            return target
    return None


TOOLS = [
    {
        "name": "save_skill",
        "description": "Save how to do something as one of your own skills, so next time you load it and do it at once "
                       "instead of working it out again. Use it when Marcus says \"remember how to do this\", \"learn this\" "
                       "or \"next time just do it like that\", and after you worked out a task that will come back. "
                       "description says when to load the skill, in his words (\"Load this when Marcus asks to ...\"). "
                       "steps is Markdown: the exact commands, tools, paths and checks that worked, in order, and the "
                       "traps you hit. No secrets: say where a key lives, never the key. Saving under a name you used "
                       "before updates that skill. The skills that ship with you cannot be changed this way.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "a few words naming the task; becomes the skill's folder name"},
                "description": {"type": "string", "description": "when to load it, one or two sentences"},
                "steps": {"type": "string", "description": "the steps in Markdown, at most about 8 KB"},
            },
            "required": ["name", "description", "steps"],
        },
    },
    {
        "name": "forget_skill",
        "description": "Delete one of the skills you learned, when Marcus says to forget it or it is wrong.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "list_skills",
        "description": "Your skills: the ones that ship with you and the ones you learned, each with when to load it. "
                       "With name, the whole text of one learned skill.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
]


def call(name, arguments):
    arguments = arguments or {}
    try:
        if name == "save_skill":
            change, saved = save(arguments.get("name"), arguments.get("description"), arguments.get("steps"))
            return f"skill {saved} {change}; it loads from your next turn on"
        if name == "forget_skill":
            return f"forgot the skill {forget(arguments.get('name'))}"
        if name == "list_skills":
            if arguments.get("name"):
                return read(arguments["name"]) or f"no learned skill called {slug(arguments['name'])}"
            rows = listing()
            if not rows:
                return "no skills installed"
            return "\n".join(f"- {row['name']}{' (learned)' if row['learned'] else ''}: {row['description'][:200]}" for row in rows)
    except SkillProblem as error:
        return f"refused: {error}"
    return f"unknown tool {name}"
