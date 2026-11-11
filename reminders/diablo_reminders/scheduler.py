"""The scheduler loop: claim what came due, deliver it, sleep until the next thing.

Firing an occurrence is two steps, each its own committed transaction:

1. claim: insert the delivery row and move the reminder to its next occurrence.
   From this point the occurrence can never be claimed again.
2. deliver: mark the attempt as sending, call Telegram, then mark it sent.

A failed send (network down, Telegram refusing) goes back to pending with an
exponential backoff, 30 s doubling to at most 30 min, honouring Telegram's
retry_after. After 24 h of failures the delivery is marked failed and stays
visible in `remind list --all`. A crash between the two marks leaves the
delivery in sending; recovery on the next start retries it, see the README.
"""

import fcntl
import logging
import os
import signal
import threading
import time
from . import store as store_mod
from . import voice

log = logging.getLogger("diablo.reminders")

BACKOFF_START = 30
BACKOFF_MAX = 1800
GIVE_UP_AFTER = 24 * 3600
MAX_SLEEP = 15
TASK_STALE_AFTER = 2 * 3600


def backoff(attempts, retry_after=None):
    delay = min(BACKOFF_START * 2 ** max(attempts - 1, 0), BACKOFF_MAX)
    if retry_after:
        delay = max(delay, float(retry_after))
    return delay


def deliver_ready(store, sender, now):
    """Try every delivery whose attempt is due. Returns how many were sent."""
    sent = 0
    for row in store.ready(now):
        if not store.begin_attempt(row["id"], now):
            continue
        attempts = row["attempts"] + 1
        try:
            kind = row.get("kind") or "remind"
            meta = {"reminder": row["reminder_id"], "delivery": row["id"], "kind": kind,
                    "client_id": f"task-{row['reminder_id']}-{int(row['due_at'])}-{int(row['claimed_at'])}"}
            if kind == "task" and now.timestamp() - row["due_at"] > TASK_STALE_AFTER:
                message = voice.stale_task(row, now)
                meta["kind"] = "remind"
            else:
                message = voice.task(row, now) if kind == "task" else voice.render(row, now)
            late = voice.is_late(row["due_at"], now)
            message_id = sender.send(message, meta)
        except Exception as error:
            if now.timestamp() - row["claimed_at"] >= GIVE_UP_AFTER:
                store.mark_failed(row["id"], error)
                log.error("reminder %s delivery %s failed for good: %s", row["reminder_id"], row["id"], error)
            else:
                delay = backoff(attempts, getattr(error, "retry_after", None))
                store.mark_retry(row["id"], error, now.timestamp() + delay)
                log.warning("reminder %s send failed (attempt %s), retry in %ss: %s", row["reminder_id"], attempts, int(delay), error)
            continue
        store.mark_sent(row["id"], now, message, late, message_id)
        sent += 1
        log.info("reminder %s sent%s", row["reminder_id"], " late" if late else "")
    return sent


def tick(store, sender, now=None):
    now = now or store_mod.now_utc()
    claimed = store.claim_due(now)
    if claimed:
        log.info("claimed %s due occurrence(s)", len(claimed))
    return deliver_ready(store, sender, now)


class Lock:
    """One scheduler per database. A second `remind run` exits instead of racing."""

    def __init__(self, db_path):
        self.path = f"{db_path}.lock"
        self.handle = None

    def acquire(self):
        self.handle = open(self.path, "a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return True

    def release(self):
        if self.handle:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def run(db_path=None, sender=None):
    from .telegram import make_sender

    sender = sender or make_sender()
    store = store_mod.Store(db_path)
    lock = Lock(store.path)
    if not lock.acquire():
        log.error("another scheduler already runs on %s", store.path)
        store.close()
        return 1
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    try:
        reset = store.recover()
        if reset:
            log.warning("%s delivery attempt(s) were cut off by a crash; retrying them", reset)
        log.info("scheduler up on %s", store.path)
        while not stop.is_set():
            try:
                tick(store, sender)
            except Exception:
                log.exception("tick failed; trying again shortly")
            wake = store.next_wakeup()
            delay = MAX_SLEEP if wake is None else min(MAX_SLEEP, max(1.0, wake - time.time()))
            stop.wait(delay)
    finally:
        lock.release()
        store.close()
    log.info("scheduler stopped")
    return 0
