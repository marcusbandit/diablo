import asyncio
import datetime
import json
import logging
import time

from . import brains, db, settings, t3link, tasks

logger = logging.getLogger(__name__)

POLL_SECONDS = 45
REFRESH_SECONDS = 6 * 3600
GRACE_SECONDS = 120
EXPIRE_SECONDS = 36 * 3600
ACTIVE = {"running", "queued", "pending", "starting", "requested", "preparing"}


def parse_time(value):
    if not value:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def judge(watch, data):
    thread = (data or {}).get("thread") or {}
    runs = (data or {}).get("recentRuns") or []
    pending = int(thread.get("pendingRequestCount") or 0)
    if pending and watch["state"] != "asking":
        return "asking", f"it waits on {pending} pending request{'s' if pending != 1 else ''} (an approval or a question)"
    if not pending and watch["state"] == "asking" and thread.get("activeRunId"):
        return "open", None
    if thread.get("activeRunId") or not runs:
        return None, None
    latest = runs[0]
    status = str(latest.get("status") or thread.get("status") or "")
    if status in ACTIVE:
        return None, None
    finished = parse_time(latest.get("completedAt"))
    if finished and finished >= watch["created"] - GRACE_SECONDS:
        return "done", f"its run ended with status {status or 'unknown'}"
    return None, None


def wakes(kind, state):
    """Whether a task change is worth a model turn and a Telegram push. Otherwise its card says it all."""
    if state in ("waiting", "failed"):
        return True
    if state == "done":
        return kind != "self_improvement"
    return False


ASKS = {
    "waiting": "Read what it asks with t3_thread_read, tell Marcus in one short line, and wait for his answer before you respond to it.",
    "failed": "Read it with t3_thread_read and tell Marcus in one short line what failed and what you will do about it.",
    "done": "Tell Marcus in one short line that it is done, with its pull request link if it made one.",
    "stopped": "Tell Marcus in one short line that it stopped before it finished, and why if the thread says.",
}
TAIL = 8
TAIL_PAGES = 3


def event_text(watch, what, state="done"):
    title = watch["title"] or "untitled"
    return (
        f"[t3 event] The thread \"{title}\" ({watch['thread_id']}) on {watch['env']}: {what}. "
        f"Its task card already shows this to Marcus. {ASKS.get(state, ASKS['done'])}"
    )


def read_thread(watch):
    """The thread with its latest run, and the newest step it took while it works."""
    data = t3link.call_tool(watch["env"], "t3_thread_read",
                            {"threadId": watch["thread_id"], "limit": 1, "maxCharsPerItem": 1, "runLimit": 1}, 30)
    if not isinstance(data, dict):
        return {}, None
    thread = data.get("thread") or {}
    count = int(thread.get("itemCount") or 0) if isinstance(thread, dict) else 0
    if not count or not thread.get("activeRunId"):
        return data, None
    items, after = [], max(0, count - TAIL * 2)
    try:
        for _ in range(TAIL_PAGES):
            page = t3link.call_tool(watch["env"], "t3_thread_read", {
                "threadId": watch["thread_id"], "view": "activity", "afterPosition": after,
                "limit": TAIL * 2, "maxCharsPerItem": 240, "runLimit": 1,
            }, 30)
            if not isinstance(page, dict):
                break
            items = (items + [i for i in page.get("items") or [] if isinstance(i, dict)])[-TAIL:]
            following = page.get("nextPosition")
            if not page.get("hasMore") or not isinstance(following, int) or following <= after:
                break
            after = following
    except Exception as error:
        logger.info("watch %s steps not readable: %s", watch["thread_id"], error)
    return data, tasks.latest_step(items)


async def check_once(deliver):
    tasks.prune()
    for watch in db.open_watches():
        if time.time() - watch["created"] > EXPIRE_SECONDS:
            db.set_watch(watch["thread_id"], "expired")
            known = db.get_task(watch["thread_id"])
            if known and known.get("state") in tasks.ACTIVE:
                tasks.save(watch["thread_id"], {"state": "done", "step": "No longer watched"})
            continue
        try:
            data, step = await asyncio.to_thread(read_thread, watch)
        except Exception as error:
            logger.info("watch %s on %s not readable yet: %s", watch["thread_id"], watch["env"], error)
            continue
        try:
            task = await asyncio.to_thread(tasks.observe, watch, data, step)
        except Exception:
            logger.exception("task card for %s failed", watch["thread_id"])
            task = None
        state, what = judge(watch, data)
        if state is None:
            continue
        db.set_watch(watch["thread_id"], state)
        if what is None:
            continue
        card = "waiting" if state == "asking" else (task or {}).get("state") or "done"
        kind = (task or {}).get("kind") or "partner"
        if not wakes(kind, card):
            logger.info("t3 %s for %s (%s): card only, no turn", card, watch["thread_id"], kind)
            continue
        if card == "done" and (task or {}).get("step") == tasks.STOPPED_STEP:
            card = "stopped"
        logger.info("t3 event for %s: %s", watch["thread_id"], what)
        try:
            turn = await brains.ask(watch["conv"] or brains.MAIN, event_text(watch, what, card), origin="t3 watcher", machine=True)
        except Exception:
            logger.exception("event turn failed")
            continue
        text = turn.text.strip() or f"{watch['title'] or 'A thread'} on {watch['env']}: {what}."
        await deliver(text, watch)


WAKE_KEY = "wake.pending"
WAKE_GIVE_UP_S = 300


async def check_wakes(deliver):
    """Carry on with orders that waited for banditbox to wake."""
    pending = json.loads(settings.get(WAKE_KEY) or "[]")
    if not pending:
        return
    up = await asyncio.to_thread(t3link.is_up, "banditbox", True)
    late = [job for job in pending if time.time() - job["created"] > WAKE_GIVE_UP_S]
    if not up and not late:
        return
    settings.set_setting(WAKE_KEY, "[]" if up else json.dumps([j for j in pending if j not in late]))
    for job in (pending if up else late):
        if up:
            text = f"[wake event] banditbox is awake and its T3 is up, the t3-banditbox tools are loaded. Carry on with this order: {job['then']}"
        else:
            text = f"[wake event] banditbox did not wake within {WAKE_GIVE_UP_S // 60} minutes. Tell Marcus, and offer to run this order on nemurenai instead: {job['then']}"
        try:
            turn = await brains.ask(job.get("conv") or brains.MAIN, text, origin="wake watcher", machine=True)
        except Exception:
            logger.exception("wake turn failed")
            continue
        if turn.text.strip():
            await deliver(turn.text.strip(), {"conv": job.get("conv") or brains.MAIN, "thread_id": "wake"})


async def run(deliver):
    while True:
        try:
            await check_wakes(deliver)
        except Exception:
            logger.exception("wake check failed")
        try:
            await check_once(deliver)
        except Exception:
            logger.exception("watch loop failed")
        await asyncio.sleep(POLL_SECONDS)


async def keep_links():
    while True:
        try:
            report = await asyncio.to_thread(t3link.refresh)
            logger.info("t3 links: %s", report)
        except Exception:
            logger.exception("t3 link refresh failed")
        await asyncio.sleep(REFRESH_SECONDS)
