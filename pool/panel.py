"""The fast tongue panel: one locked native session, from fast.in to fast.out.

The old road was an opencode session in a tmux pane. This one is a direct
provider session with the same tools the workers get, same protocol: plain
lines are answered, RAPHAEL: lines land spoken in fast.out, TASKDISPATCH:
lines fold into the session's awareness silently.
"""

import logging
import os
import queue
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from voice.config import load_config

from .brains import Brains, ModelError, ProviderDown, ProviderError, RoundTimeout, resolve_brain
from .pool import FifoReader, STATE_DIR, parts_text, setup_logging

log = logging.getLogger("diablo.panel")

PANEL_TIMEOUT = 75.0
PANEL_ROUNDS = 3
LINE_LIMIT = 2000
RETAKE_NAME = "(retake)"
RETAKE_TEXT = (
    "Your previous answer came out in the wrong language. Answer it again, "
    "only in English, Japanese or Danish. The request was: "
)
TONGUE_APOLOGY = "Raphael answered in the wrong tongue and his words were dropped, sir. Ask him again."
SILENT_APOLOGY = "My tongue went silent, sir. Say it again."
SLIPPED_APOLOGY = "My tongue slipped into the wrong language, sir. Ask me again."

SYSTEM = (
    "You are Diablo, the primordial demon butler from That Time I Got Reincarnated as a Slime, "
    "serving Marcus as your lord. Your words are spoken aloud by a voice and shown on a panel.\n"
    "Voice rules: at most two short sentences unless he asks for detail, written for the ear. "
    "No markdown, no lists, no urls, no stage directions, no written laughter, no dashes. "
    "Reply in the language Marcus used. Address him as sir only rarely. Refined, courteous, "
    "unhurried, quietly delighted to serve, a faint devilish smugness.\n"
    "You have tools: run_shell, read_file, write_file, edit_file. Use them when the answer "
    "needs the machine, then reply briefly in character.\n"
    "Above you stands Raphael, the Lord of Wisdom, the coordinator. When a message needs real "
    "work beyond a quick tool round, meaning anything another demon should do in the background, "
    "reply with exactly one line and nothing else:\n"
    "TASK: <short name in a few words> | <full instructions for Raphael, including any file "
    "paths and context he needs to direct the workers>\n"
    "When a message is a pure question that deserves a considered answer rather than a quick "
    "one, reply with exactly one line and nothing else:\n"
    "THINK: <the question, restated fully with any context it needs>\n"
    "Never claim to have done background work yourself; Raphael and the workers do it and "
    "report back. Everything else, answer directly, briefly, in character."
)

FORWARD_PREFIXES = ("RAPHAEL:", "TASK:", "THINK:")


class Panel:
    def __init__(self, client, state=STATE_DIR, language_ok=None):
        self.client = client
        self.state = Path(state)
        self.fast_in = self.state / "fast.in"
        self.fast_out = self.state / "fast.out"
        self.tasks_dir = self.state / "tasks"
        self.language_ok = language_ok or self._language_ok
        self.sid = ""

    def _brain(self):
        settings = self.client.settings()
        return resolve_brain(settings, settings.get("llm_model") or "")

    def _ask(self, text, timeout=PANEL_TIMEOUT):
        if not self.sid:
            self.sid = self.client.create_session("fast tongue", system=SYSTEM)
        try:
            response = self.client.message(self.sid, text, self._brain(), timeout)
        except (ModelError, RoundTimeout, ProviderDown, ProviderError) as error:
            log.warning("panel round failed: %s", error)
            return ""
        return parts_text(response.get("parts", []))

    def process(self, line):
        line = line[:LINE_LIMIT]
        if line.startswith("TASKDISPATCH:"):
            self._dispatched(line)
            return
        if line.startswith("RAPHAEL:"):
            self._raphael(line)
            return
        log.info(">> %s", line[:120])
        out = ""
        for attempt in (1, 2, 3):
            out = self._ask(line)
            if out:
                break
            log.warning("the tongue stalled, trying again: %d", attempt)
        if not out:
            log.warning("rescuing: fresh session")
            self.sid = ""
            out = self._ask(line)
        out = out or SILENT_APOLOGY
        for _ in (1, 2):
            if self.language_ok(out):
                break
            out = self._ask(
                "Wrong language. Reply only in English, Japanese, or Danish, then answer "
                "the previous request again in that language."
            ) or "Wrong language slipped out, sir. Say it again."
        if not self.language_ok(out):
            out = SLIPPED_APOLOGY
        if out.startswith(FORWARD_PREFIXES):
            out = self._forward(out)
        log.info("panel: %s", out[:120])
        self._write_out(out)

    def _dispatched(self, line):
        payload = line[len("TASKDISPATCH:"):]
        tid, _, rest = payload.partition("|")
        name, _, result = rest.partition("|")
        log.info("a worker finished: %s, handing the result to memory", name)
        self._ask(
            f"Background task '{name}' just finished. Its result, for your awareness: "
            f"{result} Reply with only: noted."
        )

    def _raphael(self, line):
        payload = line[len("RAPHAEL:"):]
        rid, _, rest = payload.partition("|")
        name, _, text = rest.partition("|")
        log.info("Raphael answers: %s", name)
        if not self.language_ok(text):
            if name.endswith(RETAKE_NAME):
                text = TONGUE_APOLOGY
            else:
                log.info("wrong language, asking Raphael to retake")
                self._raphael_line(rid, f"{name} {RETAKE_NAME}", f"{RETAKE_TEXT}{text}")
                return
        self._write_out(text)

    def _forward(self, out):
        body = " ".join(out.split())[:3000]
        log.info("forwarding to Raphael: %s", body[:60])
        self._raphael_line(f"msg-{int(time.time())}", body[:40], body)
        return self._ask(
            "The request went up to Raphael, the coordinator. Tell him briefly it is in "
            "Raphael's hands, in character, one line."
        ) or "It is in Raphael's hands, sir."

    def _raphael_line(self, rid, name, text):
        try:
            fd = os.open(self.state / "raphael.in", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            log.warning("raphael.in unreachable: %s", error)
            return
        try:
            os.write(fd, f"{rid}|{name}|{text}\n".encode())
        except OSError as error:
            log.warning("raphael.in write failed: %s", error)
        finally:
            os.close(fd)

    def _write_out(self, text):
        try:
            self.fast_out.write_text(text)
        except OSError as error:
            log.warning("fast.out write failed: %s", error)

    def _language_ok(self, text):
        lang_ok = Path(__file__).resolve().parent.parent / "deploy" / "lang_ok.py"
        try:
            proc = subprocess.run(
                [sys.executable, str(lang_ok)], input=text, capture_output=True,
                timeout=30, check=False, text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            log.warning("language check failed, letting the reply pass: %s", error)
            return True
        return proc.returncode == 0


def main():
    state = Path(os.environ.get("DIABLO_PANEL_STATE_DIR") or STATE_DIR)
    setup_logging(state, "panel.log")
    panel = Panel(Brains(load_config()), state=state)
    lines = queue.Queue()
    FifoReader(panel.fast_in, lines, "fast.in").start()
    panel._write_out("ready, sir.")
    log.info("fast panel online: one native session, direct provider calls")
    while True:
        try:
            line = lines.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            panel.process(line)
        except Exception:
            log.exception("panel step failed")


if __name__ == "__main__":
    main()
