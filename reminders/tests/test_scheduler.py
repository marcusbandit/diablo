import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from diablo_reminders import scheduler, voice
from diablo_reminders import schedule as s
from diablo_reminders.store import Store
from diablo_reminders.telegram import SendError


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


ADDED = utc(2026, 11, 3, 19, 0)
SUNDAY = datetime(2026, 11, 8, 13, 0)
SUNDAY_UTC = utc(2026, 11, 8, 12, 0)
DINNER = "Time to go home and make dinner for the people with Sofus."


class FakeSender:
    def __init__(self, fail=0, error=None):
        self.sent = []
        self.fail = fail
        self.error = error or SendError("network down")
        self.lock = threading.Lock()

    def send(self, text, meta=None):
        with self.lock:
            if self.fail:
                self.fail -= 1
                raise self.error
            self.sent.append(text)
            return len(self.sent)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "reminders.db")
        self.store = Store(self.path)
        self.sender = FakeSender()

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def reopen(self):
        """What a restart does: drop the connection, open a fresh one."""
        self.store.close()
        self.store = Store(self.path)
        self.store.recover()


class OnTime(Base):
    def test_seeded_sunday_reminder(self):
        row = self.store.add(DINNER, SUNDAY, now=ADDED)
        self.assertEqual(row["next_due"], SUNDAY_UTC.timestamp())
        self.assertEqual(scheduler.tick(self.store, self.sender, SUNDAY_UTC - timedelta(seconds=1)), 0)
        self.assertEqual(scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(seconds=5)), 1)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn(DINNER, self.sender.sent[0])
        self.assertNotIn("late", self.sender.sent[0])
        self.assertEqual(self.store.get(row["id"])["status"], "done")
        [delivery] = self.store.deliveries(row["id"])
        self.assertEqual((delivery["status"], delivery["late"], delivery["attempts"]), ("sent", 0, 1))

    def test_repeated_ticks_send_once(self):
        self.store.add(DINNER, SUNDAY, now=ADDED)
        for second in range(0, 600, 15):
            scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(seconds=second))
        self.assertEqual(len(self.sender.sent), 1)

    def test_past_one_off_is_refused(self):
        with self.assertRaises(ValueError):
            self.store.add("too late", datetime(2026, 11, 1, 9, 0), now=ADDED)

    def test_recurring_moves_on_and_says_when_next(self):
        row = self.store.add("Stand up", datetime(2026, 11, 6, 8, 30), "weekdays", now=ADDED)
        scheduler.tick(self.store, self.sender, s.to_utc(datetime(2026, 11, 6, 8, 30)))
        self.assertEqual(self.store.get(row["id"])["next_local"], "2026-11-09T08:30")
        self.assertIn("I shall remind you again Monday 9 November at 08:30.", self.sender.sent[0])


class RestartRecovery(Base):
    def test_missed_one_off_fires_late_on_startup(self):
        self.store.add(DINNER, SUNDAY, now=ADDED)
        self.reopen()  # the Pi was off from Saturday to Sunday evening
        scheduler.tick(self.store, self.sender, utc(2026, 11, 8, 18, 0))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("arrives late", self.sender.sent[0])
        self.assertIn("due Sunday 8 November at 13:00", self.sender.sent[0])
        self.assertIn(DINNER, self.sender.sent[0])
        [delivery] = self.store.deliveries()
        self.assertEqual(delivery["late"], 1)

    def test_missed_daily_fires_once_and_counts_the_rest(self):
        row = self.store.add("Pills", datetime(2026, 11, 4, 9, 0), "daily", now=ADDED)
        self.reopen()
        now = utc(2026, 11, 7, 12, 0)  # missed the 4th, 5th, 6th and 7th
        scheduler.tick(self.store, self.sender, now)
        scheduler.tick(self.store, self.sender, now + timedelta(minutes=1))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("3 more times", self.sender.sent[0])
        self.assertIn("due Saturday 7 November at 09:00", self.sender.sent[0])
        self.assertEqual(self.store.get(row["id"])["next_local"], "2026-11-08T09:00")

    def test_restart_between_claim_and_send(self):
        self.store.add(DINNER, SUNDAY, now=ADDED)
        self.store.claim_due(SUNDAY_UTC)  # claimed, then the process died
        self.reopen()
        scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(seconds=30))
        scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(seconds=45))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertNotIn("late", self.sender.sent[0])

    def test_restart_mid_send_retries_once(self):
        self.store.add(DINNER, SUNDAY, now=ADDED)
        self.store.claim_due(SUNDAY_UTC)
        [row] = self.store.ready(SUNDAY_UTC)
        self.store.begin_attempt(row["id"], SUNDAY_UTC)  # in flight when the power went
        self.assertEqual(self.store.ready(SUNDAY_UTC), [])
        self.reopen()
        scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(seconds=20))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.store.deliveries()[0]["attempts"], 2)

    def test_sent_survives_restart(self):
        self.store.add(DINNER, SUNDAY, now=ADDED)
        scheduler.tick(self.store, self.sender, SUNDAY_UTC)
        for _ in range(3):
            self.reopen()
            scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(hours=1))
        self.assertEqual(len(self.sender.sent), 1)


class ExactlyOnce(Base):
    def test_two_stores_cannot_claim_the_same_occurrence(self):
        self.store.add(DINNER, SUNDAY, now=ADDED)
        other = Store(self.path)
        try:
            first = self.store.claim_due(SUNDAY_UTC)
            second = other.claim_due(SUNDAY_UTC)
        finally:
            other.close()
        self.assertEqual((len(first), len(second)), (1, 0))

    def test_unique_occurrence_constraint(self):
        row = self.store.add(DINNER, SUNDAY, now=ADDED)
        self.store.claim_due(SUNDAY_UTC)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute(
                "INSERT INTO deliveries (reminder_id, due_local, due_at, claimed_at, status, next_attempt_at)"
                " VALUES (?, ?, ?, 0, 'pending', 0)",
                (row["id"], "2026-11-08T13:00", SUNDAY_UTC.timestamp()),
            )

    def test_concurrent_schedulers_send_once(self):
        for minute in range(20):
            self.store.add(f"r{minute}", SUNDAY + timedelta(minutes=minute), now=ADDED)
        now = SUNDAY_UTC + timedelta(minutes=30)
        errors = []
        start = threading.Barrier(4)

        def worker():
            st = Store(self.path)
            try:
                start.wait()
                scheduler.tick(st, self.sender, now)
            except Exception as error:
                errors.append(error)
            finally:
                st.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(text.split("\n\n")[1] for text in self.sender.sent), sorted(f"r{m}" for m in range(20)))

    def test_lock_allows_one_scheduler(self):
        first, second = scheduler.Lock(self.path), scheduler.Lock(self.path)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()


class Retry(Base):
    def test_failure_backs_off_then_sends(self):
        self.sender.fail = 2
        self.store.add(DINNER, SUNDAY, now=ADDED)
        scheduler.tick(self.store, self.sender, SUNDAY_UTC)
        [row] = self.store.deliveries()
        self.assertEqual((row["status"], row["next_attempt_at"]), ("pending", SUNDAY_UTC.timestamp() + 30))
        scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(seconds=10))  # too early
        self.assertEqual(self.store.deliveries()[0]["attempts"], 1)
        scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(seconds=30))
        self.assertEqual(self.store.deliveries()[0]["next_attempt_at"], SUNDAY_UTC.timestamp() + 90)
        scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(seconds=90))
        [row] = self.store.deliveries()
        self.assertEqual((row["status"], row["attempts"]), ("sent", 3))
        self.assertEqual(len(self.sender.sent), 1)

    def test_retry_after_from_telegram_is_honoured(self):
        self.sender.fail = 1
        self.sender.error = SendError("429", retry_after=120)
        self.store.add(DINNER, SUNDAY, now=ADDED)
        scheduler.tick(self.store, self.sender, SUNDAY_UTC)
        self.assertEqual(self.store.deliveries()[0]["next_attempt_at"], SUNDAY_UTC.timestamp() + 120)

    def test_unexpected_error_is_retried_not_stuck(self):
        self.sender.fail = 1
        self.sender.error = RuntimeError("bug in sender")
        self.store.add(DINNER, SUNDAY, now=ADDED)
        scheduler.tick(self.store, self.sender, SUNDAY_UTC)
        self.assertEqual(self.store.deliveries()[0]["status"], "pending")

    def test_long_outage_arrives_marked_late(self):
        self.sender.fail = 4
        self.store.add(DINNER, SUNDAY, now=ADDED)
        now = SUNDAY_UTC
        for _ in range(6):
            scheduler.tick(self.store, self.sender, now)
            now += timedelta(minutes=10)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("arrives late", self.sender.sent[0])

    def test_gives_up_after_a_day(self):
        self.sender.fail = 10**6
        self.store.add(DINNER, SUNDAY, now=ADDED)
        now = SUNDAY_UTC
        while now < SUNDAY_UTC + timedelta(hours=25):
            scheduler.tick(self.store, self.sender, now)
            now += timedelta(minutes=30)
        [row] = self.store.deliveries()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(self.sender.sent, [])

    def test_backoff_caps(self):
        self.assertEqual([scheduler.backoff(n) for n in (1, 2, 3, 7, 50)], [30, 60, 120, 1800, 1800])


class Robustness(Base):
    def test_broken_row_does_not_block_the_others(self):
        bad = self.store.add("bad", SUNDAY, now=ADDED)
        self.store.add(DINNER, SUNDAY, now=ADDED)
        self.store.conn.execute("UPDATE reminders SET anchor_local = 'garbage' WHERE id = ?", (bad["id"],))
        scheduler.tick(self.store, self.sender, SUNDAY_UTC)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn(DINNER, self.sender.sent[0])
        self.assertEqual(self.store.get(bad["id"])["status"], "broken")
        self.assertEqual(self.store.last_delivery(bad["id"])["status"], "failed")

    def test_render_failure_is_retried_not_stuck(self):
        self.store.add(DINNER, SUNDAY, now=ADDED)
        self.store.claim_due(SUNDAY_UTC)
        self.store.conn.execute("UPDATE deliveries SET due_local = 'garbage'")
        scheduler.deliver_ready(self.store, self.sender, SUNDAY_UTC)
        self.assertEqual(self.store.deliveries()[0]["status"], "pending")

    def test_overlap_reminder_fires_at_the_second_instant(self):
        before = utc(2026, 10, 24, 23, 50)
        wall = s.parse_due("+2h", before)
        self.store.add("late night", wall, now=before)
        scheduler.tick(self.store, self.sender, utc(2026, 10, 25, 0, 51))  # the first 02:51
        self.assertEqual(self.sender.sent, [])
        scheduler.tick(self.store, self.sender, utc(2026, 10, 25, 1, 50))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertNotIn("late.", self.sender.sent[0].split("\n")[0])

    def test_cancel_during_flight_stays_cancelled_on_give_up(self):
        self.store.add(DINNER, SUNDAY, now=ADDED)
        [delivery_id] = self.store.claim_due(SUNDAY_UTC)
        self.store.begin_attempt(delivery_id, SUNDAY_UTC)
        self.store.cancel(1)
        self.store.mark_failed(delivery_id, "gave up")
        self.assertEqual(self.store.deliveries()[0]["status"], "cancelled")

    def test_lock_keeps_the_holder_pid(self):
        first, second = scheduler.Lock(self.path), scheduler.Lock(self.path)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        with open(first.path) as handle:
            self.assertEqual(handle.read(), str(os.getpid()))
        first.release()


class Cancel(Base):
    def test_cancel_before_due(self):
        row = self.store.add(DINNER, SUNDAY, now=ADDED)
        self.store.cancel(row["id"])
        scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(minutes=1))
        self.assertEqual(self.sender.sent, [])
        self.assertEqual(self.store.list(), [])

    def test_cancel_stops_a_pending_retry(self):
        self.sender.fail = 1
        row = self.store.add(DINNER, SUNDAY, now=ADDED)
        scheduler.tick(self.store, self.sender, SUNDAY_UTC)
        self.store.cancel(row["id"])
        scheduler.tick(self.store, self.sender, SUNDAY_UTC + timedelta(minutes=5))
        self.assertEqual(self.sender.sent, [])
        self.assertEqual(self.store.deliveries()[0]["status"], "cancelled")

    def test_cancel_unknown(self):
        self.assertIsNone(self.store.cancel(999))


class Voice(unittest.TestCase):
    def test_no_dashes_in_any_message(self):
        base = {"reminder_id": 0, "text": DINNER, "due_local": "2026-11-08T13:00", "due_at": SUNDAY_UTC.timestamp(),
                "recurrence": "weekly", "reminder_status": "active", "next_local": "2026-11-15T13:00"}
        for rid in range(len(voice.ON_TIME)):
            for late in (0, 7200):
                for missed in (0, 1, 4):
                    message = voice.render({**base, "reminder_id": rid, "missed": missed}, SUNDAY_UTC + timedelta(seconds=late))
                    self.assertNotIn("\u2014", message)
                    self.assertNotIn("\u2013", message)
                    self.assertIn(DINNER, message)


if __name__ == "__main__":
    unittest.main()
