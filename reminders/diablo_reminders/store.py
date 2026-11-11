"""The durable store: one sqlite file in WAL mode, safe across crashes.

Two tables. reminders holds what Marcus asked for and when it is next due.
deliveries holds one row per occurrence that came due, and is the record that
makes firing exactly once: an occurrence is claimed by inserting its delivery
row and moving the reminder on to its next occurrence in the same transaction.
UNIQUE(reminder_id, due_at) stops any occurrence from being claimed twice, even
by two schedulers at once.

Delivery states: pending (waiting for its first or next attempt), sending (an
attempt is in flight), sent, failed (gave up), cancelled (the reminder was
cancelled before the message went out).
"""

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import schedule

log = logging.getLogger("diablo.reminders")

DEFAULT_PATH = Path.home() / ".local/state/diablo/reminders.db"
KINDS = ("remind", "task")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    anchor_local TEXT NOT NULL,
    tz TEXT NOT NULL,
    recurrence TEXT NOT NULL,
    next_local TEXT,
    next_due REAL,
    status TEXT NOT NULL,
    source TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status, next_due);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id INTEGER NOT NULL REFERENCES reminders(id),
    due_local TEXT NOT NULL,
    due_at REAL NOT NULL,
    missed INTEGER NOT NULL DEFAULT 0,
    claimed_at REAL NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    last_error TEXT,
    sent_at REAL,
    late INTEGER,
    message TEXT,
    telegram_message_id INTEGER,
    UNIQUE(reminder_id, due_at)
);
CREATE INDEX IF NOT EXISTS deliveries_ready ON deliveries(status, next_attempt_at);
"""


def default_path():
    return Path(os.environ.get("REMIND_DB") or DEFAULT_PATH)


class Store:
    def __init__(self, path=None):
        self.path = Path(path or default_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(reminders)")}
        if "kind" not in columns:
            try:
                self.conn.execute("ALTER TABLE reminders ADD COLUMN kind TEXT NOT NULL DEFAULT 'remind'")
            except sqlite3.OperationalError as error:
                if "duplicate column" not in str(error):
                    raise

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE takes the write lock up front, so a read then write is atomic."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # Reminders

    def add(self, text, wall, recurrence="none", source=None, now=None, kind="remind"):
        text = str(text or "").strip()
        if not text:
            raise ValueError("a reminder needs some text")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        recurrence = schedule.normalize_recurrence(recurrence)
        now = now or datetime.now(timezone.utc)
        wall = schedule.first_occurrence(wall, recurrence)
        if schedule.to_utc(wall) <= now:
            if recurrence == "none":
                raise ValueError(f"{schedule.describe(wall, with_year=True)} has already passed")
            wall = schedule.next_after(wall, recurrence, now)
        stamp = now.timestamp()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO reminders (text, anchor_local, tz, recurrence, next_local, next_due, status, source, created_at, updated_at, kind)"
                " VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (text, schedule.format_wall(wall), schedule.TZ_NAME, recurrence, schedule.format_wall(wall),
                 schedule.to_utc(wall).timestamp(), source, stamp, stamp, kind),
            )
        return self.get(cur.lastrowid)

    def get(self, reminder_id):
        row = self.conn.execute("SELECT * FROM reminders WHERE id = ?", (int(reminder_id),)).fetchone()
        return dict(row) if row else None

    def list(self, include_past=False):
        if include_past:
            rows = self.conn.execute("SELECT * FROM reminders ORDER BY COALESCE(next_due, updated_at), id")
        else:
            rows = self.conn.execute("SELECT * FROM reminders WHERE status = 'active' ORDER BY next_due, id")
        return [dict(row) for row in rows]

    def cancel(self, reminder_id, now=None):
        """Cancel a reminder and any of its deliveries not yet sent. Returns the row or None."""
        stamp = (now or datetime.now(timezone.utc)).timestamp()
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (int(reminder_id),)).fetchone()
            if row is None:
                return None
            if row["status"] == "active":
                conn.execute(
                    "UPDATE reminders SET status = 'cancelled', next_local = NULL, next_due = NULL, updated_at = ? WHERE id = ?",
                    (stamp, row["id"]),
                )
            conn.execute(
                "UPDATE deliveries SET status = 'cancelled' WHERE reminder_id = ? AND status IN ('pending', 'sending')",
                (row["id"],),
            )
        return self.get(reminder_id)

    def last_delivery(self, reminder_id):
        row = self.conn.execute(
            "SELECT * FROM deliveries WHERE reminder_id = ? ORDER BY due_at DESC LIMIT 1", (int(reminder_id),)
        ).fetchone()
        return dict(row) if row else None

    # Scheduler side

    def claim_due(self, now):
        """Claim every occurrence that has come due and move each reminder on.

        A reminder whose time passed several times while nothing was running
        (a recurring one while the Pi was down) is claimed once, for its most
        recent missed occurrence, with the earlier ones counted in missed.
        Returns the new delivery ids.
        """
        stamp = now.timestamp()
        claimed = []
        ids = [row[0] for row in self.conn.execute(
            "SELECT id FROM reminders WHERE status = 'active' AND next_due <= ? ORDER BY next_due, id", (stamp,)
        )]
        for reminder_id in ids:
            try:
                claimed += self._claim_one(reminder_id, now)
            except Exception as error:
                # One broken row must not hold up every other reminder.
                log.exception("reminder %s could not be claimed; marking it broken", reminder_id)
                with self.transaction() as conn:
                    conn.execute(
                        "UPDATE reminders SET status = 'broken', next_due = NULL, updated_at = ? WHERE id = ?",
                        (stamp, reminder_id),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO deliveries (reminder_id, due_local, due_at, claimed_at, status, next_attempt_at, last_error)"
                        " VALUES (?, '', ?, ?, 'failed', ?, ?)",
                        (reminder_id, stamp, stamp, stamp, f"claim failed: {error}"[:500]),
                    )
        return claimed

    def _claim_one(self, reminder_id, now):
        stamp = now.timestamp()
        claimed = []
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ? AND status = 'active' AND next_due <= ?", (reminder_id, stamp)
            ).fetchone()
            if row is not None:
                anchor = schedule.parse_wall(row["anchor_local"])
                due = schedule.parse_wall(row["next_local"])
                due_instant = datetime.fromtimestamp(row["next_due"], timezone.utc)
                missed, latest = schedule.occurrences_between(anchor, row["recurrence"], due_instant, now)
                if missed:
                    due = latest
                    due_instant = schedule.to_utc(latest)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO deliveries (reminder_id, due_local, due_at, missed, claimed_at, status, next_attempt_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                    (row["id"], schedule.format_wall(due), due_instant.timestamp(), missed, stamp, stamp),
                )
                if cur.rowcount:
                    claimed.append(cur.lastrowid)
                following = schedule.next_after(anchor, row["recurrence"], now)
                if following is None:
                    conn.execute(
                        "UPDATE reminders SET status = 'done', next_local = NULL, next_due = NULL, updated_at = ? WHERE id = ?",
                        (stamp, row["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE reminders SET next_local = ?, next_due = ?, updated_at = ? WHERE id = ?",
                        (schedule.format_wall(following), schedule.epoch(following), stamp, row["id"]),
                    )
        return claimed

    def recover(self):
        """After a crash, an attempt left in sending has an unknown outcome. Retry it.

        Returns how many were reset. See the README for why this errs towards a
        repeat over a silent loss.
        """
        with self.transaction() as conn:
            cur = conn.execute("UPDATE deliveries SET status = 'pending' WHERE status = 'sending'")
        return cur.rowcount

    def ready(self, now):
        rows = self.conn.execute(
            "SELECT d.*, r.text, r.recurrence, r.anchor_local, r.next_local, r.status AS reminder_status, r.kind, r.source"
            " FROM deliveries d JOIN reminders r ON r.id = d.reminder_id"
            " WHERE d.status = 'pending' AND d.next_attempt_at <= ? ORDER BY d.due_at, d.id",
            (now.timestamp(),),
        )
        return [dict(row) for row in rows]

    def begin_attempt(self, delivery_id, now):
        """Mark one attempt as in flight. False if the delivery is no longer pending."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE deliveries SET status = 'sending', attempts = attempts + 1 WHERE id = ? AND status = 'pending'",
                (delivery_id,),
            )
        return cur.rowcount == 1

    def mark_sent(self, delivery_id, now, message, late, message_id=None):
        with self.transaction() as conn:
            conn.execute(
                "UPDATE deliveries SET status = 'sent', sent_at = ?, message = ?, late = ?, telegram_message_id = ?, last_error = NULL"
                " WHERE id = ?",
                (now.timestamp(), message, int(bool(late)), message_id, delivery_id),
            )

    def mark_retry(self, delivery_id, error, next_attempt_at):
        with self.transaction() as conn:
            conn.execute(
                "UPDATE deliveries SET status = 'pending', last_error = ?, next_attempt_at = ? WHERE id = ? AND status = 'sending'",
                (str(error)[:500], next_attempt_at, delivery_id),
            )

    def mark_failed(self, delivery_id, error):
        with self.transaction() as conn:
            conn.execute(
                "UPDATE deliveries SET status = 'failed', last_error = ? WHERE id = ? AND status = 'sending'",
                (str(error)[:500], delivery_id),
            )

    def next_wakeup(self):
        """Earliest epoch at which something needs doing, or None."""
        row = self.conn.execute(
            "SELECT MIN(t) FROM ("
            " SELECT MIN(next_due) AS t FROM reminders WHERE status = 'active'"
            " UNION ALL SELECT MIN(next_attempt_at) FROM deliveries WHERE status = 'pending')"
        ).fetchone()
        return row[0]

    def deliveries(self, reminder_id=None):
        if reminder_id is None:
            rows = self.conn.execute("SELECT * FROM deliveries ORDER BY id")
        else:
            rows = self.conn.execute("SELECT * FROM deliveries WHERE reminder_id = ? ORDER BY id", (int(reminder_id),))
        return [dict(row) for row in rows]


def now_utc():
    return datetime.fromtimestamp(time.time(), timezone.utc)
