import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest import mock

from diablo_reminders import cli, scheduler, telegram, voice
from diablo_reminders.store import Store
from diablo_reminders.telegram import Router, SendError, TaskSender


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


ADDED = utc(2026, 11, 9, 0, 0)
DUE = datetime(2026, 11, 9, 5, 30)
DUE_UTC = utc(2026, 11, 9, 4, 30)
MAIL = "Mail Jorbis about telling each other when limits run low."


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, text, meta=None):
        self.sent.append((text, meta))
        return None


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "reminders.db")
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()


class TestTaskKind(Base):
    def test_a_task_goes_to_the_hub_and_a_reminder_to_telegram(self):
        remind, task = Recorder(), Recorder()
        self.store.add("Drink water", DUE, now=ADDED)
        self.store.add(MAIL, DUE, now=ADDED, kind="task")
        scheduler.tick(self.store, Router(remind, task), now=DUE_UTC)
        self.assertEqual(len(remind.sent), 1)
        self.assertIn("Drink water", remind.sent[0][0])
        text, meta = task.sent[0]
        self.assertEqual(meta["kind"], "task")
        self.assertTrue(text.startswith("[scheduled task #2 for"))
        self.assertNotIn("set by Marcus", text)
        self.assertTrue(meta["client_id"].startswith("task-2-"))
        self.assertIn(MAIL, text)
        self.assertNotIn("remind you", text)

    def test_a_task_fires_once_even_across_a_restart(self):
        task = Recorder()
        self.store.add(MAIL, DUE, now=ADDED, kind="task")
        scheduler.tick(self.store, Router(Recorder(), task), now=DUE_UTC)
        self.store.close()
        self.store = Store(self.path)
        self.store.recover()
        scheduler.tick(self.store, Router(Recorder(), task), now=DUE_UTC)
        self.assertEqual(len(task.sent), 1)

    def test_an_old_store_without_kinds_is_migrated_and_keeps_its_reminders(self):
        self.store.close()
        os.remove(self.path)
        conn = sqlite3.connect(self.path)
        conn.executescript(
            "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, anchor_local TEXT NOT NULL,"
            " tz TEXT NOT NULL, recurrence TEXT NOT NULL, next_local TEXT, next_due REAL, status TEXT NOT NULL,"
            " source TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
            "INSERT INTO reminders (text, anchor_local, tz, recurrence, next_local, next_due, status, created_at, updated_at)"
            " VALUES ('old one', '2026-11-09T05:30', 'Europe/Copenhagen', 'none', '2026-11-09T05:30', 1794544200, 'active', 0, 0);"
        )
        conn.commit()
        conn.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get(1)["kind"], "remind")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            self.store.add(MAIL, DUE, now=ADDED, kind="email")

    def test_the_cli_sets_a_task_and_lists_it_as_one(self):
        out = io.StringIO()
        with mock.patch.object(cli, "now_utc", return_value=ADDED):
            code = cli.main(["--db", self.path, "--json", "add", "--task", "2026-11-09T05:30", MAIL], out=out)
        self.assertEqual(code, 0)
        reply = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertTrue(reply["message"].startswith("Task #1 set"))
        self.assertEqual(reply["result"]["kind"], "task")
        out = io.StringIO()
        with mock.patch.object(cli, "now_utc", return_value=ADDED):
            cli.main(["--db", self.path, "list"], out=out)
        self.assertIn("task, ", out.getvalue())


class TestTaskSender(unittest.TestCase):
    def test_it_posts_the_instruction_with_the_delivery_as_client_id(self):
        seen = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(request, timeout=0):
            seen.update(url=request.full_url, body=json.loads(request.data), token=request.get_header("X-diablo-token"))
            return Response(b'{"ok": true, "queued": true}')

        env = {"REMIND_HUB_TOKEN": "t0ken", "REMIND_HUB_URL": "http://hub:8755/"}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", urlopen):
            TaskSender().send("do it", {"delivery": 7, "kind": "task", "client_id": "task-3-100-90"})
        self.assertEqual(seen["url"], "http://hub:8755/v1/scheduled")
        self.assertEqual(seen["body"], {"text": "do it", "client_id": "task-3-100-90"})
        self.assertEqual(seen["token"], "t0ken")

    def test_a_hub_that_is_down_is_a_retryable_send_error(self):
        with mock.patch.dict(os.environ, {"REMIND_HUB_TOKEN": "t0ken", "REMIND_HUB_URL": "http://hub:8755"}), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            with self.assertRaises(SendError):
                TaskSender().send("do it", {"delivery": 7})

    def test_without_a_hub_url_it_says_so(self):
        with mock.patch.dict(os.environ, {"REMIND_HUB_TOKEN": "t0ken", "REMIND_HUB_URL": ""}):
            with self.assertRaisesRegex(SendError, "REMIND_HUB_URL"):
                TaskSender().send("do it", {"delivery": 7})

    def test_the_unit_points_tasks_at_the_hub(self):
        unit = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "diablo-reminders.service")).read()
        self.assertIn("Environment=REMIND_HUB_URL=", unit)


class Answer:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


HUB_ENV = {"REMIND_HUB_TOKEN": "t0ken", "REMIND_HUB_URL": "http://hub:8755"}
META = {"reminder": 3, "delivery": 7, "kind": "remind", "client_id": "task-3-100-90"}


class TestReminderToTheApp(unittest.TestCase):
    def sender(self):
        self.telegram = Recorder()
        return telegram.Fallback(telegram.HubSender(), self.telegram)

    def test_a_reminder_goes_to_the_hub_notify_and_not_to_telegram(self):
        seen = {}

        def urlopen(request, timeout=0):
            seen.update(url=request.full_url, body=json.loads(request.data), token=request.get_header("X-diablo-token"))
            return Answer(b'{"ok": true, "id": 12, "telegram": false}')

        with mock.patch.dict(os.environ, HUB_ENV), mock.patch("urllib.request.urlopen", urlopen):
            self.sender().send("Drink water", META)
        self.assertEqual(seen["url"], "http://hub:8755/v1/notify")
        self.assertEqual(seen["body"], {"text": "Drink water", "kind": "reminder", "client_id": "remind-task-3-100-90"})
        self.assertEqual(seen["token"], "t0ken")
        self.assertEqual(self.telegram.sent, [])

    def test_a_hub_that_cannot_be_reached_hands_it_to_telegram(self):
        down = urllib.error.URLError(ConnectionRefusedError("refused"))
        with mock.patch.dict(os.environ, HUB_ENV), mock.patch("urllib.request.urlopen", side_effect=down):
            self.sender().send("Drink water", META)
        self.assertEqual(self.telegram.sent, [("Drink water", META)])
        with mock.patch.dict(os.environ, {"REMIND_HUB_TOKEN": "t0ken", "REMIND_HUB_URL": ""}):
            self.sender().send("Drink water", META)
        self.assertEqual(len(self.telegram.sent), 1)

    def test_a_hub_that_refuses_the_token_hands_it_to_telegram_at_once(self):
        refused = urllib.error.HTTPError("http://hub:8755/v1/notify", 403, "no", {}, io.BytesIO(b"bad token"))
        with mock.patch.dict(os.environ, HUB_ENV), mock.patch("urllib.request.urlopen", side_effect=refused):
            self.sender().send("Drink water", META)
        self.assertEqual(self.telegram.sent, [("Drink water", META)])

    def test_a_hub_that_got_it_but_failed_is_retried_then_telegram_takes_it(self):
        clock = [0.0]
        sender = telegram.Fallback(telegram.HubSender(clock=lambda: clock[0]), Recorder())
        self.telegram = sender.then
        broken = urllib.error.HTTPError("http://hub:8755/v1/notify", 500, "boom", {}, io.BytesIO(b"boom"))
        with mock.patch.dict(os.environ, HUB_ENV), mock.patch("urllib.request.urlopen", side_effect=broken):
            for wait in (0, 30):
                clock[0] += wait
                with self.assertRaises(SendError) as caught:
                    sender.send("Drink water", META)
                self.assertNotIsInstance(caught.exception, telegram.HubDown)
                self.assertEqual(self.telegram.sent, [])
            clock[0] += 60
            sender.send("Drink water", META)
        self.assertEqual(self.telegram.sent, [("Drink water", META)])

    def test_a_slow_hub_gives_up_to_telegram_after_the_window(self):
        clock = [0.0]
        sender = telegram.Fallback(telegram.HubSender(clock=lambda: clock[0], tries=10), Recorder())
        with mock.patch.dict(os.environ, HUB_ENV), mock.patch("urllib.request.urlopen", side_effect=TimeoutError("read")):
            with self.assertRaises(SendError):
                sender.send("Drink water", META)
            clock[0] += 121
            sender.send("Drink water", META)
        self.assertEqual(len(sender.then.sent), 1)

    def test_a_hub_that_recovers_takes_it_and_telegram_never_does(self):
        calls = []

        def urlopen(request, timeout=0):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("read")
            return Answer(b'{"ok": true, "id": 3}')

        sender = self.sender()
        with mock.patch.dict(os.environ, HUB_ENV), mock.patch("urllib.request.urlopen", urlopen):
            with self.assertRaises(SendError):
                sender.send("Drink water", META)
            sender.send("Drink water", META)
        self.assertEqual(self.telegram.sent, [])

    def test_the_service_sends_reminders_through_the_hub(self):
        router = telegram.make_sender()
        first = router.senders["remind"]
        self.assertIsInstance(first, telegram.Fallback)
        self.assertIsInstance(first.first, telegram.HubSender)
        self.assertIsInstance(first.then, telegram.TelegramSender)


EM_DASH = chr(0x2014)


class TestTaskVoice(unittest.TestCase):
    def test_a_late_task_says_so_without_guessing_why(self):
        row = {"reminder_id": 3, "due_local": "2026-11-09T05:30", "due_at": DUE_UTC.timestamp(), "text": MAIL, "source": "cli"}
        text = voice.task(row, utc(2026, 11, 9, 6, 0))
        self.assertIn("running late", text)
        self.assertNotIn("Pi was down", text)
        self.assertIn("Marcus set it by hand", text)
        self.assertNotIn(EM_DASH, text)

    def test_a_task_diablo_set_says_so(self):
        row = {"reminder_id": 3, "due_local": "2026-11-09T05:30", "due_at": DUE_UTC.timestamp(), "text": MAIL, "source": "diablo"}
        self.assertIn("you set it in a conversation", voice.task(row, DUE_UTC))


class TestStaleTasks(Base):
    def test_a_task_hours_past_its_time_is_not_run_but_reported(self):
        remind, task = Recorder(), Recorder()
        self.store.add(MAIL, DUE, now=ADDED, kind="task")
        scheduler.tick(self.store, Router(remind, task), now=utc(2026, 11, 9, 7, 0))
        self.assertEqual(task.sent, [])
        self.assertIn("could not run in time", remind.sent[0][0])
        self.assertIn(MAIL, remind.sent[0][0])


if __name__ == "__main__":
    unittest.main()
