import json
import logging
import os
import queue
import re
import select
import subprocess
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .brains import (
    Brains,
    ModelError,
    ProviderDown,
    ProviderError,
    RoundTimeout,
    resolve_brain,
)
from voice import hubclient
from voice.config import load_config

log = logging.getLogger("diablo.pool")

STATE_DIR = Path(os.environ.get("DIABLO_POOL_STATE_DIR") or Path.home() / ".local/state/diablo")
TASKS_DIR = STATE_DIR / "tasks"
RAPHAEL_IN = STATE_DIR / "raphael.in"
THINK_IN = STATE_DIR / "think.in"
FAST_IN = STATE_DIR / "fast.in"
LANG_OK = Path(__file__).resolve().parent.parent / "deploy" / "lang_ok.py"
NOTIFY = Path(__file__).resolve().parent.parent / "deploy" / "task-notify.py"
MAX_WORKERS = int(os.environ.get("DIABLO_MAX_WORKERS", "4"))
ROUND_TIMEOUT = float(os.environ.get("DIABLO_ROUND_TIMEOUT", "600"))
COORD_TIMEOUT = 300.0
RERUN_TIMEOUT = 300.0
OUT_TAIL = 1500
LINE_LIMIT = 4000
HISTORY_KEEP = 30

RAPH_PROMPT = (
    "You are Raphael, the Lord of Wisdom from That Time I Got Reincarnated as a Slime, "
    "the coordinator of Diablo's works. Marcus speaks through his fast tongue; his words "
    "arrive below. You never do the work yourself, you direct it. Your commands, run with "
    "your shell tool: diablo tasks list --json shows the board; diablo tasks add NAME -- "
    "PROMPT gives a Diablo worker a task, it runs in the background parallel with everything "
    "else, and workers see only the prompt, so make each one self-contained with file paths "
    "and context; diablo tasks tell ID MESSAGE pushes new information into a running task; "
    "diablo tasks show ID shows one. For work: create the task or tasks, then end your reply "
    "with exactly one final line of the form ANSWER: one short spoken line, in the language "
    "Marcus used, saying what is underway. For requests already shaped like TASK: name | "
    "instructions, add that task as given. For requests shaped like THINK: question, answer "
    "it yourself for speech, at most four spoken sentences. For questions needing no worker, "
    "create no tasks and simply end with ANSWER: the reply, written for the ear, no markdown, "
    "no dashes. Whatever happens, your final line always starts with ANSWER:."
)

WORKER_PERSONA = (
    "You are a Diablo, a worker demon dispatched by Raphael, the Lord of Wisdom. "
    "Your report is spoken aloud: plain sentences written for the ear, no markdown, "
    "no lists, no dashes."
)

INTENTS = {
    "think": "This message arrived as a THINK line: answer it yourself for speech.",
    "task": "This message arrived as a TASK line: set the workers on it as given.",
}
DEFAULT_INTENT = "This message is forwarded talk: judge whether it needs a worker, an answer, or both."

RETAKE_NAME = "(retake)"
RETAKE_TEXT = (
    "Your previous answer came out in the wrong language. Answer it again, "
    "only in English, Japanese or Danish. The request was: "
)
SILENT_APOLOGY = "Raphael fell silent, sir. Ask him again."
TONGUE_APOLOGY = "Raphael answered in a tongue I dare not speak, sir. Ask him again."
WORKER_TONGUE_APOLOGY = (
    "The work ran, but my report came out in the wrong language and was discarded. "
    "Ask and I will redo it."
)
MODEL_BROKEN = "The work never started: the worker model setting is broken, sir."

ANSI = re.compile(r"\x1b\[[0-9;]*m")
ANSWER_PREFIX = re.compile(r"^ANSWER:[ \t]*")


def intent_for(kind):
    return INTENTS.get(kind, DEFAULT_INTENT)


def build_coordinator_prompt(text, kind):
    return f"{RAPH_PROMPT}\n\n{intent_for(kind)}\nHis words: {text}"


def build_worker_prompt(prompt):
    return (
        f"{WORKER_PERSONA} Do this work yourself with tools, then report concisely: "
        f"{prompt} Never end with an ANSWER line, a plain report is all Raphael needs."
    )


def build_continuation_prompt(name, notes):
    return (
        f"{WORKER_PERSONA} The task '{name}' is already underway. While it ran, more "
        f"information arrived from Marcus: {notes} Fold the new information in, "
        f"continue the work, and report concisely."
    )


def build_language_prompt(prompt):
    return f"{WORKER_PERSONA} Wrong language: report only in English, Japanese, or Danish. The task was: {prompt}"


def parse_line(line):
    tid, _, rest = line.partition("|")
    name, _, prompt = rest.partition("|")
    return tid.strip(), name.strip(), prompt


def answer_of(text):
    answer = ""
    for line in text.splitlines():
        if line.startswith("ANSWER:"):
            answer = ANSWER_PREFIX.sub("", line, count=1)
    return answer


def strip_ansi(text):
    return ANSI.sub("", text)


def parts_text(parts, joiner="\n"):
    chunks = []
    fallback = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text") or ""
        if not text.strip():
            continue
        if part.get("type") == "text":
            chunks.append(text.strip())
        else:
            fallback.append(text.strip())
    if chunks:
        return joiner.join(chunks)
    return joiner.join(fallback)


def clean_out(parts):
    text = strip_ansi(parts_text(parts, joiner=" "))
    text = re.sub(r"\s+", " ", text).strip()
    return text[-OUT_TAIL:]


def resolve_model(client, model):
    """base, key and model id for a hub brain string, resolved against settings."""
    try:
        return resolve_brain(client.settings(), model)
    except ModelError as error:
        raise ModelError(f"{error} (brain: {model!r})") from None


class Models:
    def __init__(self, cfg, get_settings=None):
        self.cfg = cfg
        self.get_settings = get_settings or hubclient.get_settings
        self.live = {**cfg, **self.get_settings(cfg)}
        self.llm = self.live.get("llm_model") or ""
        self.worker = self.live.get("rank.baron") or self.live.get("llm_model") or ""

    def refresh(self):
        settings = self.get_settings(self.cfg)
        if settings:
            self.live = {**self.cfg, **settings}
            self.llm = self.live.get("llm_model") or self.llm
            self.worker = self.live.get("rank.baron") or self.live.get("llm_model") or self.worker
        return self


class Task:
    def __init__(self, tid, name, prompt):
        self.id = tid
        self.name = name
        self.prompt = prompt
        self.sid = ""
        self.aborted = False


class Pool:
    def __init__(self, client, models, state=STATE_DIR, max_workers=MAX_WORKERS,
                 round_timeout=ROUND_TIMEOUT, language_ok=None, notify=None, spawn=None):
        self.client = client
        self.models = models
        self.state = Path(state)
        self.tasks_dir = self.state / "tasks"
        self.fast_in = self.state / "fast.in"
        self.max_workers = max_workers
        self.round_timeout = round_timeout
        self.language_ok = language_ok or self._language_ok
        self.notify = notify or self._notify
        self.spawn = spawn or self._spawn
        self.queue = deque()
        self.active = {}
        self.lock = threading.Lock()

    def enqueue(self, line):
        tid, name, prompt = parse_line(line)
        if not tid:
            log.warning("dropped a malformed line: %r", line[:80])
            return None
        task = Task(tid, name, prompt)
        with self.lock:
            self.queue.append(task)
        self._queue_files(task)
        self.pump()
        return task

    def pump(self):
        with self.lock:
            starts = []
            while self.queue and len(self.active) < self.max_workers:
                task = self.queue.popleft()
                self.active[task.id] = task
                starts.append(task)
        for task in starts:
            self.spawn(task)

    def _spawn(self, task):
        threading.Thread(target=self.run_task, args=(task,), name=f"worker-{task.id}", daemon=True).start()

    def run_task(self, task):
        try:
            self._run_task(task)
        except Exception:
            log.exception("worker for %s crashed", task.id)
        finally:
            self._release(task)

    def _release(self, task):
        with self.lock:
            self.active.pop(task.id, None)
        self.pump()

    def _run_task(self, task):
        if self._status(task.id) == "canceled":
            log.info("task finished canceled: %s, work discarded", task.name)
            return
        try:
            model = resolve_model(self.client, self.models.refresh().worker)
        except ModelError as error:
            log.error("task %s never ran: %s", task.id, error)
            self._finish(task, MODEL_BROKEN, None)
            return
        task.sid = self.client.create_session(task.name or task.id)
        self._start_files(task)
        log.info("task started: %s (%s)", task.name, task.id)
        threading.Thread(target=self._beat, args=(task,), daemon=True).start()
        inbox = self.tasks_dir / f"{task.id}.inbox"
        out = self.tasks_dir / f"{task.id}.out"
        prompt = build_worker_prompt(task.prompt)
        parts = []
        while True:
            if self._status(task.id) == "canceled":
                log.info("task finished canceled: %s, work discarded", task.name)
                return
            try:
                response = self._round(task, prompt, model)
            except ProviderDown:
                continue
            if response is not None and response.get("parts"):
                parts = response["parts"]
            if self._status(task.id) == "canceled":
                log.info("task finished canceled: %s, work discarded", task.name)
                return
            notes = self._drain_inbox(inbox)
            if notes:
                prompt = build_continuation_prompt(task.name, notes)
                log.info("continuation round: %s (%s)", task.name, task.id)
                continue
            self._finish(task, clean_out(parts), model)
            return

    def _round(self, task, prompt, model):
        for attempt in (1, 2, 3):
            try:
                response = self.client.message(task.sid, prompt, model, self.round_timeout)
            except RoundTimeout:
                log.warning("round timed out for %s, the session survives", task.name)
                response = self.client.last_assistant(task.sid)
                self._append_out(task, response)
                return response
            except ProviderDown as error:
                log.warning("round failed for %s: %s", task.name, error)
                if attempt < 3:
                    time.sleep(5 * attempt)
                    continue
                raise
            except ProviderError as error:
                log.warning("round failed for %s: %s", task.name, error)
                return None
            self._append_out(task, response)
            return response
        raise ProviderDown("round could not reach the provider")

    def _finish(self, task, result, model):
        try:
            (self.tasks_dir / f"{task.id}.status").write_text("done")
        except OSError as error:
            log.error("status write failed for %s: %s", task.id, error)
        log.info("task finished: %s", task.name)
        if result and model is not None and not self.language_ok(result):
            log.info("task result in the wrong language, asking for it again: %s", task.name)
            result = self._rerun_language(task, model, result)
        try:
            (self.tasks_dir / f"{task.id}.clean").write_text(result)
        except OSError as error:
            log.error("clean write failed for %s: %s", task.id, error)
            return
        self._dispatch_result(task, result)
        self.notify(task.id, task.name, result)

    def _rerun_language(self, task, model, result):
        try:
            sid = self.client.create_session(task.name or task.id)
            response = self.client.message(sid, build_language_prompt(task.prompt), model, RERUN_TIMEOUT)
        except (ProviderDown, ProviderError) as error:
            log.warning("language rerun failed for %s: %s", task.name, error)
            return WORKER_TONGUE_APOLOGY
        try:
            (self.tasks_dir / f"{task.id}.out").write_text(json.dumps(response) + "\n")
        except OSError as error:
            log.warning("out rewrite failed for %s: %s", task.id, error)
        rerun = clean_out(response.get("parts", []))
        if not self.language_ok(rerun):
            return WORKER_TONGUE_APOLOGY
        return rerun or result

    def _beat(self, task):
        out = self.tasks_dir / f"{task.id}.out"
        while self.active.get(task.id) is task:
            try:
                out.touch()
            except OSError:
                pass
            time.sleep(60)

    def _start_files(self, task):
        try:
            (self.tasks_dir / f"{task.id}.status").write_text("running")
            (self.tasks_dir / f"{task.id}.name").write_text(task.name)
            (self.tasks_dir / f"{task.id}.prompt").write_text(task.prompt)
            (self.tasks_dir / f"{task.id}.inbox").write_text("")
        except OSError as error:
            log.error("task files failed for %s: %s", task.id, error)

    def _queue_files(self, task):
        try:
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
            (self.tasks_dir / f"{task.id}.status").write_text("queued")
            (self.tasks_dir / f"{task.id}.name").write_text(task.name)
            (self.tasks_dir / f"{task.id}.prompt").write_text(task.prompt)
            (self.tasks_dir / f"{task.id}.inbox").touch()
        except OSError as error:
            log.error("queue files failed for %s: %s", task.id, error)

    def _status(self, tid):
        try:
            return (self.tasks_dir / f"{tid}.status").read_text().strip()
        except OSError:
            return ""

    def _drain_inbox(self, inbox):
        try:
            notes = inbox.read_text()
            inbox.write_text("")
        except OSError:
            return ""
        return notes.strip()

    def _append_out(self, task, response):
        try:
            with (self.tasks_dir / f"{task.id}.out").open("a", encoding="utf-8") as out:
                out.write(json.dumps(response) + "\n")
        except OSError as error:
            log.error("out append failed for %s: %s", task.id, error)

    def _dispatch_result(self, task, result):
        line = f"TASKDISPATCH:{task.id}|{task.name}|{result}\n"
        try:
            fd = os.open(self.fast_in, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            log.warning("fast.in unreachable for %s: %s", task.name, error)
            return
        try:
            os.write(fd, line.encode())
        except OSError as error:
            log.warning("fast.in write failed for %s: %s", task.name, error)
        finally:
            os.close(fd)

    def _language_ok(self, text):
        try:
            proc = subprocess.run(
                [sys.executable, str(LANG_OK)], input=text, capture_output=True,
                timeout=30, check=False, text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            log.warning("language check failed, letting the reply pass: %s", error)
            return True
        return proc.returncode == 0

    def _notify(self, tid, name, result):
        try:
            subprocess.run(
                [sys.executable, str(NOTIFY), tid, name], input=result,
                capture_output=True, timeout=30, check=False, text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            log.warning("task-notify failed for %s: %s", tid, error)

    def cancel_sweep(self):
        with self.lock:
            active = list(self.active.values())
        for task in active:
            if task.aborted:
                continue
            if self._status(task.id) == "canceled":
                task.aborted = True
                log.info("canceling: %s (%s)", task.name, task.id)
                self.client.abort(task.sid)


class Coordinator:
    def __init__(self, client, models, state=STATE_DIR, language_ok=None, timeout=COORD_TIMEOUT):
        self.client = client
        self.models = models
        self.state = Path(state)
        self.tasks_dir = self.state / "tasks"
        self.fast_in = self.state / "fast.in"
        self.language_ok = language_ok or self._language_ok
        self.timeout = timeout
        self.retake = queue.Queue()
        self.sid = ""

    def process(self, line):
        line = line[:LINE_LIMIT]
        tid, name, text = parse_line(line)
        if not tid:
            log.warning("raphael dropped a malformed line: %r", line[:80])
            return
        kind = self._kind(tid)
        log.info(">> [%s] %s", name, text[:120])
        prompt = build_coordinator_prompt(text, kind)
        answer = answer_of(parts_text(self._ask(prompt).get("parts", [])))
        log.info("raphael: %s", answer or "(no answer line)")
        if not answer:
            if kind == "msg":
                self._raphael_line(tid, name, SILENT_APOLOGY)
            return
        if kind == "think":
            self._finish_think(tid, name, text, answer)
            return
        if kind == "msg" and not self.language_ok(answer):
            if name.endswith(RETAKE_NAME):
                log.warning("still the wrong language, giving up on this answer")
                self._raphael_line(tid, name, TONGUE_APOLOGY)
            else:
                log.warning("raphael's answer in the wrong language, asking him again")
                self.retake.put(f"{tid}|{name} {RETAKE_NAME}|{RETAKE_TEXT}{text}")
            return
        if kind == "msg":
            self._raphael_line(tid, name, answer)

    def _finish_think(self, tid, name, text, answer):
        try:
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
            (self.tasks_dir / f"{tid}.name").write_text(name)
            (self.tasks_dir / f"{tid}.prompt").write_text(text)
            (self.tasks_dir / f"{tid}.clean").write_text(answer)
            (self.tasks_dir / f"{tid}.status").write_text("done")
        except OSError as error:
            log.error("think files failed for %s: %s", tid, error)
            return
        log.info("raphael answered: %s", name)

    def _kind(self, tid):
        try:
            return (self.tasks_dir / f"{tid}.kind").read_text().strip() or "msg"
        except OSError:
            return "msg"

    def _ask(self, prompt):
        out = {}
        for attempt in (1, 2, 3):
            out = self._run(prompt)
            if out.get("parts"):
                return out
            log.warning("raphael stalled, trying again: %d", attempt)
        log.warning("rescuing: fresh raphael session")
        self.sid = ""
        self._arm()
        return self._run(prompt)

    def _run(self, prompt):
        if not self.sid:
            self._arm()
        if not self.sid:
            return {}
        try:
            model = resolve_model(self.client, self.models.refresh().llm)
        except ModelError as error:
            log.error("raphael has no usable model: %s", error)
            return {}
        try:
            response = self.client.message(self.sid, prompt, model, self.timeout)
        except RoundTimeout:
            log.warning("raphael round timed out, the session survives")
            return self.client.last_assistant(self.sid)
        except (ProviderDown, ProviderError) as error:
            log.warning("raphael run failed: %s", error)
            return {}
        self.client.trim(self.sid, HISTORY_KEEP)
        return response

    def _arm(self):
        self.sid = self.client.create_session("raphael")
        log.info("raphael armed: %s", self.sid)

    def _raphael_line(self, tid, name, answer):
        line = f"RAPHAEL:{tid}|{name}|{answer}\n"
        try:
            fd = os.open(self.fast_in, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            log.warning("fast.in unreachable for raphael: %s", error)
            return
        try:
            os.write(fd, line.encode())
        except OSError as error:
            log.warning("fast.in write failed for raphael: %s", error)
        finally:
            os.close(fd)

    def _language_ok(self, text):
        try:
            proc = subprocess.run(
                [sys.executable, str(LANG_OK)], input=text, capture_output=True,
                timeout=30, check=False, text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            log.warning("language check failed, letting the reply pass: %s", error)
            return True
        return proc.returncode == 0


class FifoReader:
    def __init__(self, path, sink, name):
        self.path = Path(path)
        self.sink = sink
        self.name = name
        self.fd = None

    def start(self):
        thread = threading.Thread(target=self.loop, name=f"fifo-{self.name}", daemon=True)
        thread.start()
        return thread

    def _open(self):
        while self.fd is None:
            try:
                if not self.path.exists():
                    os.mkfifo(self.path)
                self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
            except OSError as error:
                log.warning("%s: fifo open failed, retrying: %s", self.name, error)
                time.sleep(2)

    def _close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def loop(self):
        self._open()
        buffer = b""
        while True:
            try:
                ready, _, _ = select.select([self.fd], [], [], 1.0)
                if not ready:
                    continue
                chunk = os.read(self.fd, 65536)
            except OSError as error:
                log.warning("%s: fifo read failed, reopening: %s", self.name, error)
                self._close()
                buffer = b""
                self._open()
                continue
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace")
                if line.strip():
                    self.sink.put(line)


def setup_logging(state, name="pool.log"):
    state.mkdir(parents=True, exist_ok=True)
    plain = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger("diablo")
    root.setLevel(logging.INFO)
    file_handler = RotatingFileHandler(state / name, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(plain)
    root.addHandler(file_handler)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(plain)
    root.addHandler(console)


def coordinator_loop(coordinator, lines):
    while True:
        line = lines.get()
        try:
            coordinator.process(line)
        except Exception:
            log.exception("raphael step failed")


def cancel_watch(pool):
    while True:
        try:
            pool.cancel_sweep()
        except Exception:
            log.exception("cancel sweep hiccup")
        time.sleep(2)


def main():
    state = Path(os.environ.get("DIABLO_POOL_STATE_DIR") or STATE_DIR)
    setup_logging(state)
    cfg = load_config()
    models = Models(cfg)
    client = Brains(cfg)
    pool = Pool(
        client, models, state=state,
        max_workers=int(os.environ.get("DIABLO_MAX_WORKERS", MAX_WORKERS)),
        round_timeout=float(os.environ.get("DIABLO_ROUND_TIMEOUT", ROUND_TIMEOUT)),
    )
    coordinator = Coordinator(client, models, state=state)
    raphael_lines = queue.Queue()
    think_lines = queue.Queue()
    FifoReader(state / "raphael.in", raphael_lines, "raphael.in").start()
    FifoReader(state / "think.in", think_lines, "think.in").start()
    threading.Thread(target=coordinator_loop, args=(coordinator, raphael_lines), name="raphael", daemon=True).start()
    threading.Thread(target=cancel_watch, args=(pool,), name="cancel", daemon=True).start()
    log.info(
        "pool online: up to %d workers, round timeout %.0fs, direct provider calls",
        pool.max_workers, pool.round_timeout,
    )
    while True:
        try:
            line = think_lines.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            pool.enqueue(line)
        except Exception:
            log.exception("enqueue failed")
