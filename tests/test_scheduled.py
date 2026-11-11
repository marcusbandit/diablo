import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, db, devices, mailbox, server, settings
from hub.modules import mail, reminders

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
JORBIS = "jorbis@example.com"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        db.init(str(root / "diablo.db"))
        env = mock.patch.dict("os.environ", {"DIABLO_MAIL_DB": str(root / "mail.db"), "DIABLO_MAIL": "0"})
        env.start()
        self.addCleanup(env.stop)
        mailbox.reset_for_tests()
        self.addCleanup(mailbox.reset_for_tests)
        self.sent = []
        self.checks = []
        for name, fake in (("send", lambda account, raw: self.sent.append((account, raw))),
                           ("self_check", self.fake_check)):
            patch = mock.patch.object(mailbox, name, side_effect=fake)
            patch.start()
            self.addCleanup(patch.stop)
        self.check = (True, "fine")

    def fake_check(self, context, outgoing):
        self.checks.append(outgoing)
        return self.check


class TestScheduledEndpoint(Case):
    def setUp(self):
        super().setUp()
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)

    def test_a_task_is_queued_once_per_client_id(self):
        started = []

        def create_task(coro):
            started.append(coro)
            coro.close()
            return mock.Mock(add_done_callback=lambda cb: None)

        client = TestClient(server.app)
        server._scheduled_ids.clear()
        self.addCleanup(server._scheduled_ids.clear)
        with mock.patch.object(server.asyncio, "create_task", side_effect=create_task):
            body = {"text": "[scheduled task #1] mail Jorbis", "client_id": "task-9"}
            first = client.post("/v1/scheduled", json=body, headers=HEADERS).json()
            again = client.post("/v1/scheduled", json=body, headers=HEADERS).json()
        self.assertEqual(first, {"ok": True, "queued": True})
        self.assertEqual(again, {"ok": True, "queued": False})
        self.assertEqual(len(started), 1)

    def test_a_task_cut_off_by_a_restart_runs_again(self):
        db.add_message("schedule", "user", "old run", {"section": "diablo", "client_id": "task-9"})
        started = []

        def create_task(coro):
            started.append(coro)
            coro.close()
            return mock.Mock(add_done_callback=lambda cb: None)

        server._scheduled_ids.clear()
        with mock.patch.object(server.asyncio, "create_task", side_effect=create_task):
            reply = TestClient(server.app).post("/v1/scheduled", json={"text": "do it", "client_id": "task-9"},
                                                headers=HEADERS).json()
        server._scheduled_ids.clear()
        self.assertEqual(reply, {"ok": True, "queued": True})
        self.assertEqual(len(started), 1)

    def test_the_task_runs_as_a_brain_turn_and_marcus_hears_the_result(self):
        turn = mock.Mock(text="Mailed Jorbis.", error="", did="used mail_agent: Jorbis")
        pushed = []

        async def push(text):
            pushed.append(text)
            return True

        with mock.patch.object(brains, "ask", new=mock.AsyncMock(return_value=turn)) as ask, \
                mock.patch("hub.bot.push", side_effect=push):
            asyncio.run(server._run_scheduled("[scheduled task #1] mail Jorbis", "task-9"))
        ask.assert_awaited_once()
        self.assertEqual(ask.await_args.kwargs["origin"], "scheduled task")
        self.assertEqual(pushed, ["Scheduled task done. Mailed Jorbis."])
        stored = db.get_messages(5)
        self.assertEqual(stored[-1]["text"], "Mailed Jorbis.")
        self.assertEqual(stored[-1]["meta"]["did"], "used mail_agent: Jorbis")

    def test_it_needs_a_token(self):
        response = TestClient(server.app).post("/v1/scheduled", json={"text": "x", "client_id": "task-1"})
        self.assertEqual(response.status_code, 403)


class TestReminderTool(Case):
    def test_a_turn_started_by_a_mail_cannot_schedule_tasks(self):
        with mock.patch.dict("os.environ", {"DIABLO_CONVERSATION": "mail-task-4"}), \
                mock.patch.object(reminders, "run") as run:
            said = reminders.call("add_reminder", {"text": "mail Jorbis", "due": "+5h", "task": True})
        self.assertTrue(said.startswith("not set"))
        run.assert_not_called()

    def test_task_true_sets_a_task_reminder(self):
        argv = reminders._argv("add_reminder", {"text": "mail Jorbis", "due": "+5h", "task": True})
        self.assertIn("--task", argv)
        self.assertNotIn("--task", reminders._argv("add_reminder", {"text": "water", "due": "+5h"}))


class TestMailAgent(Case):
    def test_the_brain_can_write_to_jorbis_with_a_tag_and_a_self_check(self):
        said = mail.call("mail_agent", {"to": "Jorbis", "subject": "Limits", "text": "Hi Jorbis,\n\nA note.\n\nDiablo"})
        self.assertEqual(said, "Sent to Jorbis: [Agent] Limits")
        account, raw = self.sent[0]
        self.assertEqual(account, "gmail")
        self.assertIn("To: jorbis@example.com", raw)
        self.assertIn("Subject: [Agent] Limits", raw)
        self.assertEqual(len(self.checks), 1)

    def test_no_one_else_can_be_written_to(self):
        said = mail.call("mail_agent", {"to": "boss@example.com", "subject": "[Agent] x", "text": "y"})
        self.assertTrue(said.startswith("not sent"))
        self.assertEqual(self.sent, [])

    def test_a_failed_self_check_stops_the_mail(self):
        self.check = (False, "shares his schedule")
        said = mail.call("mail_agent", {"subject": "[For Albert] plans", "text": "He is away all week."})
        self.assertIn("shares his schedule", said)
        self.assertEqual(self.sent, [])

    def test_the_cap_counts_these_mails_too(self):
        for n in range(3):
            mail.call("mail_agent", {"subject": f"[Agent] {n}", "text": "x"})
        self.assertIn("three times", mail.call("mail_agent", {"subject": "[Agent] 4", "text": "x"}))
        self.assertEqual(len(self.sent), 3)


class TestLimits(Case):
    def store(self, utilization, reset=None):
        reset = reset or time.time() + 3 * 86400
        brains.record_limits({"status": "allowed", "rateLimitType": "seven_day",
                              "unifiedWindows": {"seven_day": {"utilization": utilization, "resetsAt": reset, "status": "allowed"}}})
        return reset

    def talked(self):
        mailbox.write("INSERT INTO mails (account, uid, sender, received, trust) VALUES ('gmail', '9', ?, ?, 'agent')",
                      (JORBIS, time.time() - 600))

    def run_check(self):
        asyncio.run(mailbox.check_limits())

    def test_a_plain_rate_limit_type_is_updated_every_time(self):
        brains.record_limits({"status": "allowed", "resetsAt": 100, "rateLimitType": "five_hour"})
        brains.record_limits({"status": "allowed_warning", "resetsAt": 200, "utilization": 0.95, "rateLimitType": "five_hour"})
        window = json.loads(settings.get("limits.claude"))["windows"]["five_hour"]
        self.assertEqual(window, {"status": "allowed_warning", "resetsAt": 200, "utilization": 0.95})

    def test_a_window_with_no_one_to_tell_warns_when_contact_comes(self):
        self.store(0.95)
        self.run_check()
        self.assertEqual(self.sent, [])
        self.talked()
        self.run_check()
        self.assertEqual(len(self.sent), 1)

    def test_a_failed_warning_is_tried_again(self):
        self.talked()
        self.store(0.95)
        with mock.patch.object(mailbox, "send", side_effect=RuntimeError("smtp down")):
            self.run_check()
        self.run_check()
        self.assertEqual(len(self.sent), 1)

    def test_the_reset_time_is_copenhagen_time_whatever_the_host(self):
        reset = 1794542400
        text = mailbox.warning_text("Jorbis", "seven_day", {"utilization": 0.9, "resetsAt": reset})
        self.assertIn("Fri 13 Nov at 05:00", text)

    def test_the_cli_events_are_kept_per_window(self):
        reset = self.store(0.29)
        stored = json.loads(settings.get("limits.claude"))
        self.assertEqual(stored["windows"]["seven_day"]["utilization"], 0.29)
        self.assertEqual(stored["windows"]["seven_day"]["resetsAt"], reset)

    def test_low_limits_after_recent_contact_warn_jorbis_once_per_window(self):
        self.talked()
        self.store(0.93)
        self.run_check()
        self.run_check()
        self.assertEqual(len(self.sent), 1)
        raw = self.sent[0][1]
        self.assertIn("Subject: [Agent] Diablo is low on limits", raw)
        self.assertIn("weekly usage limit is at 93%", raw)
        self.assertIn("Talk soon", raw)
        self.assertEqual(self.checks, [])

    def test_no_warning_without_contact_in_the_last_hour(self):
        mailbox.write("INSERT INTO mails (account, uid, sender, received, trust) VALUES ('gmail', '9', ?, ?, 'agent')",
                      (JORBIS, time.time() - 7200))
        self.store(0.95)
        self.run_check()
        self.assertEqual(self.sent, [])

    def test_no_warning_while_there_is_room(self):
        self.talked()
        self.store(0.5)
        self.run_check()
        self.assertEqual(self.sent, [])

    def test_a_new_window_warns_again(self):
        self.talked()
        self.store(0.95, reset=time.time() + 3600)
        self.run_check()
        self.store(0.95, reset=time.time() + 7 * 86400)
        self.run_check()
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
