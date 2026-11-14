import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import db, devices, sections, server, standby

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        standby._state = {"state": "mirror"}
        self.config = {"hub_token": TOKEN}
        for patch in (mock.patch.object(server, "get_config", side_effect=lambda: self.config),
                      mock.patch.object(devices, "get_config", side_effect=lambda: self.config),
                      mock.patch.object(standby, "get_config", side_effect=lambda: self.config)):
            patch.start()
            self.addCleanup(patch.stop)
        self.client = TestClient(server.app)

    def page(self, **params):
        response = self.client.get("/v1/history", params=params, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def texts(self, **params):
        return [m["text"] for m in self.page(**params)["messages"]]

    def test_needs_the_token(self):
        self.assertEqual(self.client.get("/v1/history").status_code, 403)
        self.assertEqual(self.client.get("/v1/history", headers={"X-Diablo-Token": "wrong"}).status_code, 403)

    def test_every_channel_oldest_first_with_how_it_was_said(self):
        db.add_message("voice", "user", "Turn the desk lamp down", None)
        db.add_message("voice", "assistant", "Done, sir.", None)
        db.add_message("telegram", "user", "Is the oven still on?", {"voice": True})
        db.add_message("telegram", "assistant", "It is off, sir.", None)
        db.add_message("desktop", "user", "Write the shopping list", {"section": "diablo"})
        db.add_message("api", "user", "Rename the parrot", {"chat": "c1"})
        got = self.page()["messages"]
        self.assertEqual([m["text"] for m in got], ["Turn the desk lamp down", "Done, sir.", "Is the oven still on?",
                                                    "It is off, sir.", "Write the shopping list", "Rename the parrot"])
        self.assertEqual([m["channel"] for m in got], ["voice", "voice", "telegram", "telegram", "desktop", "api"])
        self.assertEqual([m["spoken"] for m in got if m["role"] == "user"], [True, True, False, False])
        self.assertEqual(got[4]["section"], "diablo")
        self.assertTrue(all(isinstance(m["ts"], float) for m in got))

    def test_pages_go_back_in_time_until_nothing_is_left(self):
        for n in range(7):
            db.add_message("voice", "user", f"line {n}", None)
        first = self.page(limit=3)
        self.assertEqual([m["text"] for m in first["messages"]], ["line 4", "line 5", "line 6"])
        self.assertTrue(first["more"])
        second = self.page(limit=3, before=first["messages"][0]["id"])
        self.assertEqual([m["text"] for m in second["messages"]], ["line 1", "line 2", "line 3"])
        self.assertTrue(second["more"])
        last = self.page(limit=3, before=second["messages"][0]["id"])
        self.assertEqual([m["text"] for m in last["messages"]], ["line 0"])
        self.assertFalse(last["more"])

    def test_search_needs_every_word_in_any_case(self):
        db.add_message("voice", "user", "Water the BASIL on the balcony", None)
        db.add_message("telegram", "assistant", "The basil is watered, sir.", None)
        db.add_message("desktop", "user", "Buy basil seeds", None)
        self.assertEqual(self.texts(q="basil water"), ["Water the BASIL on the balcony", "The basil is watered, sir."])
        self.assertEqual(self.texts(q="SEEDS"), ["Buy basil seeds"])
        self.assertEqual(self.texts(q="100%"), [])

    def test_channel_filter_and_counts(self):
        db.add_message("voice", "user", "spoken one", None)
        db.add_message("telegram", "user", "typed one", None)
        db.add_message("phone", "user", "phone one", None)
        body = self.page(channel="telegram,phone")
        self.assertEqual([m["text"] for m in body["messages"]], ["typed one", "phone one"])
        self.assertEqual(body["channels"], {"voice": 1, "telegram": 1, "phone": 1})
        self.assertEqual(self.texts(channel="voice", q="one"), ["spoken one"])

    def test_withdrawn_lines_stay_out(self):
        old = db.add_message("telegram", "user", "Book a table for two", None)
        new = db.add_message("telegram", "user", "Book a table for four", None)
        db.supersede([old], new)
        self.assertEqual(self.texts(), ["Book a table for four"])
        self.assertEqual(self.page()["channels"], {"telegram": 1})

    def test_after_brings_only_newer_lines_without_counts(self):
        first = db.add_message("voice", "user", "first", None)
        for n in range(5):
            db.add_message("telegram", "user", f"new {n}", None)
        body = self.page(after=first, limit=3)
        self.assertEqual([m["text"] for m in body["messages"]], ["new 0", "new 1", "new 2"])
        self.assertTrue(body["more"])
        self.assertNotIn("channels", body)
        rest = self.page(after=body["messages"][-1]["id"], limit=3)
        self.assertEqual([m["text"] for m in rest["messages"]], ["new 3", "new 4"])
        self.assertFalse(rest["more"])
        self.assertEqual(self.texts(after=rest["messages"][-1]["id"]), [])

    def test_deleted_sections_stay_out(self):
        db.add_message("desktop", "user", "Squats on Friday", {"section": "workout"})
        db.add_message("desktop", "user", "Lamp at ten", {"section": "diablo"})
        db.add_message("voice", "user", "No section here", None)
        gone = [{"id": "workout", "deleted": True}, {"id": "diablo", "deleted": False}]
        with mock.patch.object(sections, "all_sections", return_value=gone):
            body = self.page()
        self.assertEqual([m["text"] for m in body["messages"]], ["Lamp at ten", "No section here"])
        self.assertEqual(body["channels"], {"desktop": 1, "voice": 1})

    def test_limit_is_kept_in_range(self):
        for n in range(3):
            db.add_message("voice", "user", f"line {n}", None)
        self.assertEqual(self.texts(limit=0), ["line 2"])
        self.assertEqual(len(self.texts(limit=5000)), 3)


if __name__ == "__main__":
    unittest.main()
