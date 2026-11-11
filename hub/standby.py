import asyncio
import contextlib
import json
import logging
import os
import socket
import sqlite3
import tempfile
import time
import uuid

import httpx

from . import db, settings
from .config import get_config

logger = logging.getLogger(__name__)

PROBE_S = 10
FAIL_AFTER = 6
BACK_AFTER = 2
SNAPSHOT_S = 60
DRAIN_WAIT_S = 600
ID_GAP = 1_000_000
RECAP_MESSAGES = 6
RECAP_LINE = 240
RECAP_CHARS = 1600
WRITABLE = {
    ("POST", "/v1/chat"),
    ("PUT", "/v1/sections"),
    ("POST", "/v1/facts"),
    ("POST", "/v1/messages"),
    ("POST", "/v1/act"),
}
ALWAYS_OPEN = {"/v1/standby/status"}

_state = {"state": "mirror"}
_probe = {"fails": 0, "oks": 0, "last_ok": None}
_writes = {"open": 0}


def primary_url():
    table = get_config().get("standby")
    if not isinstance(table, dict):
        return ""
    return str(table.get("primary") or "").rstrip("/")


def is_standby():
    return bool(primary_url())


def role():
    return "standby" if is_standby() else "primary"


def state():
    return _state["state"] if is_standby() else "primary"


def serving():
    return not is_standby() or _state["state"] == "active"


def inherited(message_id):
    return is_standby() and "first_message" in _state and message_id < _state["first_message"]


@contextlib.contextmanager
def writing():
    _writes["open"] += 1
    try:
        yield
    finally:
        _writes["open"] -= 1


def allowed(method, path):
    if not is_standby() or path in ALWAYS_OPEN:
        return True
    if _state["state"] != "active":
        return False
    return method in ("GET", "HEAD") or (method, path) in WRITABLE


def _state_file():
    return os.path.join(os.path.dirname(db.path() or ".") or ".", "standby.json")


def load():
    global _state
    try:
        with open(_state_file()) as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        loaded = {}
    _state = loaded if isinstance(loaded, dict) and loaded.get("state") in ("mirror", "active", "draining") else {"state": "mirror"}


def _save():
    target = _state_file()
    with open(target + ".tmp", "w") as handle:
        json.dump(_state, handle)
    os.replace(target + ".tmp", target)


def status():
    report = {"role": role(), "state": state()}
    if is_standby():
        report.update({
            "primary": primary_url(),
            "snapshot_at": _state.get("snapshot_at"),
            "active_since": _state.get("active_since"),
            "first_message": _state.get("first_message"),
            "primary_last_ok": _probe["last_ok"],
            "primary_fails": _probe["fails"],
            "pull_error": _state.get("pull_error"),
        })
        if "first_message" in _state:
            messages, facts = db.count_since(_state["first_message"], _state["first_fact"])
            report["pending"] = {"messages": messages, "facts": facts}
    return report


def etag():
    parts = []
    for suffix in ("", "-wal"):
        try:
            info = os.stat(db.path() + suffix)
            parts.append(f"{info.st_mtime_ns}-{info.st_size}")
        except FileNotFoundError:
            parts.append("0")
    return '"' + ".".join(parts) + '"'


def snapshot():
    target = os.path.join(tempfile.gettempdir(), f"diablo-snapshot-{uuid.uuid4().hex}.db")
    db.snapshot_to(target)
    return target


def _line(message):
    who = "he" if message["role"] == "user" else "you"
    return f"{who}: " + " ".join(str(message["text"]).split())[:RECAP_LINE]


def _join(head, lines):
    body = ""
    for line in reversed(lines):
        candidate = line + (" | " + body if body else "")
        if len(head) + len(candidate) + 1 > RECAP_CHARS:
            break
        body = candidate
    return f"{head} {body})" if body else None


def _add_handover(conv, note):
    if not note:
        return
    key = f"handover.{conv}"
    earlier = settings.get(key)
    settings.set_setting(key, f"{earlier}\n{note}" if earlier else note)


def clean_replay(messages, facts):
    rows, kept = [], []
    for m in messages:
        if not isinstance(m, dict) or isinstance(m.get("id"), bool) or not isinstance(m.get("id"), int):
            raise ValueError("each message needs an integer id")
        if m.get("role") not in ("user", "assistant") or not isinstance(m.get("text"), str):
            raise ValueError(f"message {m['id']}: role and text are required")
        if not isinstance(m.get("ts"), (int, float)) or not isinstance(m.get("source"), str):
            raise ValueError(f"message {m['id']}: ts and source are required")
        if m.get("meta") is not None and not isinstance(m["meta"], dict):
            raise ValueError(f"message {m['id']}: meta must be an object")
        rows.append({k: m.get(k) for k in ("id", "ts", "source", "role", "text", "meta")})
    for f in facts:
        if not isinstance(f, dict) or not isinstance(f.get("text"), str) or not isinstance(f.get("ts"), (int, float)):
            raise ValueError("each fact needs ts and text")
        tags = f.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError("fact tags must be a list of strings")
        kept.append({"ts": f["ts"], "text": f["text"], "tags": tags})
    return rows, kept


def leave_handover(stored, host):
    from . import sections

    by_conv = {}
    for m in stored:
        section = (m.get("meta") or {}).get("section") or sections.MAIN_ID
        by_conv.setdefault(sections.conv_for(section), []).append(m)
    head = (f"(While diablo-pi was down, the standby hub on {host} answered in this conversation"
            " from a fresh session. What was said there, oldest first:")
    for conv, rows in by_conv.items():
        _add_handover(conv, _join(head, [_line(m) for m in rows]))


def _standby_handover(main_sources):
    from . import sections

    host = socket.gethostname()
    head = (f"(You are answering from {host}, the standby hub, because diablo-pi is down."
            " Telegram and the T3 Code watchers stay on the Pi, so they are offline until it is back."
            " This session is new; the last messages in this conversation were:")
    by_conv = {}
    for section in sections.all_sections():
        if section["deleted"]:
            continue
        rows = db.section_messages(section["id"], 0, RECAP_MESSAGES, main_sources)
        by_conv.setdefault(sections.conv_for(section["id"]), {}).update((m["id"], m) for m in rows)
    for conv, found in by_conv.items():
        rows = [found[key] for key in sorted(found)][-RECAP_MESSAGES:]
        note = _join(head, [_line(m) for m in rows]) or head.rsplit(";", 1)[0] + ".)"
        settings.set_setting(f"handover.{conv}", note)


def _activate(main_sources):
    if "first_message" not in _state:
        top, fact = db.max_ids()
        first = ((top + ID_GAP) // ID_GAP + 1) * ID_GAP
        db.start_messages_at(first)
        _state.update(first_message=first, first_fact=fact + 1, active_since=time.time())
        for row in db.get_all_settings():
            if row["key"].startswith("brain.session."):
                settings.set_setting(row["key"], "")
        _standby_handover(main_sources)
    _state["state"] = "active"
    _save()
    logger.warning("diablo-pi is down, the standby is serving (new messages from id %s)", _state["first_message"])


def _headers():
    return {"X-Diablo-Token": str(get_config().get("hub_token") or "")}


async def _probe_primary(client):
    try:
        response = await client.get(primary_url() + "/v1/health", timeout=5)
        up = response.status_code == 200
    except httpx.HTTPError:
        up = False
    if up:
        _probe.update(fails=0, oks=_probe["oks"] + 1, last_ok=time.time())
    else:
        _probe.update(fails=_probe["fails"] + 1, oks=0)
    return up


def _verify(path):
    conn = sqlite3.connect(path)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("snapshot failed its integrity check")
        conn.execute("SELECT COUNT(*) FROM messages").fetchone()
    finally:
        conn.close()


async def pull(client, force=False):
    headers = _headers()
    if _state.get("etag") and not force:
        headers["If-None-Match"] = _state["etag"]
    response = await client.get(primary_url() + "/v1/standby/snapshot", headers=headers, timeout=60)
    if response.status_code == 304:
        return False
    response.raise_for_status()
    incoming = db.path() + ".incoming"
    with open(incoming, "wb") as handle:
        handle.write(response.content)
    try:
        await asyncio.to_thread(_verify, incoming)
    except (sqlite3.Error, ValueError):
        os.unlink(incoming)
        raise
    await asyncio.to_thread(db.swap_in, incoming)
    _state.update(etag=response.headers.get("etag"), snapshot_at=time.time())
    _save()
    return True


async def _drain(client):
    from . import brains, sections

    if _state["state"] != "draining":
        _state["state"] = "draining"
        _save()
        logger.warning("diablo-pi is back, the standby is handing over")
    waited = 0
    while (_writes["open"] or brains.any_busy()) and waited < DRAIN_WAIT_S:
        await asyncio.sleep(1)
        waited += 1
    await brains.close_all()
    messages, facts = db.written_since(_state["first_message"], _state["first_fact"])
    body = {
        "host": socket.gethostname(),
        "messages": messages,
        "facts": facts,
        "sections": sections.all_sections(),
    }
    response = await client.post(primary_url() + "/v1/standby/replay", headers=_headers(), json=body, timeout=60)
    response.raise_for_status()
    await pull(client, force=True)
    for key in ("first_message", "first_fact", "active_since"):
        _state.pop(key, None)
    _state["state"] = "mirror"
    _save()
    logger.warning("handed %d messages and %d facts back to diablo-pi", len(messages), len(facts))


async def step(client, main_sources, now=None):
    now = time.time() if now is None else now
    up = await _probe_primary(client)
    current = _state["state"]
    if current == "mirror":
        if up and now - _state.get("pulled_at", 0) >= SNAPSHOT_S:
            _state["pulled_at"] = now
            try:
                await pull(client)
                _state.pop("pull_error", None)
            except (httpx.HTTPError, sqlite3.Error, ValueError, OSError) as error:
                _state["pull_error"] = f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {error}"[:300]
                _save()
                raise
        elif _probe["fails"] >= FAIL_AFTER and _state.get("snapshot_at"):
            _activate(main_sources)
    elif current == "active" and _probe["oks"] >= BACK_AFTER:
        await _drain(client)
    elif current == "draining":
        if up:
            await _drain(client)
        elif _probe["fails"] >= FAIL_AFTER:
            _activate(main_sources)


async def run(main_sources):
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await step(client, main_sources)
            except Exception:
                logger.exception("standby step failed")
            await asyncio.sleep(PROBE_S)
