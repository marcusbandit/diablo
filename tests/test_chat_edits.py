import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, db, devices, server

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


class EditCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def chat(self, text="hi", reply="Done.", fail=False, error="", **body):
        turn = brains.Turn(text=reply, backend="claude", error=error) if error else brains.Turn(text=reply, backend="claude")
        ask = mock.AsyncMock(side_effect=RuntimeError("down")) if fail else mock.AsyncMock(return_value=turn)
        with mock.patch.object(server.brains, "ask", ask), mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": text, "source": "desktop", **body})
        return response, ask

    def turn(self, text, reply, client_id):
        response, _ = self.chat(text, reply, client_id=client_id)
        self.assertEqual(response.status_code, 200)
        return response.json()


class TestEdit(EditCase):
    def test_an_edit_is_a_new_turn_that_withdraws_the_old_branch(self):
        first = self.turn("Book Friday", "Booked Friday.", "c1")
        second = self.turn("And a table for two", "Done, sir.", "c2")
        response, ask = self.chat("Book Saturday", "Booked Saturday.", client_id="c3", edits=first["user_id"])
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(ask.call_args.args[1], "Book Saturday")
        note = ask.call_args.kwargs["notes"][-1]
        self.assertIn("edited an earlier message", note)
        self.assertIn("Book Friday", note)
        new_user = db.get_message(body["user_id"])
        self.assertEqual(new_user["meta"]["edits"], first["user_id"])
        for withdrawn in (first["user_id"], first["id"], second["user_id"], second["id"]):
            self.assertEqual(db.get_message(withdrawn)["meta"]["superseded_by"], body["user_id"])
        self.assertNotIn("superseded_by", new_user["meta"])
        self.assertNotIn("superseded_by", db.get_message(body["id"])["meta"])

    def test_a_failed_edit_withdraws_nothing(self):
        first = self.turn("Book Friday", "Booked Friday.", "c1")
        response, _ = self.chat("Book Saturday", client_id="c2", edits=first["user_id"], fail=True)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("superseded_by", db.get_message(first["user_id"])["meta"])
        self.assertNotIn("superseded_by", db.get_message(first["id"])["meta"])

    def test_only_his_own_messages_in_the_section_can_be_edited(self):
        first = self.turn("hi", "hello", "c1")
        for bad in (first["id"], 9999):
            response, ask = self.chat("x", edits=bad, client_id="c2")
            self.assertEqual(response.status_code, 404)
            ask.assert_not_called()
        response, _ = self.chat("x", edits=first["user_id"], section="workout", client_id="c3")
        self.assertEqual(response.status_code, 404)

    def test_edit_and_again_together_are_refused(self):
        first = self.turn("hi", "hello", "c1")
        response, _ = self.chat("x", edits=first["user_id"], regenerates=first["id"], client_id="c2")
        self.assertEqual(response.status_code, 400)


class TestAnswerAgain(EditCase):
    def test_answer_again_asks_the_same_question_without_a_new_message(self):
        first = self.turn("What is on today?", "Nothing, sir.", "c1")
        before = len(db.get_messages(50))
        response, ask = self.chat("What is on today?", "Two meetings and the gym.", client_id="g1", regenerates=first["id"])
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user_id"], first["user_id"])
        self.assertEqual(body["reply"], "Two meetings and the gym.")
        self.assertEqual(len(db.get_messages(50)), before + 1)
        self.assertEqual(ask.call_args.args[1], "What is on today?")
        self.assertEqual(ask.call_args.kwargs["message_id"], first["user_id"])
        self.assertIn("answer this message again", ask.call_args.kwargs["notes"][-1])
        self.assertIn("Nothing, sir.", ask.call_args.kwargs["notes"][-1])
        self.assertEqual(db.get_message(first["id"])["meta"]["superseded_by"], body["id"])
        self.assertEqual(db.get_message(body["id"])["meta"]["regenerates"], first["id"])

    def test_the_question_comes_from_the_hub_not_the_client(self):
        first = self.turn("The real question", "Answer.", "c1")
        _, ask = self.chat("something else", client_id="g1", regenerates=first["id"])
        self.assertEqual(ask.call_args.args[1], "The real question")

    def test_a_retry_of_the_same_answer_again_gets_the_stored_reply(self):
        first = self.turn("q", "a", "c1")
        one = self.chat("q", "fresh", client_id="g1", regenerates=first["id"])[0].json()
        response, ask = self.chat("q", "other", client_id="g1", regenerates=first["id"])
        self.assertEqual(response.json()["id"], one["id"])
        self.assertEqual(response.json()["user_id"], first["user_id"])
        ask.assert_not_called()

    def test_a_retry_while_the_answer_again_runs_is_refused(self):
        first = self.turn("q", "a", "c1")
        server.AGAIN_RUNNING.add(("diablo", "g1"))
        self.addCleanup(server.AGAIN_RUNNING.clear)
        response, ask = self.chat("q", client_id="g1", regenerates=first["id"])
        self.assertEqual(response.status_code, 409)
        ask.assert_not_called()

    def test_an_answer_again_that_errs_changes_nothing(self):
        first = self.turn("q", "a", "c1")
        before = len(db.get_messages(50))
        response, _ = self.chat("q", "", client_id="g1", regenerates=first["id"], error="rate limited")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(db.get_messages(50)), before)
        self.assertNotIn("superseded_by", db.get_message(first["id"])["meta"])
        self.assertFalse(server.AGAIN_RUNNING)

    def test_a_failed_answer_again_keeps_the_question_and_the_old_reply(self):
        first = self.turn("q", "a", "c1")
        response, _ = self.chat("q", client_id="g1", regenerates=first["id"], fail=True)
        self.assertEqual(response.status_code, 502)
        self.assertIsNotNone(db.get_message(first["user_id"]))
        self.assertNotIn("superseded_by", db.get_message(first["id"])["meta"])

    def test_only_a_reply_can_be_answered_again(self):
        first = self.turn("q", "a", "c1")
        for bad in (first["user_id"], 4242):
            response, ask = self.chat("q", client_id="g1", regenerates=bad)
            self.assertEqual(response.status_code, 404)
            ask.assert_not_called()

class TestStop(EditCase):
    def stop(self, running, **body):
        stop = mock.AsyncMock(return_value=running)
        with mock.patch.object(server.brains, "stop", stop):
            response = self.client.post("/v1/turns/stop", headers=HEADERS, json=body)
        return response, stop

    def test_a_running_turn_is_stopped_in_the_conversation_of_its_section(self):
        response, stop = self.stop(True, section="diablo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"stopped": True})
        stop.assert_awaited_once_with(server.sections.conv_for("diablo"))

    def test_nothing_running_says_so_and_a_missing_section_is_a_404(self):
        response, _ = self.stop(False)
        self.assertEqual(response.json(), {"stopped": False})
        response, stop = self.stop(True, section="nowhere")
        self.assertEqual(response.status_code, 404)
        stop.assert_not_awaited()

    def test_stopping_needs_the_token(self):
        response = self.client.post("/v1/turns/stop", json={})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
