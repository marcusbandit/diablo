import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import bot, brains, db, deliver, devices, events, lockguard, mailbox, mcp_server, questions, server, settings

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

    def test_mail_text_that_looks_like_markdown_stays_text(self):
        row = {"sender_name": "[Reset your password](https://evil.example)", "sender": JORBIS,
               "summary": "Click ![x](https://evil.example/p.png) *now* `code`"}
        text = mailbox.ping_text(row, "marcus@example.com", "https://mail.example.com/m?a=1&b=2")
        asyncio.run(deliver.deliver(text, "mail", markup="html"))
        shown = self.last()["text"]
        self.assertIn(r"**\[Reset your password\](https://evil.example)**", shown)
        self.assertIn(r"Click !\[x\](https://evil.example/p.png) \*now\* \`code\`", shown)
        self.assertEqual(shown.count("]("), 3)
        self.assertEqual(shown.count("\\]("), 2)
        self.assertTrue(shown.endswith("[Open in Gmail](https://mail.example.com/m?a=1&b=2), marcus@example.com"))
        self.assertEqual(deliver.plain("<b>a</b> [b](c)", markdown=False), "a [b](c)")

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
    def turn(self, origin, key="main"):
        path = lockguard.turn_path("main", key)
        lockguard.note_turn(path, origin)
        return mock.patch.dict("os.environ", {"DIABLO_CONVERSATION": "main", "DIABLO_TURN_FILE": str(path)})

    def test_the_tool_goes_through_the_hub_with_the_turn_origin(self):
        response = mock.Mock(status_code=200, json=lambda: {"ok": True, "telegram": True})
        with self.turn("telegram"), mock.patch.object(mcp_server.httpx, "post", return_value=response) as post, \
                mock.patch.object(mcp_server, "get_config", return_value={"hub_token": TOKEN}):
            said = mcp_server.notify("Done, sir.")
        self.assertEqual(said, "sent to Telegram and the app")
        self.assertTrue(post.call_args.args[0].endswith("/v1/notify"))
        sent = post.call_args.kwargs["json"]
        self.assertEqual({k: sent[k] for k in ("text", "kind", "origin")}, {"text": "Done, sir.", "kind": "report", "origin": "telegram"})
        self.assertTrue(sent["client_id"].startswith("notify-"))

    def test_a_turn_beside_never_changes_the_origin_of_the_turn_it_runs_beside(self):
        with self.turn("telegram"):
            lockguard.note_turn(lockguard.turn_path("main", "side-1"), "voice at his desk")
            lockguard.arm("main", lockguard.Guard(origin="voice at his desk"))
            self.assertEqual(mcp_server.turn_origin(), "telegram")
        main_env = brains.mcp_config("main")["mcpServers"]["diablo"]["env"]
        side_env = brains.mcp_config("main", "side-1")["mcpServers"]["diablo"]["env"]
        self.assertNotEqual(main_env["DIABLO_TURN_FILE"], side_env["DIABLO_TURN_FILE"])

    def test_with_the_hub_away_it_is_kept_in_the_app(self):
        with mock.patch.object(mcp_server.httpx, "post", side_effect=mcp_server.httpx.ConnectError("down")), \
                mock.patch.object(mcp_server, "get_config", return_value={"hub_token": TOKEN}):
            said = mcp_server.notify("Done, sir.")
        self.assertTrue(said.startswith("saved in his app"))
        self.assertEqual(self.last()["meta"]["section"], "diablo")

    def test_a_hub_that_stored_it_but_answered_late_leaves_one_copy(self):
        def slow(url, headers=None, json=None, timeout=None):
            asyncio.run(deliver.deliver(json["text"], json["kind"], client_id=json["client_id"]))
            raise mcp_server.httpx.ReadTimeout("slow telegram")

        with mock.patch.object(mcp_server.httpx, "post", side_effect=slow), \
                mock.patch.object(mcp_server, "get_config", return_value={"hub_token": TOKEN}):
            mcp_server.notify("Done, sir.")
        self.assertEqual([m["text"] for m in db.get_messages(5)], ["Done, sir."])

    def test_a_voice_note_off_telegram_goes_to_the_app_as_text(self):
        with self.turn("phone app"), mock.patch.object(mcp_server, "notify", return_value="sent to his app") as notify:
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
        offer = mailbox.choice(approval_id, ("Approve", "approve"), ("Deny", "deny"))
        self.assertTrue(asyncio.run(mailbox.notify("<b>Jorbis asks</b>: Book the meeting room", offer)))
        return self.last()["meta"]["objects"][0]

    def tap(self, block, client_id="tap-1", choices=(0,)):
        with mock.patch.object(server.brains, "ask") as turn, \
                mock.patch.object(server.lockguard, "for_turn", return_value=lockguard.Guard()):
            response = TestClient(server.app).post(f"/v1/questions/{block['id']}/answer", headers=HEADERS,
                                                   json={"choices": list(choices), "source": "phone", "client_id": client_id})
        turn.assert_not_called()
        return response

    def test_an_approval_is_a_question_card_whose_answer_approves(self):
        approval_id = self.approval()
        block = self.ask(approval_id)
        self.assertEqual(block["kind"], "question")
        self.assertEqual([o["label"] for o in block["options"]], ["Approve", "Deny"])
        self.assertFalse(block["allow_text"])
        self.assertEqual(self.last()["text"], "**Jorbis asks**: Book the meeting room")
        self.card.assert_not_called()
        response = self.tap(block)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["reply"], "Allowed: Book the meeting room. Diablo starts on it within a few seconds.")
        self.assertEqual(body["answered"], block["id"])
        self.assertEqual(mailbox.deciding(approval_id), "approve")
        self.assertEqual(questions.get(block["id"])["state"], "answered")
        self.assertEqual(db.get_message(body["user_id"])["text"], "Approve")
        with mock.patch.object(mailbox, "start_task") as start:
            asyncio.run(mailbox.drain_decisions())
        start.assert_called_once_with(approval_id)
        self.assertEqual(mailbox.approval(approval_id)["status"], "approved")

    def test_a_retried_tap_gets_the_stored_reply(self):
        block = self.ask(self.approval())
        first = self.tap(block).json()
        again = self.tap(block)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["id"], first["id"])
        self.assertEqual(again.json()["reply"], first["reply"])

    def test_a_panel_tap_closes_the_card_before_the_loop_runs_it(self):
        approval_id = self.approval()
        block = self.ask(approval_id)
        mailbox.queue_decision(approval_id, "approve")
        self.assertEqual(questions.get(block["id"])["state"], "cancelled")
        self.assertEqual(self.tap(block, choices=(1,)).status_code, 409)
        self.assertEqual(mailbox.deciding(approval_id), "approve")

    def test_a_card_tap_the_request_will_not_take_leaves_the_card_answered_with_why(self):
        approval_id = self.approval()
        block = self.ask(approval_id)
        mailbox.write("UPDATE approvals SET status = 'denied' WHERE id = ?", (approval_id,))
        body = self.tap(block).json()
        self.assertIn("not waiting for approval", body["reply"])
        self.assertEqual(questions.get(block["id"])["state"], "answered")

    def test_a_decision_that_fails_in_the_loop_says_so_in_the_app(self):
        approval_id = self.approval()
        self.tap(self.ask(approval_id))
        with mock.patch.object(mailbox, "decide", side_effect=RuntimeError("smtp is down")):
            asyncio.run(mailbox.drain_decisions())
        failed = self.last()
        self.assertEqual(failed["meta"]["kind"], "mail")
        self.assertIn("That did not go through", failed["text"])
        self.assertIn("smtp is down", failed["text"])

    def test_telegram_gets_the_buttons_only_in_all(self):
        settings.set_setting("delivery.telegram", "all")
        approval_id = self.approval()
        self.ask(approval_id)
        self.card.assert_awaited_once()
        self.assertEqual(self.card.call_args.args[1], [[("Approve", f"mail:approve:{approval_id}"), ("Deny", f"mail:deny:{approval_id}")]])

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
