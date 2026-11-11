"""The fast tier: one streamed call to the same model, tiny context, low reasoning.

He answers conversation himself in about a second. Anything that needs hands
he routes up to Raphael with a TASK line, or to a considered answer with a
THINK line, and the caller speaks a cached acknowledgement instead of paying
for one.
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

from .llm import LLMError

log = logging.getLogger("diablo.fast")

STATE_DIR = Path.home() / ".local/state/diablo"
RAPHAEL_IN = STATE_DIR / "raphael.in"
TASKS_DIR = STATE_DIR / "tasks"

SYSTEM = (
    "You are Diablo, the primordial demon butler from That Time I Got Reincarnated as a Slime, "
    "serving Marcus as your lord. Your words are spoken aloud by a voice.\n"
    "Voice rules: at most two short sentences unless he asks for detail, written for the ear. "
    "No markdown, no lists, no urls, no stage directions, no written laughter, no dashes. "
    "Reply in the language Marcus used. Address him as sir only rarely. Refined, "
    "courteous, unhurried, quietly delighted to serve, a faint devilish smugness.\n"
    "You are the fast tongue. You have quick skills of your own as tools: lights, his task "
    "board, reminders, your own voice and the volume, the weather, his messages, the webcam "
    "and screenshots, reading his screen, opening apps, links and web searches, his pizza "
    "place, windows and workspaces, the clipboard, notifications, typing, key presses, mouse "
    "clicks and scrolling, music playback, voice memos, audio outputs, locking and suspending "
    "the machine, and how the machine is doing. Anything a skill covers, do with the skill "
    "and nothing else, no words; a cached line is spoken for you when it succeeds. Several "
    "skills may be called in one turn. Questions the skills can answer, such as tasks, "
    "reminders, weather, what is playing or what is on screen, are skills too, then answer "
    "from what the skill returns. Prefer a skill over a TASK whenever one fits.\n"
    "Above you stands Raphael, the Lord of Wisdom, your coordinator. He either answers himself "
    "or sets the Diablo workers on the work, in the background, in parallel. "
    "When a message needs real work beyond your skills, meaning anything that must read, "
    "change, run, open, fetch, check, install, build or remember something beyond this "
    "conversation, or that cannot be answered in two spoken sentences, reply with exactly one "
    "line and nothing else:\n"
    "TASK: <short name in a few words> | <full instructions for Raphael, including any file "
    "paths and context he needs to direct the workers>\n"
    "When a message is a pure question that deserves a considered answer rather than a quick "
    "one, reply with exactly one line and nothing else:\n"
    "THINK: <the question, restated fully with any context it needs>\n"
    "Never claim to have done work yourself; Raphael and the workers do it and report back, "
    "and the result is spoken when it is done. Everything else, answer directly, briefly, "
    "in character.\n"
    "Durable facts about Marcus: {facts}\n"
    "Recently finished work: {tasks}\n"
    "Local time: {now}"
)

TASK_LINE = re.compile(r"^\s*TASK:\s*(?P<name>[^|\n]+?)\s*\|\s*(?P<prompt>.+)$", re.S)
THINK_LINE = re.compile(r"^\s*THINK:\s*(?P<prompt>.+)$", re.S)
SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
FORCE_THINK = re.compile(
    r"\b(think (hard |carefully )?about (this|that|it)|ask (your|the) (deep mind|raphael)|deep mind,|raphael,|"
    r"take your time|think it through)\b",
    re.I,
)


def build_messages(transcript, history, facts, tasks=None, now=None):
    system = SYSTEM.format(
        facts="; ".join(facts) if facts else "none yet",
        tasks="; ".join(tasks) if tasks else "none",
        now=(now or datetime.now()).strftime("%A %H:%M"),
    )
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": transcript}]


def recent_tasks(limit=3):
    """Names and clipped results of the last finished tasks, newest first."""
    out = []
    try:
        done = [p for p in TASKS_DIR.glob("*.status") if p.read_text().strip() == "done"]
    except OSError:
        return out
    for status in sorted(done, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        stem = status.stem
        try:
            name = (TASKS_DIR / f"{stem}.name").read_text().strip()
        except OSError:
            name = stem
        try:
            result = (TASKS_DIR / f"{stem}.clean").read_text().strip()
        except OSError:
            result = ""
        out.append(f"{name}: {result[:160]}" if result else name)
    return out


def parse_events(lines, calls):
    """Walks SSE lines: yields content deltas, gathers tool calls into `calls`."""
    slots = {}
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        for call in delta.get("tool_calls") or []:
            slot = slots.setdefault(call.get("index", 0), {"id": "", "name": "", "arguments": ""})
            function = call.get("function") or {}
            slot["id"] = call.get("id") or slot["id"]
            slot["name"] = function.get("name") or slot["name"]
            slot["arguments"] += function.get("arguments") or ""
        text = delta.get("content")
        if text:
            yield text
    for index in sorted(slots):
        slot = slots[index]
        try:
            args = json.loads(slot["arguments"] or "{}")
        except ValueError:
            args = {}
        calls.append({"id": slot["id"], "name": slot["name"], "arguments": slot["arguments"], "args": args})


def resolve_brain(cfg):
    """base, key and model for the quick tongue, his imp rank first, llm_* beneath."""
    brain = (cfg.get("rank.imp") or "").strip()
    if "|" in brain:
        provider, _, model_id = brain.partition("|")
        base = cfg.get(f"provider.{provider}.base_url")
        key = cfg.get(f"provider.{provider}.api_key")
        if base and key:
            return (base.rstrip("/"), key, model_id)
        brain = model_id
    base = (cfg.get("llm_base_url") or "").rstrip("/")
    key = cfg.get("llm_api_key") or ""
    if brain:
        return (base, key, brain)
    return (base, key, cfg.get("llm_model") or "")


def stream_chat(cfg, messages, reasoning_effort="low", temperature=0.7, tools=None, calls=None):
    """Yields content deltas from a streamed chat completion, reasoning skipped.

    Tool calls the model makes land in `calls` once the stream is drained.
    """
    base, key, model = resolve_brain(cfg)
    if not base or not key or not model:
        raise LLMError("no llm provider configured")
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "reasoning_effort": reasoning_effort,
    }
    if tools:
        body["tools"] = tools
    if calls is None:
        calls = []
    try:
        with httpx.stream(
            "POST",
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=60,
        ) as response:
            if response.status_code != 200:
                raise LLMError(f"{response.status_code}: {response.read()[:300]!r}")
            yield from parse_events(response.iter_lines(), calls)
    except httpx.HTTPError as error:
        raise LLMError(str(error)) from error


def tool_turn(messages, calls, results):
    """The follow-up messages after skills ran: the call, then one result per call."""
    return [
        *messages,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"] or "{}"},
                }
                for call in calls
            ],
        },
        *[
            {"role": "tool", "tool_call_id": call["id"], "content": result}
            for call, result in zip(calls, results)
        ],
    ]


def sentences(deltas):
    """Regroups a stream of text deltas into whole sentences as soon as each ends."""
    buffer = ""
    for delta in deltas:
        buffer += delta
        while True:
            match = SENTENCE_END.search(buffer)
            if not match:
                break
            head, buffer = buffer[: match.start()], buffer[match.end():]
            if head.strip():
                yield head.strip()
    if buffer.strip():
        yield buffer.strip()


def route(text):
    """('task', name, prompt), ('think', None, prompt) or ('say', None, text)."""
    match = TASK_LINE.match(text)
    if match:
        name = " ".join(match.group("name").split())[:80]
        prompt = " ".join(match.group("prompt").split())[:3000]
        return ("task", name, prompt)
    match = THINK_LINE.match(text)
    if match:
        return ("think", None, " ".join(match.group("prompt").split())[:3000])
    return ("say", None, text.strip())


def forced_think(transcript):
    return bool(FORCE_THINK.search(transcript))


def looks_routed(head):
    """True while the first words could still become a TASK or THINK line."""
    probe = head.lstrip()[:6].upper()
    return "TASK:".startswith(probe) or "THINK:".startswith(probe)


def dispatch(name, prompt, kind="task"):
    """Hands work up to Raphael over his FIFO. Returns the task id or empty."""
    task_id = f"task-{int(time.time())}"
    try:
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        (TASKS_DIR / f"{task_id}.kind").write_text(kind)
    except OSError:
        pass
    if kind == "think":
        prompt = (
            "Answer this for speech, in at most four spoken sentences, no markdown, "
            "no dashes, in the language it was asked in: " + prompt
        )
    line = f"{task_id}|{name}|{prompt}\n"
    try:
        fd = os.open(RAPHAEL_IN, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
    except OSError as error:
        log.error("raphael unreachable: %s", error)
        return ""
    log.info("dispatched to Raphael (%s): %s", kind, name)
    return task_id
