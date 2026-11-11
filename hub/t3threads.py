"""The T3 Code threads Diablo runs, with their live state, for the apps.

Built from the watches (hub/watch.py) of the last half day: each is read from its
environment, at most every FRESH_S seconds, and only while some app holds the event
stream. A change to the list publishes {"kind": "t3"}.
"""
import asyncio
import logging
import re
import time
from urllib.parse import quote, unquote

from . import db, events, t3link, tasks
from .watch import parse_time

logger = logging.getLogger(__name__)

WINDOW_S = 12 * 3600
FRESH_S = 15
MOST = 12
WORKING = {"running", "queued", "pending", "starting", "requested", "preparing"}
STOPPED = {"interrupted", "cancelled", "rolled_back"}
LINK = re.compile(r"^(?:t3-thread://v2/|https://app\.t3\.codes/threads/)([^/]+)/(.+)$")

_rows = {}
_read_at = 0.0
_lock = None


def open_link(thread):
    """The t3code:// link that opens the thread in the T3 Code app, or empty."""
    found = LINK.match(str(thread.get("link") or "").strip().strip("<>"))
    if not found:
        return ""
    return f"t3code://threads/{quote(unquote(found.group(1)), safe='')}/{quote(unquote(found.group(2)), safe='')}"


def state_of(thread, run):
    if int(thread.get("pendingRequestCount") or 0):
        return "waiting"
    status = str((run or {}).get("status") or thread.get("status") or "")
    if thread.get("activeRunId") or status in WORKING:
        return "working"
    if str(thread.get("status") or "") == "waiting":
        return "waiting"
    if status == "failed":
        return "failed"
    if status in STOPPED:
        return "stopped"
    return "finished"


def newest_pr(thread):
    """The thread's newest pull request with a url, or None."""
    prs = [p for p in thread.get("pullRequests") or [] if isinstance(p, dict) and p.get("url")]
    linked = thread.get("linkedPullRequest")
    return prs[-1] if prs else linked if isinstance(linked, dict) and linked.get("url") else None


def summarize(watch, data):
    thread = (data or {}).get("thread") or {}
    runs = (data or {}).get("recentRuns") or []
    run = runs[0] if runs and isinstance(runs[0], dict) else {}
    state = state_of(thread, run)
    started = (parse_time(run.get("startedAt") or run.get("requestedAt")) or parse_time(thread.get("createdAt"))
               or watch["created"])
    finished = None
    if state in ("finished", "failed", "stopped"):
        finished = parse_time(run.get("completedAt")) or parse_time(thread.get("updatedAt")) or None
    pr = newest_pr(thread)
    return {
        "id": watch["thread_id"],
        "env": watch["env"],
        "title": str(thread.get("title") or watch["title"] or "Untitled"),
        "state": state,
        "pending": int(thread.get("pendingRequestCount") or 0),
        "started": started,
        "finished": finished,
        "pr": {"number": pr.get("number"), "url": pr.get("url"), "state": pr.get("state")} if pr else None,
        "open": open_link(thread),
        "asked": str(watch["title"] or ""),
        "reachable": True,
    }


def unreadable(watch):
    old = _rows.get(watch["thread_id"])
    if old:
        return dict(old, reachable=False)
    return {
        "id": watch["thread_id"], "env": watch["env"], "title": watch["title"] or "Untitled", "state": "unknown",
        "pending": 0, "started": watch["created"], "finished": None, "pr": None, "open": "",
        "asked": str(watch["title"] or ""), "reachable": False,
    }


def read(watch):
    return t3link.call_tool(watch["env"], "t3_thread_read",
                            {"threadId": watch["thread_id"], "limit": 1, "maxCharsPerItem": 1, "runLimit": 1}, 15)


async def one(watch):
    try:
        data = await asyncio.wait_for(asyncio.to_thread(read, watch), 20)
        if not isinstance(data, dict) or not isinstance(data.get("thread"), dict):
            raise ValueError(f"not a thread: {str(data)[:120]}")
        try:
            await asyncio.to_thread(tasks.observe, watch, data)
        except Exception:
            logger.exception("task card for %s failed", watch["thread_id"])
        return summarize(watch, data)
    except Exception as error:
        logger.info("thread %s on %s not readable: %s", watch["thread_id"], watch["env"], error)
        return unreadable(watch)


def shape(rows):
    return [(r["id"], r["state"], r["title"], r["pending"], r["finished"], r["reachable"], str(r["pr"])) for r in rows]


def chosen(watches):
    """The ones still open or asking first, so a thread waiting on Marcus is never cut."""
    return sorted(watches, key=lambda w: (w["state"] not in ("open", "asking"), -w["created"]))[:MOST]


async def refresh():
    global _read_at, _rows
    watches = chosen(db.recent_watches(time.time() - WINDOW_S))
    rows = list(await asyncio.gather(*(one(w) for w in watches))) if watches else []
    before = shape(listed())
    _rows = {r["id"]: r for r in rows}
    _read_at = time.time()
    if shape(listed()) != before:
        events.publish({"kind": "t3"})
    return rows


RANK = {"waiting": 0, "working": 1, "unknown": 2}


def listed():
    return sorted(_rows.values(), key=lambda r: (RANK.get(r["state"], 3), -(r["finished"] or r["started"] or 0)))


async def fresh():
    """Reads every listed thread again unless that happened in the last FRESH_S seconds."""
    global _lock
    _lock = _lock or asyncio.Lock()
    async with _lock:
        if time.time() - _read_at >= FRESH_S:
            await refresh()


async def _fresh_quietly():
    try:
        await fresh()
    except Exception:
        logger.exception("t3 thread refresh failed")


async def current():
    """What is known now. A stale list is read again in the background; a change to it
    publishes a t3 event, so a slow machine never holds up the answer."""
    if time.time() - _read_at >= FRESH_S and not (_lock and _lock.locked()):
        asyncio.get_running_loop().create_task(_fresh_quietly())
    return listed()


async def run():
    while True:
        if events.subscribers:
            await _fresh_quietly()
        await asyncio.sleep(FRESH_S)


# ---------------------------------------------------------------- threads for voice
# "Go to the Diablo purpose thread": every unsettled top-level thread on every T3 host, kept
# warm so Jev can pick one at once. Listing them takes about half a second per host, so a
# stale list is used as it is while a fresh one is fetched behind it.
VOICE_TTL_S = 30
VOICE_MAX = 12
# A host that is off (kangaeru often is) must not hold the list: about 0.1 to 0.4 s is normal.
VOICE_HOST_S = 2.5
VOICE_LINK = re.compile(r"\((t3-thread://[^)]+)\)")
_voice = {"at": 0.0, "rows": [], "busy": False}


def _voice_rows(env, data):
    rows = []
    for t in (data.get("threads") or []) if isinstance(data, dict) else []:
        if t.get("parentThreadId") or t.get("settled") or t.get("snoozed"):
            continue
        raw = str(t.get("link") or "")
        found = VOICE_LINK.search(raw)
        # The link comes as a plain https://app.t3.codes/threads/... URL from the hub's MCP
        # call, and as a markdown link from T3's own client: both open the same thread.
        link = open_link({"link": found.group(1) if found else raw})
        if link:
            rows.append({"title": str(t.get("title") or "untitled")[:90], "status": str(t.get("status") or ""),
                         "open": link, "updated": str(t.get("updatedAt") or ""), "env": env})
    return rows


def _voice_fetch():
    import concurrent.futures

    def one(env):
        try:
            return _voice_rows(env, t3link.call_tool(env, "t3_thread_list",
                                                     {"allProjects": True, "settled": False, "limit": 60},
                                                     timeout=VOICE_HOST_S))
        except Exception as error:
            logger.debug("voice threads from %s failed: %s", env, error)
            return []

    envs = t3link.environments()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(envs))) as pool:
        rows = [row for found in pool.map(one, envs) for row in found]
    busy = {"running", "waiting", "queued", "starting", "preparing"}
    rows.sort(key=lambda r: (r["status"] not in busy, "".join(chr(255 - ord(c)) for c in r["updated"])))
    return rows[:VOICE_MAX]


def _voice_refresh():
    try:
        rows = _voice_fetch()
        if rows or not _voice["rows"]:
            _voice["rows"] = rows
        _voice["at"] = time.time()
    finally:
        _voice["busy"] = False


def voice_threads(wait=False):
    """The unsettled threads to pick from by voice: [{title, status, open, updated, env}].

    A fresh list is returned at once; a stale one is returned too, with a refetch behind it.
    With no list yet, wait=True fetches one first."""
    import threading

    stale = time.time() - _voice["at"] > VOICE_TTL_S
    if stale and not _voice["busy"]:
        _voice["busy"] = True
        if wait and not _voice["rows"]:
            _voice_refresh()
        else:
            threading.Thread(target=_voice_refresh, daemon=True, name="voice-threads").start()
    return list(_voice["rows"])


# The check lives with Jev's questions, so the ears can make it too.
from .jev import wants_threads  # noqa: E402,F401
