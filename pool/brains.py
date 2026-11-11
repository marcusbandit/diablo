"""The direct brains: OpenAI shaped chat completions with a local tool loop.

Sessions live in memory as plain message lists. Tool calls run on this
machine as real subprocesses. No opencode anywhere.
"""

import json
import logging
import subprocess
import time
from pathlib import Path

import httpx

from voice import hubclient

log = logging.getLogger("diablo.pool.brains")

RANKS = ("imp", "fiend", "baron", "viscount", "marquis", "duke", "lord")
FORBIDDEN = ("rm -rf /", "mkfs", "dd if=", "shutdown", "reboot", ":(){:|:&};:")
TEMPERATURE = 0.6
CALL_TIMEOUT = 180.0
SHELL_TIMEOUT = 120
OUTPUT_LIMIT = 8000
READ_LIMIT = 40000
WRITE_LIMIT = 200000
MAX_STEPS = 60


class ModelError(Exception):
    pass


class RoundTimeout(Exception):
    pass


class ProviderDown(Exception):
    pass


class ProviderError(Exception):
    pass


def resolve_brain(settings, brain):
    """base, key and model id for a brain string.

    A rank name resolves through the rank.<name> setting. "provider|model-id"
    resolves provider.<name>.base_url and provider.<name>.api_key. A plain
    model id rides the default provider settings.
    """
    name = str(brain or "").strip()
    if name in RANKS:
        name = str(settings.get(f"rank.{name}") or "").strip()
    if "|" in name:
        provider, _, model_id = name.partition("|")
        base = str(settings.get(f"provider.{provider}.base_url") or "").strip()
        key = str(settings.get(f"provider.{provider}.api_key") or "").strip()
        model_id = model_id.strip()
        if not base or not key or not model_id:
            raise ModelError(f"unknown provider: {provider}")
        return base.rstrip("/"), key, model_id
    base = str(settings.get("llm_base_url") or "").strip()
    key = str(settings.get("llm_api_key") or "").strip()
    model_id = name or str(settings.get("llm_model") or "").strip()
    if not base or not key or not model_id:
        raise ModelError("the provider settings are missing")
    return base.rstrip("/"), key, model_id


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command on this machine and return its output",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file, truncated when enormous",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a file whole, creating parent directories",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact older string with a new one in a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
]


def _extra_tools():
    """User tools from ~/.config/diablo/tools.py: a TOOLS list plus run(name, args).

    A broken file costs its tools, not the built-ins.
    """
    path = Path.home() / ".config" / "diablo" / "tools.py"
    if not path.exists():
        return [], None
    import importlib.util

    try:
        spec = importlib.util.spec_from_file_location("diablo_user_tools", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        extra = list(getattr(module, "TOOLS", []))
        runner = getattr(module, "run", None)
        if extra and not callable(runner):
            return [], None
        return extra, runner
    except Exception:
        log.exception("user tools failed to load: %s", path)
        return [], None


EXTRA_TOOLS, EXTRA_RUN = _extra_tools()
ALL_TOOLS = TOOLS + EXTRA_TOOLS


def _path(raw):
    return str(raw or "").strip()


def run_tool(name, args):
    try:
        if name == "run_shell":
            return _run_shell(str(args.get("command", "")))
        if name == "read_file":
            return _read_file(_path(args.get("path")))
        if name == "write_file":
            return _write_file(_path(args.get("path")), str(args.get("content", "")))
        if name == "edit_file":
            return _edit_file(_path(args.get("path")), str(args.get("old", "")), str(args.get("new", "")))
    except OSError as error:
        return f"failed: {error}"
    if EXTRA_RUN is not None:
        try:
            result = EXTRA_RUN(name, args)
        except Exception as error:
            log.exception("user tool %s failed", name)
            return f"{name} failed: {error}"
        if result is not None:
            return str(result)
    return f"unknown tool: {name}"


def _run_shell(command):
    low = command.lower()
    if not command.strip():
        return "no command given"
    if any(item in low for item in FORBIDDEN):
        return "refused: destructive command"
    try:
        done = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=SHELL_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {SHELL_TIMEOUT}s"
    out = (done.stdout + done.stderr).strip()
    return out[:OUTPUT_LIMIT] or "(no output)"


def _read_file(path):
    if not path:
        return "no path given"
    text = (Path(path).expanduser()).read_text(encoding="utf-8", errors="replace")
    return text[:READ_LIMIT] + ("\n[truncated]" if len(text) > READ_LIMIT else "") or "(empty file)"


def _write_file(path, content):
    if not path:
        return "no path given"
    if len(content) > WRITE_LIMIT:
        return "refused: content over the size limit"
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {target}"


def _edit_file(path, old, new):
    if not path:
        return "no path given"
    if not old:
        return "refused: an empty older string would replace the whole file"
    target = Path(path).expanduser()
    text = target.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)
    if count == 0:
        return "the older string is not in the file"
    if count > 1:
        return f"the older string appears {count} times, give a longer one"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return "edited"


def shape(message):
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    text = (content or "").strip()
    return {
        "info": {"role": "assistant"},
        "parts": [{"type": "text", "text": text}] if text else [],
    }


class Brains:
    """Direct provider sessions, one message list per session id."""

    def __init__(self, cfg, get_settings=None):
        self.cfg = cfg
        self.get_settings = get_settings or hubclient.get_settings
        self.sessions = {}
        self.aborted = set()
        self.counter = 0

    def settings(self):
        return {**self.cfg, **(self.get_settings(self.cfg) or {})}

    def create_session(self, title, system=None):
        self.counter += 1
        sid = f"ses_{self.counter}"
        self.sessions[sid] = []
        if system:
            self.sessions[sid].append({"role": "system", "content": system})
        return sid

    def restore(self, messages, title):
        self.counter += 1
        sid = f"ses_{self.counter}"
        self.sessions[sid] = list(messages)
        return sid

    def abort(self, sid):
        self.aborted.add(sid)

    def trim(self, sid, keep):
        session = self.sessions.get(sid)
        if not session or len(session) <= keep:
            return
        head = 1 if session[0].get("role") == "system" else 0
        del session[head:max(head, len(session) - keep)]

    def last_assistant(self, sid):
        for message in reversed(self.sessions.get(sid, [])):
            if message.get("role") == "assistant":
                return shape(message)
        return {"info": {}, "parts": []}

    def message(self, sid, text, brain, timeout, step_hook=None, max_steps=MAX_STEPS):
        base, key, model_id = brain
        session = self.sessions.get(sid)
        if session is None:
            raise ProviderError(f"no such session: {sid}")
        entry = len(session)
        deadline = time.monotonic() + timeout
        session.append({"role": "user", "content": text})
        steps = 0
        while True:
            if sid in self.aborted:
                self.aborted.discard(sid)
                raise RoundTimeout(f"round aborted: {sid}")
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise RoundTimeout(f"round budget spent: {sid}")
            try:
                reply = self._chat(base, key, model_id, session, min(CALL_TIMEOUT, budget))
            except (ProviderDown, ProviderError):
                del session[entry:]
                raise
            session.append(reply)
            calls = reply.get("tool_calls") or []
            for call in calls:
                name = call.get("function", {}).get("name", "")
                args = _args_of(call)
                if step_hook:
                    step_hook({"kind": "tool", "name": name, "args": args})
                result = run_tool(name, args)
                if step_hook:
                    step_hook({"kind": "result", "name": name, "output": result})
                session.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": result,
                })
            if not calls:
                return shape(reply)
            steps += 1
            if steps >= max_steps:
                return shape(reply)

    def _chat(self, base, key, model_id, messages, timeout):
        body = {
            "model": model_id,
            "messages": messages,
            "temperature": TEMPERATURE,
            "tools": ALL_TOOLS,
            "reasoning_effort": "low",
        }
        try:
            response = httpx.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=timeout,
            )
        except httpx.TimeoutException as error:
            raise ProviderDown(f"chat timed out: {error}") from None
        except httpx.HTTPError as error:
            raise ProviderDown(str(error)) from error
        if response.status_code in (429, 500, 502, 503, 504):
            raise ProviderDown(f"{response.status_code}: {response.text[:300]}")
        if response.status_code >= 400:
            raise ProviderError(f"{response.status_code}: {response.text[:300]}")
        try:
            return response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ProviderDown(f"unexpected chat response: {response.text[:300]}") from error


def _args_of(call):
    raw = call.get("function", {}).get("arguments") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}
