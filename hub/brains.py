import asyncio
import base64
import collections
import contextlib
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import busy as busy_turns
from . import agent, charter, context, db, events, hue, jev, lockguard, modules, questions, route, settings, standby, t3link, threads, usage

logger = logging.getLogger(__name__)

BACKENDS = ("claude", "opencode", "api")
MAIN = "main"
IDLE_SECONDS = 900
TURN_SECONDS = 900
READ_LIMIT = 64 * 1024 * 1024
HUB_ROOT = Path(__file__).resolve().parent.parent
STATE = Path.home() / ".local" / "state" / "diablo"


@dataclass
class Turn:
    text: str = ""
    objects: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    backend: str = ""
    model: str = ""
    session_id: str = ""
    error: str = ""
    cost: float = 0.0
    note: str = ""
    touched: str = ""
    modules: list = field(default_factory=list)
    did: str = ""
    threads: list = field(default_factory=list)
    tokens: int = 0
    act: dict = None
    timing: dict = None
    first_text: str = ""
    first_text_at: float = 0.0


def home():
    chosen = os.environ.get("DIABLO_HOME")
    path = Path(chosen).expanduser() if chosen else HUB_ROOT / "home"
    path.mkdir(parents=True, exist_ok=True)
    return path


SKILLS_SRC = Path(__file__).resolve().parent / "skills"


SKILLS_MANIFEST = ".diablo-skills.json"


def skills_digest():
    digest = hashlib.sha256()
    if SKILLS_SRC.is_dir():
        for path in sorted(SKILLS_SRC.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(SKILLS_SRC)).encode() + b"\0")
                digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def install_skills():
    claude_dir = home() / ".claude"
    target = claude_dir / "skills"
    manifest = claude_dir / SKILLS_MANIFEST
    try:
        previous = json.loads(manifest.read_text())
    except (OSError, ValueError):
        previous = {}
    shipped = sorted(p.name for p in SKILLS_SRC.iterdir() if p.is_dir()) if SKILLS_SRC.is_dir() else []
    digest = skills_digest()
    present = all((target / name).is_dir() for name in shipped)
    if previous.get("digest") == digest and previous.get("names") == shipped and present:
        return shipped
    target.mkdir(parents=True, exist_ok=True)
    for name in previous.get("names") or []:
        if name not in shipped and "/" not in name and name not in ("", ".", ".."):
            shutil.rmtree(target / name, ignore_errors=True)
    for name in shipped:
        destination = target / name
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(SKILLS_SRC / name, destination, ignore=shutil.ignore_patterns("__pycache__"))
    write_private(manifest, json.dumps({"digest": digest, "names": shipped}))
    return shipped


def ensure_skills():
    try:
        return install_skills()
    except Exception:
        logger.exception("could not install the brain skills")
        return None


def backend():
    chosen = str(settings.get("brain.backend") or "claude").strip().lower()
    return chosen if chosen in BACKENDS else "claude"


def model_for(name):
    if name == "claude":
        return str(settings.get("brain.claude.model") or "claude-sonnet-5-5")
    if name == "opencode":
        return str(settings.get("brain.opencode.model") or "fireworks-ai/accounts/fireworks/models/glm-5p3-flash")
    return str(settings.get("llm_model") or "")


def effort():
    return str(settings.get("brain.claude.effort") or "medium")


def session_key(name, conv):
    return f"brain.session.{name}.{conv}"


def forget(conv):
    for name in BACKENDS:
        settings.set_setting(session_key(name, conv), "")


def executable(name):
    for candidate in (shutil.which(name), str(Path.home() / ".local" / "bin" / name), f"/usr/bin/{name}"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def mcp_config(conv):
    env = {key: value for key, value in os.environ.items() if key.startswith("DIABLO_")}
    env.update({
        "DIABLO_HUB_DB": os.environ.get("DIABLO_HUB_DB", str(HUB_ROOT / "diablo.db")),
        "DIABLO_CONVERSATION": conv,
        "PYTHONPATH": str(HUB_ROOT),
    })
    servers = {
        "diablo": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "hub.mcp_server"],
            "env": env,
        }
    }
    servers.update(t3link.mcp_servers())
    return {"mcpServers": servers}


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def host_line():
    role = ", standby" if standby.is_standby() else ""
    return f"[running on {socket.gethostname()}{role}]"


def prefix(origin, notes=None):
    stamp = datetime.datetime.now().strftime("%a %d %b %H:%M")
    head = f"[{origin}, {stamp}]\n{host_line()}"
    if notes:
        head += "\n" + "\n".join(notes)
    return head


def usage_log(turn, conv, origin, started, decision=None, extra=None):
    # The split a client can show: how long Jev took, and the whole turn on the hub.
    jev_ms = (decision.jev or {}).get("ms") if decision is not None and decision.jev else None
    turn.timing = {"brain_ms": round((time.time() - started) * 1000), "jev_ms": jev_ms,
                   "backend": turn.backend, "model": turn.model, "tools": len(turn.steps),
                   "tier": decision.tier if decision is not None else None}
    if turn.first_text_at:
        turn.timing["first_text_ms"] = round((turn.first_text_at - started) * 1000)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": time.time(), "conv": conv, "origin": origin, "backend": turn.backend,
            "model": turn.model, "seconds": round(time.time() - started, 1),
            "cost": turn.cost, "error": turn.error[:200], "tools": len(turn.steps),
        }
        if turn.tokens:
            row["ctx"] = turn.tokens
        if extra:
            row.update(extra)
        if decision is not None:
            row["route"] = {"id": decision.id, "subject": decision.subject, "tier": decision.tier, "applied": decision.applied}
        with open(STATE / "usage.jsonl", "a") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError:
        pass


def collect_object(turn, name, arguments):
    short = name.rsplit("__", 1)[-1]
    module = modules.writer(short)
    if module is not None:
        turn.modules.append(module)
    if short.endswith("emit_object"):
        if not agent.object_problem(arguments):
            try:
                turn.objects.append(agent.keep_object(arguments))
            except (OSError, ValueError):
                logger.exception("could not keep the object")
    elif short.endswith("hue_light"):
        turn.modules.append("lights")
        wanted = str((arguments or {}).get("target") or "all").lower().strip()
        turn.touched = "all" if wanted in ("all", "everything", "the room") else hue.TARGETS.get(wanted, "all")
        try:
            lamps = hue.lamp_states()
        except Exception:
            lamps = None
        if lamps:
            turn.objects.append({"kind": "lights", "lamps": lamps})
    elif short.endswith("workspace_todo"):
        ws = str(arguments.get("workspace") or "scratch")
        turn.objects.append({"kind": "todos", "workspace": ws, "todos": db.get_todos(ws)})


def refresh_panels(turn):
    if turn.modules:
        events.publish({"kind": "panel", "modules": list(dict.fromkeys(turn.modules + ["today"]))})


def light_detail(arguments):
    parts = [str(arguments.get("action") or "on"), str(arguments.get("target") or "all")]
    if arguments.get("brightness") not in (None, ""):
        parts.append(f"{arguments['brightness']}%")
    for key in ("color", "temperature"):
        if arguments.get(key):
            parts.append(str(arguments[key]))
    return " ".join(parts)


def step_label(name, arguments):
    short = name.split("__")[-1] if name.startswith("mcp__") else name
    server = name.split("__")[1] if name.startswith("mcp__") and name.count("__") >= 2 else ""
    if short == "hue_light" and isinstance(arguments, dict):
        return f"hue_light: {light_detail(arguments)}"
    detail = ""
    for key in ("title", "command", "query", "description", "url", "pattern", "file_path", "action", "text"):
        value = arguments.get(key) if isinstance(arguments, dict) else None
        if value:
            detail = " ".join(str(value).split())[:90]
            break
    where = f" on {server[3:]}" if server.startswith("t3-") else ""
    return f"{short}{where}: {detail}" if detail else f"{short}{where}"


class ClaudeSession:
    def __init__(self, conv, lane=False, fork_from=None):
        self.conv = conv
        self.laned = lane
        # A side session runs beside a busy one: resumed from it with --fork-session, so it
        # knows the conversation, and never stored as the conversation's session.
        self.side = fork_from is not None
        self.fork_from = fork_from
        self.lane = None
        self.used = False
        self.tokens = 0
        self.proc = None
        self.signature = None
        self.session_id = ""
        self.last_used = time.monotonic()
        self.stderr = collections.deque(maxlen=40)
        self.reader = None
        self.busy = False
        self.stopping = False
        self.model = ""
        self.effort_level = ""
        self.spent = {}

    def alive(self):
        return self.proc is not None and self.proc.returncode is None

    async def _drain_stderr(self):
        try:
            while self.proc and self.proc.stderr:
                line = await self.proc.stderr.readline()
                if not line:
                    return
                self.stderr.append(line.decode(errors="replace").rstrip())
        except Exception:
            return

    async def spawn(self, model, effort_level, resume, fork=False):
        binary = executable("claude")
        if not binary:
            raise RuntimeError("claude is not installed on this machine")
        config_path = STATE / f"mcp-{self.conv}.json"
        write_private(config_path, json.dumps(mcp_config(self.conv)))
        argv = [
            binary, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model,
            "--effort", effort_level,
            "--permission-mode", "bypassPermissions",
            "--allow-dangerously-skip-permissions",
            "--mcp-config", str(config_path),
            "--settings", lockguard.hook_settings(self.conv),
            "--append-system-prompt", charter.charter(),
        ]
        if resume:
            argv += ["--resume", resume] + (["--fork-session"] if fork else [])
            self.session_id = resume
        else:
            self.session_id = str(uuid.uuid4())
            argv += ["--session-id", self.session_id]
        env = dict(os.environ)
        env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"
        env.pop("CLAUDECODE", None)
        self.stderr.clear()
        self.spent = {}
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(home()),
            env=env,
            limit=READ_LIMIT,
            start_new_session=True,
        )
        self.reader = asyncio.create_task(self._drain_stderr())
        self.model, self.effort_level = model, effort_level
        logger.info("claude session %s up for %s (%s, %s, %s)", self.session_id, self.conv, model, effort_level, "resumed" if resume else "fresh")

    async def close(self):
        proc, self.proc = self.proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    def kill(self):
        if self.alive():
            self.stopping = True
            self.proc.kill()

    def current(self):
        signature = hashlib.sha1(json.dumps(mcp_config(self.conv), sort_keys=True).encode()).hexdigest()
        return self.alive() and self.signature == signature, signature

    async def control(self, request):
        """One control request to the idle process; returns the response body."""
        request_id = uuid.uuid4().hex[:12]
        self.proc.stdin.write((json.dumps({"type": "control_request", "request_id": request_id, "request": request}) + "\n").encode())
        await self.proc.stdin.drain()

        async def answer():
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    raise ConnectionResetError("claude exited during a control request")
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                response = event.get("response") or {}
                if event.get("type") == "control_response" and response.get("request_id") == request_id:
                    if response.get("subtype") != "success":
                        raise RuntimeError(str(response.get("error") or response)[:200])
                    return response.get("response") or {}

        return await asyncio.wait_for(answer(), timeout=20)

    async def switch(self, model, effort_level):
        """Move the live process to another model or effort without a respawn."""
        if model != self.model:
            await self.control({"subtype": "set_model", "model": model})
            self.model = model
        if effort_level != self.effort_level:
            await self.control({"subtype": "apply_flag_settings", "settings": {"effortLevel": effort_level}})
            self.effort_level = effort_level

    async def warm(self, model, effort_level, switch=True):
        fresh, signature = self.current()
        if fresh:
            if switch and (model, effort_level) != (self.model, self.effort_level):
                try:
                    await self.switch(model, effort_level)
                except Exception as error:
                    logger.warning("could not switch %s to %s/%s in place (%s), respawning", self.conv, model, effort_level, error)
                else:
                    return False
            else:
                return False
        if self.side:
            stored = self.fork_from
        else:
            stored = (self.session_id if self.used else None) if self.laned else settings.get(session_key("claude", self.conv))
        await self.close()
        await self.spawn(model, effort_level, stored or None, fork=self.side and bool(stored))
        self.signature = signature
        return True

    async def turn(self, content, model, effort_level, on_step=None, on_text=None):
        await self.warm(model, effort_level)
        self.used = True
        if not self.side:
            settings.set_setting(session_key("claude", self.conv), self.session_id)
        result = Turn(backend="claude", model=model, session_id=self.session_id)
        message = {"type": "user", "message": {"role": "user", "content": content}}
        self.busy = True
        self.stopping = False
        self.last_used = time.monotonic()
        try:
            self.proc.stdin.write((json.dumps(message) + "\n").encode())
            await self.proc.stdin.drain()
            await asyncio.wait_for(self._read(result, on_step, on_text), timeout=TURN_SECONDS)
        except asyncio.TimeoutError:
            self.kill()
            result.error = f"the turn ran past {TURN_SECONDS // 60} minutes and was stopped"
        except (BrokenPipeError, ConnectionResetError):
            result.error = "the claude process was gone"
        finally:
            self.busy = False
            self.last_used = time.monotonic()
        if self.stopping:
            result.error = "stopped"
            return result
        if result.error and not self.alive():
            tail = " | ".join(list(self.stderr)[-3:])
            if tail:
                result.error += f" ({tail[:300]})"
        return result

    async def _read(self, result, on_step, on_text=None):
        texts = []
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                await self.proc.wait()
                result.error = result.error or f"claude exited with code {self.proc.returncode}"
                return
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                sid = event.get("session_id")
                if sid:
                    self.session_id = sid
                    result.session_id = sid
                    if not self.side:
                        settings.set_setting(session_key("claude", self.conv), sid)
            elif kind == "assistant":
                used = (event.get("message") or {}).get("usage") or {}
                seen = sum(int(used.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                if seen:
                    self.tokens = seen
                    result.tokens = seen
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text", "").strip():
                        texts.append(block["text"])
                        if not result.first_text:
                            # His first line, written before any tool runs on a spoken turn, is
                            # said aloud at once while the work goes on.
                            result.first_text, result.first_text_at = block["text"].strip(), time.time()
                            if on_text:
                                await on_text(result.first_text)
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "tool")
                        arguments = block.get("input") or {}
                        label = step_label(name, arguments)
                        result.steps.append(label)
                        collect_object(result, name, arguments)
                        if on_step:
                            await on_step(label)
            elif kind == "rate_limit_event":
                record_limits(event.get("rate_limit_info") or {})
            elif kind == "result":
                result.cost = float(event.get("total_cost_usd") or 0.0)
                self.spent = usage.record_claude(self.spent, event)
                final = str(event.get("result") or "").strip()
                if event.get("is_error") or str(event.get("subtype", "")).startswith("error"):
                    result.error = final or str(event.get("subtype") or "error")
                    if event.get("errors"):
                        result.error += f": {str(event['errors'])[:300]}"
                result.text = final or (texts[-1].strip() if texts else "")
                return


def record_limits(info):
    """Keep the subscription's latest usage windows from the claude CLI, for the mail handler's limit warnings."""
    try:
        stored = json.loads(settings.get("limits.claude") or "{}")
    except ValueError:
        stored = {}
    windows = dict(stored.get("windows") or {})
    for name, window in (info.get("unifiedWindows") or {}).items():
        if isinstance(window, dict):
            windows[name] = window
    named = info.get("rateLimitType")
    if named and named not in (info.get("unifiedWindows") or {}):
        merged = dict(windows.get(named) or {})
        merged.update({key: info[key] for key in ("status", "resetsAt", "utilization") if info.get(key) is not None})
        windows[named] = merged
    try:
        settings.set_setting("limits.claude", json.dumps({"windows": windows, "at": time.time()}))
    except Exception:
        logger.exception("could not store the usage limits")


class Lanes:
    """The claude sessions of one conversation, one per thread lane.

    The turn's main thread is its lane. A turn in the lane of the warm session goes
    on in it while its context stays under threads.session_tokens; anything else
    takes the spare, a process spawned ahead with a fresh session, and is seeded
    with the retrieved context instead of the whole history. The lane it left is
    kept warm only up to threads.keep_recent, then closed: seeding is cheaper than
    carrying a long transcript, and every process costs the Pi about 340 MB.
    """

    def __init__(self, conv):
        self.conv = conv
        self.active = None
        self.recent = collections.OrderedDict()
        self.spare = None
        self.filling = None
        self.spawning = asyncio.Lock()

    def all(self):
        return [s for s in [self.active, *self.recent.values(), self.spare] if s is not None]

    def busy_session(self):
        return next((s for s in self.all() if s.busy), None)

    async def fill(self, model, effort_level):
        async with self.spawning:
            if self.spare is not None and self.spare.alive():
                return False
            session = ClaudeSession(self.conv, lane=True)
            await session.warm(model, effort_level, switch=False)
            self.spare = session
            return True

    def fill_soon(self):
        if self.filling is None or self.filling.done():
            self.filling = asyncio.get_running_loop().create_task(self._fill())

    async def _fill(self):
        try:
            await self.fill(model_for("claude"), effort())
        except Exception:
            logger.exception("could not spawn the spare claude session")

    def _park(self, session, lane):
        if session is None:
            return
        keep = int(threads._num("threads.keep_recent", 0))
        if session.lane == lane or keep <= 0 or not session.alive():
            asyncio.get_running_loop().create_task(session.close())
            return
        self.recent[session.lane] = session
        while len(self.recent) > keep:
            _, old = self.recent.popitem(last=False)
            asyncio.get_running_loop().create_task(old.close())

    def current(self):
        """The warm session for a turn that belongs to no thread, like a watcher's: whatever lane is open."""
        if self.active is not None and self.active.alive():
            return self.active
        return self.choose("unfiled", float("inf"))[0]

    def choose(self, lane, cap):
        """(session, seeded) for a turn in this lane."""
        current = self.active
        if current is not None and current.lane == lane and current.alive() and current.tokens < cap:
            return current, False
        parked = self.recent.pop(lane, None)
        if parked is not None:
            if parked.alive() and parked.tokens < cap:
                self._park(current, lane)
                self.active = parked
                return parked, False
            asyncio.get_running_loop().create_task(parked.close())
        session = self.spare if self.spare is not None and self.spare.alive() and not self.spare.used else ClaudeSession(self.conv, lane=True)
        self.spare = None
        session.lane = lane
        self._park(current, lane)
        self.active = session
        return session, True

    async def close(self):
        for session in self.all():
            await session.close()
        self.active, self.spare = None, None
        self.recent.clear()


_lanes = {}


def lanes_for(conv):
    if conv not in _lanes:
        _lanes[conv] = Lanes(conv)
    return _lanes[conv]


def laned(conv):
    return conv == MAIN and threads.enabled() and backend() == "claude"


_sessions = {}
_side = collections.defaultdict(set)
# The requests a conversation's Claude is working on right now, oldest first: what Jev
# weighs a new spoken line against, to queue it behind them or run it beside them.
running_said = collections.defaultdict(list)
_locks = collections.defaultdict(asyncio.Lock)
_running = {}
inflight = collections.Counter()


def lock(conv):
    return _locks[conv]


def busy(conv):
    return lock(conv).locked()


def any_busy():
    return any(held.locked() for held in list(_locks.values()))


def give_back_handover(conv, note):
    key = f"handover.{conv}"
    newer = settings.get(key)
    settings.set_setting(key, f"{note}\n{newer}" if newer else note)


def take_handover(conv):
    key = f"handover.{conv}"
    note = settings.get(key)
    if note:
        settings.set_setting(key, "")
    return note or None


async def run_side(conv, text, images, on_step, decision=None, on_text=None):
    """A spoken request that has nothing to do with what Claude is busy on: a fork of the busy
    session answers it at once, beside the first, and is closed after the one turn."""
    busy = _lanes[conv].busy_session() if conv in _lanes else None
    busy = busy or _sessions.get(conv)
    base = (busy.session_id if busy is not None and busy.session_id else "") or settings.get(session_key("claude", conv)) or ""
    session = ClaudeSession(conv, fork_from=base)
    _side[conv].add(session)
    model, effort_level = (decision.model, decision.effort) if decision else (model_for("claude"), effort())
    try:
        return await session.turn(build_content(text, images), model, effort_level, on_step, on_text)
    finally:
        _side[conv].discard(session)
        await session.close()


def parallel_ok():
    return str(settings.get("voice.parallel") or "1").strip().lower() not in ("0", "off", "false", "no")


# Words that point back at something: a line with one waits for what is running. Jev alone
# let "move that program to workspace 3" and "reply to the one from Albert" run beside the
# request they need (measured 2026-11-11, 10 pairs).
POINTS_BACK = re.compile(
    r"\b(it|its|that|those|them|they|there|then|also|too|the one|the other|this one|the result|the same|"
    r"the program|the app|the music one|the window|afterwards|after that)\b|^\W*(and|but|so)\b", re.I)


def runs_beside(answer, said=""):
    """Jev's call on a line said while Claude is busy: True to run it at once beside the busy
    turn, False to queue it. A line that points back always queues, and so does one Jev is
    unsure of: waiting is never wrong, only slower."""
    if POINTS_BACK.search(str(said or "")):
        return False
    if not answer or answer.get("after") != "separate":
        return False
    return answer.get("after_confidence", 0) >= float(settings.get("voice.parallel_threshold") or 0.7)


async def stop(conv):
    for side in list(_side.get(conv, ())):
        side.kill()
    session = _sessions.get(conv)
    if conv in _lanes and _lanes[conv].busy_session() is not None:
        session = _lanes[conv].busy_session()
    task = _running.get(conv)
    if session and session.busy:
        session.kill()
        return True
    if task and not task.done():
        task.cancel()
        return True
    return False


async def reset(conv):
    session = _sessions.pop(conv, None)
    if session:
        await session.close()
    if conv in _lanes:
        await _lanes.pop(conv).close()
    forget(conv)


async def close_all():
    for conv in list(_sessions):
        session = _sessions.pop(conv)
        await session.close()
    for conv in list(_lanes):
        await _lanes.pop(conv).close()


async def keep_warm():
    """Keep the main session spawned and current so a message never pays the cold start."""
    if backend() != "claude" or lock(MAIN).locked() or not standby.serving():
        return
    await asyncio.to_thread(t3link.mcp_servers)
    if laned(MAIN):
        threads.quick_pick.prepare()
        lanes = lanes_for(MAIN)
        async with lock(MAIN):
            try:
                for session in [lanes.active, lanes.spare]:
                    if session is not None and session.alive():
                        await session.warm(session.model or model_for("claude"), session.effort_level or effort(), switch=False)
                if await lanes.fill(model_for("claude"), effort()):
                    logger.info("spare claude session warmed")
            except Exception:
                logger.exception("could not warm the claude lanes")
        return
    session = _sessions.get(MAIN)
    if session is None:
        session = _sessions[MAIN] = ClaudeSession(MAIN)
    async with lock(MAIN):
        try:
            if await session.warm(model_for("claude"), effort(), switch=False):
                logger.info("main claude session warmed")
        except Exception:
            logger.exception("could not warm the main claude session")


async def reap_idle():
    while True:
        await keep_warm()
        await asyncio.sleep(60)
        now = time.monotonic()
        for conv, session in list(_sessions.items()):
            if conv != MAIN and not session.busy and now - session.last_used > IDLE_SECONDS and session.alive():
                logger.info("closing idle claude session for %s", conv)
                await session.close()
        for lanes in list(_lanes.values()):
            for lane, session in list(lanes.recent.items()):
                if not session.busy and now - session.last_used > IDLE_SECONDS:
                    lanes.recent.pop(lane, None)
                    await session.close()


async def restart_all():
    for session in list(_sessions.values()) + [s for lanes in _lanes.values() for s in lanes.all()]:
        if not session.busy:
            await session.close()


def build_content(text, images):
    if not images:
        return text
    blocks = []
    for data, media_type in images:
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode()},
        })
    blocks.append({"type": "text", "text": text})
    return blocks


async def run_claude(conv, text, images, on_step, decision=None, session=None, on_text=None):
    if session is None:
        session = _sessions.get(conv)
    if session is None:
        session = ClaudeSession(conv)
        _sessions[conv] = session
    content = build_content(text, images)
    model, effort_level = (decision.model, decision.effort) if decision else (model_for("claude"), effort())
    result = await session.turn(content, model, effort_level, on_step, on_text)
    if result.error and not result.steps and "No conversation found" in " ".join(session.stderr) + result.error:
        logger.warning("stored claude session for %s is gone, starting fresh", conv)
        settings.set_setting(session_key("claude", conv), "")
        session.used = False
        await session.close()
        said = result
        result = await session.turn(content, model, effort_level, on_step, None if said.first_text else on_text)
        if said.first_text:
            result.first_text, result.first_text_at = said.first_text, said.first_text_at
    return result


def write_opencode_config(conv):
    config = {"$schema": "https://opencode.ai/config.json", "mcp": {}}
    for name, server in mcp_config(conv)["mcpServers"].items():
        if server["type"] == "stdio":
            config["mcp"][name] = {
                "type": "local",
                "command": [server["command"], *server["args"]],
                "environment": server.get("env") or {},
                "enabled": True,
            }
        else:
            config["mcp"][name] = {
                "type": "remote",
                "url": server["url"],
                "headers": server.get("headers") or {},
                "enabled": True,
            }
    write_private(home() / "opencode.json", json.dumps(config, indent=2))
    (home() / "AGENTS.md").write_text(charter.charter() + "\n")


async def run_opencode(conv, text, on_step):
    binary = executable("opencode")
    model = model_for("opencode")
    result = Turn(backend="opencode", model=model)
    if not binary:
        result.error = "opencode is not installed on this machine"
        return result
    write_opencode_config(conv)
    argv = [binary, "run", "--format", "json", "--auto", "-m", model]
    stored = settings.get(session_key("opencode", conv))
    if stored:
        argv += ["-s", stored]
    argv.append(text)
    env = dict(os.environ)
    key = settings.get("llm_api_key")
    if key and "fireworks" in str(settings.get("llm_base_url")):
        env.setdefault("FIREWORKS_API_KEY", key)
    openrouter = settings.get("provider.openrouter.api_key")
    if openrouter:
        env.setdefault("OPENROUTER_API_KEY", openrouter)
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(home()), env=env, limit=READ_LIMIT, start_new_session=True,
    )
    _running[conv] = asyncio.current_task()
    texts = {}
    order = []
    try:
        async def read():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                sid = event.get("sessionID")
                if sid and not result.session_id:
                    result.session_id = sid
                    settings.set_setting(session_key("opencode", conv), sid)
                part = event.get("part") or {}
                if event.get("type") == "text":
                    message_id = part.get("messageID", "")
                    if message_id not in texts:
                        order.append(message_id)
                        texts[message_id] = ""
                    texts[message_id] += part.get("text", "")
                elif event.get("type") == "tool_use":
                    name = part.get("tool", "tool")
                    arguments = (part.get("state") or {}).get("input") or {}
                    label = step_label(name, arguments)
                    result.steps.append(label)
                    collect_object(result, name, arguments)
                    if on_step:
                        await on_step(label)
                elif event.get("type") == "step_finish":
                    result.cost += float(part.get("cost") or 0.0)
                elif event.get("type") == "error":
                    result.error = str(event.get("error") or part)[:400]
        await asyncio.wait_for(read(), timeout=TURN_SECONDS)
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        result.error = f"the turn ran past {TURN_SECONDS // 60} minutes and was stopped"
    except asyncio.CancelledError:
        proc.kill()
        result.error = "stopped"
    finally:
        _running.pop(conv, None)
    if order:
        result.text = texts[order[-1]].strip()
    if proc.returncode not in (0, None) and not result.text and not result.error:
        tail = (await proc.stderr.read()).decode(errors="replace").strip().splitlines()[-2:]
        result.error = f"opencode exited with code {proc.returncode}: {' '.join(tail)[:300]}"
    return result


async def run_api(conv, text, origin):
    result = Turn(backend="api", model=model_for("api"))
    ctx = {"origin": origin, "host_line": host_line(), "chat": None if conv in (MAIN,) else conv, "guard_conv": conv}
    try:
        reply, objects = await asyncio.to_thread(agent.run_agent, text, {}, None, ctx)
    except Exception as error:
        result.error = str(error)[:300]
        return result
    result.text = reply
    result.objects = objects
    return result


def fallback_allowed():
    return str(settings.get("brain.fallback") or "api").strip().lower() in BACKENDS and bool(settings.get("llm_api_key"))


DIRECT_FAILURES = ("refused", "did not answer", "no hue", "unknown light", "must be")
SLOTS = {"corner lamp": "left", "ceiling panel": "up", "wood lamp": "right", "all": "all"}
STEPS = {"small": 15, "normal": 25, "large": 40}
DIM_FROM_OFF = {"small": 35, "normal": 25, "large": 15}
_lamps_cache = [0.0, None]


def lamps_now(max_age=3.0):
    """Lamp states, cached for a few seconds so one message reads the bridge once."""
    if _lamps_cache[1] is not None and time.time() - _lamps_cache[0] <= max_age:
        return _lamps_cache[1]
    try:
        lamps = hue.lamp_states()
    except Exception:
        lamps = []
    _lamps_cache[:] = [time.time(), lamps]
    return lamps


def jev_answer(text, origin, card="", labels=(), machine=False, extra=None):
    """Jev's read of the line, or None: off, a machine origin, no key, slow or failing."""
    if str(settings.get("jev.enabled") or "on").strip().lower() in ("0", "off", "false", "no"):
        return None
    if machine:
        return None
    return jev.decide(text, card, labels, extra=extra) if extra else jev.decide(text, card, labels)


def run_desktop(conv, text, act):
    """A desktop command jev picked: the ears run it on banditbox, nothing to think about here."""
    did = act["did"]
    context.record(conv, text, did, via="direct")
    line = did[0].upper() + did[1:]
    context.answered(text, f"{line}, sir.", did, "direct")
    return Turn(text=f"{line}, sir.", backend="direct", model="desktop", did=did, act=act)


def _pct(lamp):
    return max(1, min(100, round((lamp.get("bri") or 0) * 100 / 254)))


def _name(slot):
    return "all lights" if slot == "all" else "the " + context.LAMP_NAMES.get(slot, slot)


def apply_light(action, slot, lamps, before_event=None):
    """Run one direct light action. Returns (result text, what was done) or None for unknown."""
    kind = action["action"]
    chosen = [lamp for lamp in lamps if slot == "all" or lamp["id"] == slot]
    if kind in ("lights_on", "lights_off"):
        word = kind.split("_")[1]
        return hue.hue_light(word, slot), f"turned {_name(slot)} {word}"
    if kind in ("dim", "brighten"):
        if not chosen:
            return None
        step = STEPS.get(action.get("amount"), 25) * (-1 if kind == "dim" else 1)
        if kind == "dim":
            lit = [lamp for lamp in chosen if lamp.get("on")]
            if not lit:
                level = DIM_FROM_OFF.get(action.get("amount"), 25)
                results = [hue.hue_light("on", lamp["id"], brightness=level) for lamp in chosen]
                return ", ".join(results), f"turned {_name(slot)} on dimmed to {level}%"
            chosen = lit
        results, levels = [], []
        for lamp in chosen:
            start = _pct(lamp) if lamp.get("on") else 0
            level = max(1, min(100, start + step))
            results.append(hue.hue_light("on", lamp["id"], brightness=level))
            levels.append(f"the {lamp['name']} to {level}%")
        return ", ".join(results), ("dimmed " if kind == "dim" else "brightened ") + ", ".join(levels)
    if kind == "color":
        return hue.hue_light("on", slot, color=action["color"]), f"made {_name(slot)} {action['color']}"
    if kind in ("warmer", "cooler"):
        big = action.get("amount") == "large"
        kelvin = (2200 if big else 2600) if kind == "warmer" else (6500 if big else 5000)
        return hue.hue_light("on", slot, temperature=kelvin), f"made {_name(slot)} {kind} ({kelvin} K)"
    if kind == "undo":
        saved = (before_event or {}).get("before") or []
        if not saved:
            return None
        results = []
        for lamp in saved:
            if lamp.get("on"):
                color = lamp.get("color")
                results.append(hue.hue_light("on", lamp["id"], brightness=_pct(lamp), color=None if color in (None, "#f2f1ee") else color))
            else:
                results.append(hue.hue_light("off", lamp["id"]))
        return ", ".join(results), "put the lights back as they were"
    return None


def pick_event(conv, text, events, labels, answer):
    """The event the line is about and whether jev asked for more context.

    Jev decides from the card (the events and their ages). Only when jev is
    off, unreachable or unsure does the time rule in context.last_device pick.
    """
    if answer and answer.get("refers_to") == "earlier" and answer.get("refers_to_confidence", 0) >= 0.3:
        return None, True
    if answer and answer.get("refers_to_confidence", 0) >= 0.6:
        choice = answer.get("refers_to")
        if choice in labels:
            return events[labels.index(choice)], False
        if choice == "none":
            return None, False
    return context.last_device(conv, text), False


CONTEXT_NOTE = ("(He refers to something from earlier that is not in the last few events. Look it up with "
                "thread_history and search the brain before answering; ask him only if it is still unclear.)")


async def run_direct(conv, text, decision, recent=None):
    """Do a plain light change without a model; None when it does not fit or did not work."""
    action = dict(decision.direct)
    if recent is not None and not recent.get("device"):
        recent = None
    if action["action"] == "again":
        if not recent or not recent.get("action") or recent["action"].get("action") in ("again", "undo"):
            return None
        action = {**recent["action"]}
        slot = recent.get("device") or "all"
    else:
        slot = SLOTS.get(action["target"]) or (recent.get("device") if recent and action["target"] == "last" else None)
    if not slot:
        return None
    lamps = await asyncio.to_thread(lamps_now, 0)
    outcome = await asyncio.to_thread(apply_light, action, slot, lamps, recent)
    if outcome is None:
        return None
    result, did = outcome
    if any(word in result for word in DIRECT_FAILURES):
        logger.warning("direct %s failed (%s), handing the line to claude", action, result)
        return None
    _lamps_cache[0] = 0.0
    keep = action if action["action"] != "undo" else None
    context.record(conv, text, did, device=slot, before=lamps, action=keep, via="direct")
    line = did[0].upper() + did[1:]
    context.answered(text, f"{line}, sir.", did, "direct")
    return Turn(text=f"{line}, sir.", backend="direct", model="hue", did=did)


def did_for(turn):
    """What the turn did, in a line: kept in the reply's meta for the next turn's recap."""
    parts = []
    if turn.error and not turn.text:
        parts.append(f"failed: {turn.error[:120]}")
    if turn.steps:
        shown = turn.steps[:6]
        more = f" and {len(turn.steps) - len(shown)} more" if len(turn.steps) > len(shown) else ""
        parts.append("used " + "; ".join(shown) + more)
    if turn.note:
        parts.append(f"answered by the {turn.backend or 'api'} brain after a failure")
    return ". ".join(parts)


def record_turn(conv, said, turn):
    """Leave the brain turn in the short memory: what he asked and what happened."""
    did = turn.did or ("answered: " + turn.text[:90] if turn.text else "")
    context.record(conv, said, did, device=turn.touched or None, via=turn.backend or "claude")


def recap_for(said):
    try:
        return context.recap(skip=said)
    except Exception:
        logger.exception("could not build the recap")
        return None


WORK_WORDS = {
    "small_change": "a small change to an existing project", "app_update": "an app update",
    "big_work": "bigger work in an existing project", "new_project": "a new project",
    "no_project": "a task with no project",
}


def handoff_note(handoff):
    """Tell claude to orchestrate: brief a T3 thread instead of doing the work."""
    return (
        f"(Orchestrate this, do not do the work yourself. The router reads it as {WORK_WORDS.get(handoff['work'], 'project work')}. "
        "Call t3_where, then decide where it runs: a project that only exists on banditbox runs there (if banditbox is down, "
        "call wake_banditbox with the whole order and end your turn); a new project, a no-project task, a small change or an "
        "app update runs on nemurenai, cloning the project there with t3_project_clone if it is missing. "
        f"Launch with modelSelection {{instanceId: claudeAgent, model: {handoff['model']}, options: [{{id: effort, value: {handoff['effort']}}}]}} "
        "unless he named a model, then call watch_t3_thread and reply in a line.)"
    )


def pick_tier(conv, text, images, origin, answer=None, recent_light=False, needs_context=False, machine=False):
    """The router's decision for a claude turn, or None when it is off or broken."""
    try:
        decision = route.route(text, conv=conv, images=bool(images), jev=answer,
                               recent_light=recent_light, needs_context=needs_context, handoff=not machine)
        if machine and route.rank(decision.tier) < route.rank("voice"):
            decision.tier = "voice"
            decision.model, decision.effort = route.model_effort("voice")
    except Exception:
        logger.exception("router failed, the turn runs on the default model")
        return None
    decision.applied = route.enabled()
    route.log(decision, conv=conv, origin=origin, text=text[:160])
    return decision if decision.applied else None


async def ask(conv, text, origin="telegram", images=None, on_step=None, **kwargs):
    inflight[conv] += 1
    shown = busy_turns.start(conv, origin)

    async def stepped(label):
        busy_turns.step(shown, label)
        if on_step:
            await on_step(label)

    try:
        return await _ask(conv, text, origin, images, stepped, **kwargs)
    finally:
        busy_turns.end(shown)
        inflight[conv] -= 1
        if inflight[conv] <= 0:
            del inflight[conv]


def spoken(origin):
    return "spoken aloud" in str(origin or "") or "voice note" in str(origin or "")


def lamps_line(lamps):
    if lamps is None and _lamps_cache[1] is not None and time.time() - _lamps_cache[0] <= 120:
        lamps = _lamps_cache[1]
    return context.room_line(lamps) if lamps else ""


async def file_when_picked(picking, message_id):
    """For a turn that did not wait on the pick, like a direct light change: file it once the pick is in."""
    try:
        result = await picking
        if result.attach and message_id is not None:
            await asyncio.sleep(2)
            threads.retag([message_id], add=result.attach, reason="filed")
            threads.touched(result.attach)
        await threads.settle_late(result, message_id)
    except Exception:
        logger.exception("could not file a direct turn under its thread")


def background(coro):
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


_background = set()


async def _ask(conv, text, origin="telegram", images=None, on_step=None, notes=None, machine=False, message_id=None,
               thread_hint=None, desktop=None, on_text=None):
    started = time.time()
    looking = asyncio.create_task(asyncio.to_thread(lockguard.for_turn, origin))
    chosen = backend()
    said = text
    decision = None
    event = None
    lamps = None
    asked = None
    threaded = conv == MAIN and threads.enabled() and not machine and message_id is not None
    picking = asyncio.create_task(threads.pick(text, spoken=spoken(origin), skip=said, hint=thread_hint)) if threaded else None
    if chosen == "claude":
        events = context.recent(conv)
        lit = any(e.get("device") and e["age"] <= context.related_s() for e in events)
        roomish = lit or route.features(text)["home"] > 0
        lamps = await asyncio.to_thread(lamps_now) if roomish else None
        card, labels = context.card(events, lamps)
        asked = jev.desktop_questions(desktop) if desktop and not images and not machine else None
        if asked and spoken(origin):
            card = (card + "\n\n" if card else "") + jev.desktop_note(desktop)
        queue_check = jev.after_question(running_said[conv]) if busy(conv) and spoken(origin) and parallel_ok() else None
        if queue_check:
            asked = {**(asked or {}), **queue_check}
        answer = await asyncio.to_thread(jev_answer, text, origin, card, labels, machine, asked)
        event, needs_context = pick_event(conv, text, events, labels, answer)
        decision = pick_tier(conv, text, images, origin, answer,
                             recent_light=bool(event and event.get("device")), needs_context=needs_context,
                             machine=machine)
        if needs_context:
            notes = [CONTEXT_NOTE] + list(notes or [])
        if decision is not None and decision.handoff:
            notes = [handoff_note(decision.handoff)] + list(notes or [])
    act = route.desktop_action(answer, text, desktop) if asked else None
    if act is not None and act.get("action") == "again" and event and event.get("device"):
        # "A bit more" right after a light change means the lights, not the last desktop act.
        act = None
    if act is not None and (await looking).restricted:
        # A locked banditbox: a voice in the room drives nothing on it.
        act = None
    if act is not None:
        turn = run_desktop(conv, text, act)
        if picking is not None:
            background(file_when_picked(picking, message_id))
        usage_log(turn, conv, origin, started, decision)
        return turn
    if decision is not None and decision.direct:
        turn = await run_direct(conv, text, decision, event)
        if turn is not None:
            if decision.direct.get("kind") == "hue":
                turn.modules.append("lights")
            if picking is not None:
                background(file_when_picked(picking, message_id))
            refresh_panels(turn)
            usage_log(turn, conv, origin, started, decision)
            return turn
    guard = await looking
    if guard.line:
        notes = [guard.line] + list(notes or [])
    if guard.restricted and chosen == "opencode":
        chosen = "api"
    picked = await picking if picking is not None else None
    if picked is not None and picked.attach:
        threads.attach_message(message_id, picked.attach)
    handover = take_handover(conv)
    filed = picked is not None and picked.source not in ("off", "error")
    extra = {"pick": picked.row()} if picked is not None else None
    asking = None
    beside = chosen == "claude" and busy(conv) and spoken(origin) and parallel_ok() and runs_beside(answer, said)
    if chosen == "claude" and busy(conv) and spoken(origin):
        logger.info("%s: %r %s", conv, said[:80], "runs beside the busy turn" if beside else "queues behind the busy turn")
    running_said[conv].append(said)
    try:
        async with (contextlib.nullcontext() if beside else lock(conv)):
            lockguard.arm(conv, guard)
            if not beside:
                asking = questions.turns[conv] = uuid.uuid4().hex
            session, seeded = None, False
            if chosen == "claude" and laned(conv) and not beside:
                if filed:
                    cap = threads._num("threads.session_tokens", 80000)
                    session, seeded = lanes_for(conv).choose(picked.lane or "unfiled", cap)
                    extra.update(lane=picked.lane, seeded=seeded)
                else:
                    session = lanes_for(conv).current()
            if filed:
                retrieved = threads.assemble(picked, said=said, lamps_line=lamps_line(lamps),
                                             held=None if seeded or session is None else picked.lane)
            else:
                retrieved = recap_for(said)
            notes = ([handover] if handover else []) + ([retrieved] if retrieved else []) + list(notes or []) or None
            message = prefix(origin, notes) + "\n" + text
            if notes:
                text = "\n".join(notes) + "\n" + text
            if beside:
                turn = await run_side(conv, message, images, on_step, decision, **({"on_text": on_text} if on_text else {}))
            elif chosen == "claude":
                turn = await run_claude(conv, message, images, on_step, decision,
                                        **({"session": session} if session else {}), **({"on_text": on_text} if on_text else {}))
            elif chosen == "opencode":
                turn = await run_opencode(conv, message, on_step)
            else:
                turn = await run_api(conv, text, origin)
            if turn.error and not turn.text and chosen != "api" and turn.error != "stopped" and fallback_allowed():
                logger.warning("%s failed for %s (%s), falling back to the api brain", chosen, conv, turn.error)
                failed = turn
                turn = await run_api(conv, text, origin)
                turn.first_text, turn.first_text_at = failed.first_text, failed.first_text_at
                turn.note = f"{chosen} failed ({failed.error[:160]}), the api brain answered instead"
    except BaseException:
        if handover:
            give_back_handover(conv, handover)
        raise
    finally:
        try:
            running_said[conv].remove(said)
        except ValueError:
            pass
        if asking is not None and questions.turns.get(conv) == asking:
            questions.turns.pop(conv, None)
        if laned(conv):
            try:
                lanes_for(conv).fill_soon()
            except RuntimeError:
                pass
    if handover and turn.error and not turn.text:
        give_back_handover(conv, handover)
    turn.did = turn.did or did_for(turn)
    try:
        if not beside:
            # A side turn has no turn id of its own; claiming would take the busy turn's questions.
            turn.objects = list(turn.objects) + questions.claim(conv, started, asking)
    except Exception:
        logger.exception("could not claim the questions of the turn")
    if filed:
        turn.threads = list(picked.attach)
        threads.touched(picked.attach)
        if picked.late is not None:
            background(threads.settle_late(picked, message_id))
    if turn.error != "stopped":
        record_turn(conv, said, turn)
        context.answered(said, turn.text, turn.did, turn.backend)
    refresh_panels(turn)
    usage_log(turn, conv, origin, started, decision, extra)
    return turn


def describe():
    chosen = backend()
    lines = [f"brain: {chosen} ({model_for(chosen)}{', effort ' + effort() if chosen == 'claude' else ''})"]
    if chosen == "claude":
        lines.append(f"router: {'on' if route.enabled() else 'off (shadow log only)'}, ceiling {route._setting_tier('route.max_tier', 'chat')}")
    for name in ("claude", "opencode"):
        lines.append(f"{name}: {'installed' if executable(name) else 'not installed'}")
    lines.append(f"fallback: {'api' if fallback_allowed() else 'off'}")
    return "\n".join(lines)
