"""The scheduler as a real process: killed hard, started again, nothing lost or doubled."""

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from diablo_reminders import cli
from diablo_reminders import schedule as s
from diablo_reminders.store import Store

ROOT = Path(__file__).resolve().parent.parent


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class SchedulerProcess(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "reminders.db")
        self.outbox = os.path.join(self.dir.name, "outbox.jsonl")
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.dir.cleanup()

    def start(self):
        env = dict(os.environ, REMIND_DB=self.db, REMIND_OUTBOX=self.outbox, PYTHONPATH=str(ROOT))
        proc = subprocess.Popen([sys.executable, "-m", "diablo_reminders", "run"], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.procs.append(proc)
        return proc

    def sent(self):
        try:
            return [json.loads(line) for line in Path(self.outbox).read_text().splitlines()]
        except FileNotFoundError:
            return []

    def add_missed(self, text, minutes_ago):
        """A reminder whose time passed while no scheduler ran."""
        store = Store(self.db)
        try:
            past = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
            wall = s.to_wall(past).replace(second=0, microsecond=0)
            return store.add(text, wall, now=past - timedelta(hours=1))
        finally:
            store.close()

    def delivery_status(self):
        store = Store(self.db)
        try:
            return [d["status"] for d in store.deliveries()]
        finally:
            store.close()

    def test_missed_reminder_fires_late_once_across_kills(self):
        row = self.add_missed("Dinner with Sofus", 90)
        proc = self.start()
        self.assertTrue(wait_for(lambda: self.delivery_status() == ["sent"]))
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        for _ in range(2):
            proc = self.start()
            time.sleep(1.0)
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait()
        [message] = self.sent()
        self.assertEqual(message["reminder"], row["id"])
        self.assertIn("arrives late", message["text"])

    def test_kill_between_send_and_mark_resends_once(self):
        """The documented window: Telegram took it, the sent mark was not written yet."""
        self.add_missed("Dinner with Sofus", 90)
        store = Store(self.db)
        try:
            [delivery_id] = store.claim_due(datetime.now(timezone.utc))
            store.begin_attempt(delivery_id, datetime.now(timezone.utc))
        finally:
            store.close()
        Path(self.outbox).write_text(json.dumps({"text": "first copy"}) + "\n")
        proc = self.start()
        self.assertTrue(wait_for(lambda: self.delivery_status() == ["sent"]))
        time.sleep(1.0)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
        self.assertEqual(len(self.sent()), 2)  # one repeat, never a loss, never more

    def test_second_scheduler_refuses_to_run(self):
        first = self.start()
        time.sleep(0.5)
        second = self.start()
        self.assertEqual(second.wait(timeout=10), 1)
        self.assertIn("another scheduler", second.stderr.read())
        first.send_signal(signal.SIGTERM)
        self.assertEqual(first.wait(timeout=20), 0)

    def test_new_reminder_is_picked_up_while_running(self):
        proc = self.start()
        store = Store(self.db)
        try:
            now = datetime.now(timezone.utc)
            store.add("soon", s.to_wall(now).replace(second=0, microsecond=0), "daily", now=now - timedelta(minutes=1))
        finally:
            store.close()
        self.assertTrue(wait_for(lambda: len(self.sent()) == 1, timeout=20))
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "reminders.db")

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *argv):
        out = io.StringIO()
        code = cli.main(["--db", self.db, "--json", *argv], out=out)
        return code, json.loads(out.getvalue())

    def test_add_list_cancel(self):
        code, added = self.run_cli("add", "2099-11-08T13:00", "Time", "to", "go", "home", "--repeat", "weekly")
        self.assertEqual(code, 0)
        self.assertEqual(added["result"]["text"], "Time to go home")
        self.assertEqual(added["result"]["repeats"], "every Sunday at 13:00")
        self.assertIn("Sunday 8 November 2099 at 13:00", added["message"])
        _, listed = self.run_cli("list")
        self.assertEqual([r["id"] for r in listed["result"]], [added["result"]["id"]])
        code, cancelled = self.run_cli("cancel", str(added["result"]["id"]))
        self.assertEqual((code, cancelled["result"]["status"]), (0, "cancelled"))
        _, listed = self.run_cli("list")
        self.assertEqual(listed["result"], [])
        _, everything = self.run_cli("list", "--all")
        self.assertEqual(len(everything["result"]), 1)

    def test_errors_are_reported(self):
        code, reply = self.run_cli("add", "2001-01-01T10:00", "past")
        self.assertEqual((code, reply["ok"]), (2, False))
        self.assertIn("already passed", reply["error"])
        code, reply = self.run_cli("cancel", "42")
        self.assertEqual(code, 2)
        self.assertIn("no reminder #42", reply["error"])


if __name__ == "__main__":
    unittest.main()
