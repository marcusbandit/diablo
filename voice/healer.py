import hashlib
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

from .llm import chat, LLMError

log = logging.getLogger("diablo.heal")

PROJECT = Path(__file__).resolve().parent.parent
BACKUPS = Path.home() / ".local/state/diablo/heal-backups"
COOLDOWN_S = 3600.0
MAX_HEALS_PER_BOOT = 3

PROMPT = (
    "You are the self-healing maintainer of the Diablo voice assistant, a Python project at "
    "{project}. A runtime error occurred. Here is the traceback and the current content of every "
    "involved source file. Identify the minimal correct fix and return the FULL corrected content "
    "of each file you change, each wrapped as <FILE relative/path.py> ... </FILE>. Change nothing "
    "else, keep style, never add comments explaining the fix. If the error is transient or not in "
    "these files, return nothing.\n\nTRACEBACK:\n{error}\n\n"
)


def _signature(error_text):
    lines = [line for line in error_text.strip().splitlines() if line.strip()]
    return hashlib.sha1(lines[-1].encode()).hexdigest()[:12] if lines else ""


def _files_from_traceback(error_text):
    paths = set()
    for match in re.finditer(r'File "([^"]+)", line \d+', error_text):
        path = Path(match.group(1)).resolve()
        if str(path).startswith(str(PROJECT)) and path.suffix == ".py":
            paths.add(path)
    return sorted(paths)


def _extract_blocks(text):
    blocks = {}
    for match in re.finditer(
        r"<FILE ([^>]+)>\n(.*?)</FILE>", text, re.DOTALL
    ):
        blocks[match.group(1).strip()] = match.group(2)
    return blocks


def heal(error_text, cfg):
    signature = _signature(error_text)
    if not signature:
        return
    if not getattr(heal, "_count", 0) < MAX_HEALS_PER_BOOT:
        log.warning("heal budget spent this boot, skipping")
        return
    state = getattr(heal, "_last", {})
    if time.monotonic() - state.get(signature, -COOLDOWN_S) < COOLDOWN_S:
        log.info("error %s already healed recently, skipping", signature)
        return
    heal._last = state
    heal._count = getattr(heal, "_count", 0) + 1
    threading.Thread(target=_heal, args=(error_text, cfg, signature), daemon=True).start()


def _heal(error_text, cfg, signature):
    files = _files_from_traceback(error_text)
    if not files:
        log.info("healer: no project files in traceback, nothing to do")
        return
    sources = "\n\n".join(
        f"===== {path.relative_to(PROJECT)} =====\n{path.read_text()}" for path in files
    )
    prompt = PROMPT.format(project=PROJECT, error=error_text) + sources
    try:
        out = chat(cfg, [{"role": "user", "content": prompt}], temperature=0.0)
    except LLMError as error:
        log.warning("healer: brain unreachable: %s", error)
        return
    blocks = _extract_blocks(out.get("content") or "")
    if not blocks:
        log.info("healer: no patch produced")
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    for relative, content in blocks.items():
        target = (PROJECT / relative).resolve()
        if not str(target).startswith(str(PROJECT)) or not target.suffix == ".py":
            log.warning("healer: refusing path %s", relative)
            continue
        backup = BACKUPS / f"{target.name}.{stamp}"
        backup.write_text(target.read_text())
        target.write_text(content)
        check = subprocess.run(
            [str(PROJECT / ".venv-voice/bin/python"), "-m", "py_compile", str(target)],
            capture_output=True,
        )
        if check.returncode != 0:
            log.error("healer: patch failed compile, restoring %s", target.name)
            target.write_text(backup.read_text())
            continue
        log.info("healer: patched %s", relative)
    smoke = subprocess.run(
        [
            str(PROJECT / ".venv-voice/bin/python"), "-c",
            "import voice.capture, voice.gate, voice.tts, voice.agent, voice.commands, voice.jevfast, "
            "voice.quick, voice.presence, voice.recordings, voice.feedback, voice.timeline",
        ],
        capture_output=True, cwd=PROJECT,
    )
    if smoke.returncode != 0:
        log.error("healer: smoke import failed, restoring all backups")
        for relative, _content in blocks.items():
            target = (PROJECT / relative).resolve()
            backup = BACKUPS / f"{target.name}.{stamp}"
            if backup.exists():
                target.write_text(backup.read_text())
        return
    log.info("healer: restarting voice loop")
    subprocess.run(["systemctl", "--user", "restart", "diablo-voice"], capture_output=True)