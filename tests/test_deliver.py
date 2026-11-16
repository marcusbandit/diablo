import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import bot, db, deliver, devices, events, lockguard, mailbox, mcp_server, questions, server, settings

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
        self.events = []
        publish = mock.patch.object(events, "publish", side_effect=self.events.append)
        publish.start()
        self.addCleanup(publish.stop)
        for listener in (self.on_message, questions.link):
            db.listeners.append(listener)
            self.addCleanup(db.listeners.remove, listener)
        self.push = mock.AsyncMock(return_value=True)
        self.card = mock.AsyncMock(return_value=True)
        self.asks = mock.AsyncMock(return_value=0)
        for name, fake in (("push", self.push), ("push_card", self.card), ("push_questions", self.asks)):
            patch = mock.patch.object(bot, name, fake)
            patch.start()
            self.addCleanup(patch.stop)
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)

    def on_message(self, message_id, source, meta):
        events.message_added(message_id, source, meta, db.MAIN_SOURCES)

    def last(self):
        return db.get_messages(1)[-1]

    def messages(self):
        return [e for e in self.events if e.get("kind") == "message"]


class TestDeliver(Case):
    def test_a_proactive_message_lands_in_the_app_and_not_on_telegram(self):
        done = asyncio.run(deliver.deliver("The hub thread is finished.", "t3"))
        stored = self.last()
        self.assertEqual(stored["id"], done["id"])
        self.assertEqual(stored["role"], "assistant")
        self.assertEqual(stored["text"], "The hub thread is finished.")
        self.assertEqual(stored["meta"]["section"], "diablo")
        self.assertTrue(stored["meta"]["pushed"])
        self.assertEqual(stored["meta"]["kind"], "t3")
        self.assertEqual(self.messages(), [{"kind": "message", "section": "diablo", "id": done["id"]}])
        self.assertFalse(done["telegram"])
        self.push.assert_not_called()
        self.card.assert_not_called()

    def test_a_message_for_a_telegram_turn_also_goes_to_telegram(self):
        done = asyncio.run(deliver.deliver("Here is the report.", "report", origin="telegram voice note"))
        self.push.assert_awaited_once_with("Here is the report.")
        self.assertTrue(done["telegram"])
        self.assertEqual(self.last()["meta"]["kind"], "report")

    def test_all_brings_back_telegram_for_everything(self):
        settings.set_setting("delivery.telegram", "all")
        asyncio.run(deliver.deliver("Mailed Jorbis.", "scheduled", telegram_text="Scheduled task done. Mailed Jorbis."))
        self.push.assert_awaited_once_with("Scheduled task done. Mailed Jorbis.")
        self.assertEqual(self.last()["text"], "Mailed Jorbis.")
        self.assertEqual(len(self.messages()), 1)
        settings.set_setting("delivery.telegram", "something else")
        self.assertEqual(deliver.mode(), "replies")

    def test_a_question_rides_on_the_message_and_reaches_telegram_only_in_all(self):
        q = questions.ask("main", {"question": "Ship it?", "options": ["Yes", "No"]})
        block = questions.public(q)
        asyncio.run(deliver.deliver("A thread needs you.", "t3", objects=[block]))
        self.asks.assert_not_called()
        self.assertEqual(questions.get(q["id"])["message"], self.last()["id"])
        settings.set_setting("delivery.telegram", "all")
        asyncio.run(deliver.deliver("Another one.", "t3", objects=[block]))
        self.asks.assert_awaited_once()

    def test_html_is_drawn_as_markdown_in_the_app(self):
        asyncio.run(deliver.deliver('<b>Jorbis</b>: a &amp; b\n<a href="https://example.com/m?a=1&amp;b=2">Open in Gmail</a>',
                                    "mail", markup="html"))
        self.assertEqual(self.last()["text"], "**Jorbis**: a & b\n[Open in Gmail](https://example.com/m?a=1&b=2)")
        self.card.assert_not_called()

    def test_a_client_id_is_delivered_once(self):
        first = asyncio.run(deliver.deliver("Drink water.", "reminder", client_id="remind-1"))
        again = asyncio.run(deliver.deliver("Drink water.", "reminder", client_id="remind-1"))
        self.assertEqual(again, {"id": first["id"], "telegram": False, "repeat": True})
        self.assertEqual(len(self.messages()), 1)


class TestNotifyRoute(Case):
    def test_notify_stores_in_the_app_and_keeps_telegram_out(self):
        client = TestClient(server.app)
        body = {"text": "Drink water.", "kind": "reminder", "client_id": "remind-7"}
        first = client.post("/v1/notify", headers=HEADERS, json=body).json()
        again = client.post("/v1/notify", headers=HEADERS, json=body).json()
        self.assertEqual(first["ok"], True)
        self.assertFalse(first["telegram"])
        self.assertEqual(again["id"], first["id"])
        self.assertTrue(again["repeat"])
        self.assertEqual(self.last()["meta"]["kind"], "reminder")
        self.push.assert_not_called()
        client.post("/v1/notify", headers=HEADERS, json={"text": "From the chat.", "origin": "telegram"})
        self.push.assert_awaited_once_with("From the chat.")

    def test_a_t3_event_lands_in_the_app(self):
        asyncio.run(server.deliver("The thread waits on you.", {"conv": "main", "thread_id": "t1"}))
        asyncio.run(server.deliver("I restarted myself.", {"conv": "main", "thread_id": "restart"}))
        kinds = [m["meta"]["kind"] for m in db.get_messages(5)]
        self.assertEqual(kinds, ["t3", "restart"])
        self.push.assert_not_called()


class TestMcpNotify(Case):
    def test_the_tool_goes_through_the_hub_with_the_turn_origin(self):
        lockguard.arm("main", lockguard.Guard(origin="telegram"))
        response = mock.Mock(status_code=200, json=lambda: {"ok": True, "telegram": True})
        with mock.patch.object(mcp_server.httpx, "post", return_value=response) as post, \
                mock.patch.object(mcp_server, "get_config", return_value={"hub_token": TOKEN}), \
                mock.patch.dict("os.environ", {"DIABLO_CONVERSATION": "main"}):
            said = mcp_server.notify("Done, sir.")
        self.assertEqual(said, "sent to Telegram and the app")
        self.assertTrue(post.call_args.args[0].endswith("/v1/notify"))
        self.assertEqual(post.call_args.kwargs["json"], {"text": "Done, sir.", "kind": "report", "origin": "telegram"})

    def test_with_the_hub_away_it_is_kept_in_the_app(self):
        with mock.patch.object(mcp_server.httpx, "post", side_effect=mcp_server.httpx.ConnectError("down")), \
                mock.patch.object(mcp_server, "get_config", return_value={"hub_token": TOKEN}):
            said = mcp_server.notify("Done, sir.")
        self.assertTrue(said.startswith("saved in his app"))
        self.assertEqual(self.last()["meta"]["section"], "diablo")

    def test_a_voice_note_off_telegram_goes_to_the_app_as_text(self):
        lockguard.arm("main", lockguard.Guard(origin="phone app"))
        with mock.patch.object(mcp_server, "notify", return_value="sent to his app") as notify, \
                mock.patch.dict("os.environ", {"DIABLO_CONVERSATION": "main"}):
            said = mcp_server.send_voice("Good evening.", "en")
        notify.assert_called_once_with("Good evening.", "voice")
        self.assertIn("as text", said)


class TestMailApprovals(Case):
    def approval(self):
        return mailbox.write(
            "INSERT INTO approvals (mail_id, sender, what, task, task_kind, payload, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (0, JORBIS, "task", "Book the meeting room", "calendar", "{}", 0),
        )

    def ask(self, approval_id):
        buttons = [[("Approve", f"mail:approve:{approval_id}"), ("Deny", f"mail:deny:{approval_id}")]]
        self.assertTrue(asyncio.run(mailbox.notify("<b>Jorbis asks</b>: Book the meeting room", buttons)))
        return self.last()["meta"]["objects"][0]

    def test_an_approval_is_a_question_card_whose_answer_approves(self):
        approval_id = self.approval()
        block = self.ask(approval_id)
        self.assertEqual(block["kind"], "question")
        self.assertEqual([o["label"] for o in block["options"]], ["Approve", "Deny"])
        self.assertFalse(block["allow_text"])
        self.assertEqual(self.last()["text"], "**Jorbis asks**: Book the meeting room")
        self.card.assert_not_called()
        with mock.patch.object(mailbox, "start_task") as start, \
                mock.patch.object(server.brains, "ask") as turn, \
                mock.patch.object(server.lockguard, "for_turn", return_value=lockguard.Guard()):
            response = TestClient(server.app).post(f"/v1/questions/{block['id']}/answer", headers=HEADERS,
                                                   json={"choices": [0], "source": "phone", "client_id": "tap-1"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["reply"], f"Approved #{approval_id}. Diablo is on it.")
        self.assertEqual(body["answered"], block["id"])
        turn.assert_not_called()
        start.assert_called_once_with(approval_id)
        self.assertEqual(mailbox.approval(approval_id)["status"], "approved")
        self.assertEqual(questions.get(block["id"])["state"], "answered")
        self.assertEqual(db.get_message(body["user_id"])["text"], "Approve")

    def test_telegram_gets_the_buttons_only_in_all(self):
        settings.set_setting("delivery.telegram", "all")
        approval_id = self.approval()
        self.ask(approval_id)
        self.card.assert_awaited_once()
        self.assertEqual(self.card.call_args.args[1][0][0], ("Approve", f"mail:approve:{approval_id}"))

    def test_a_decision_made_elsewhere_closes_the_card(self):
        approval_id = self.approval()
        block = self.ask(approval_id)
        said = asyncio.run(mailbox.decide(approval_id, "deny"))
        self.assertEqual(said, f"Denied #{approval_id}.")
        self.assertEqual(questions.get(block["id"])["state"], "cancelled")

    def test_an_approval_card_never_replaces_his_question(self):
        mine = questions.ask("main", {"question": "Ship it?", "options": ["Yes", "No"]})
        self.ask(self.approval())
        self.assertEqual(questions.get(mine["id"])["state"], "pending")


if __name__ == "__main__":
    unittest.main()
