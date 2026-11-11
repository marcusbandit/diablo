import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import db, devices, server, standby

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


class TestSearch(unittest.TestCase):
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

    def get(self, query, **extra):
        params = {"q": query, **extra}
        response = self.client.get("/v1/search", params=params, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        return [m["text"] for m in response.json()["messages"]]

    def test_every_word_must_match_in_any_case_newest_first(self):
        db.add_message("telegram", "user", "Dim the desk lamp at ten", {"threads": ["a"]})
        db.add_message("desktop", "assistant", "The DESK lamp dims at 22:00", {"section": "diablo", "threads": ["a"]})
        db.add_message("telegram", "user", "Desk chair is broken", None)
        self.assertEqual(self.get("desk lamp"), ["The DESK lamp dims at 22:00", "Dim the desk lamp at ten"])
        self.assertEqual(self.get("LAMP"), ["The DESK lamp dims at 22:00", "Dim the desk lamp at ten"])

    def test_letters_outside_ascii_fold_too(self):
        db.add_message("telegram", "user", "Rødgrød med fløde", None)
        self.assertEqual(self.get("RØDGRØD"), ["Rødgrød med fløde"])

    def test_like_wildcards_are_plain_text(self):
        db.add_message("telegram", "user", "100% done", None)
        db.add_message("telegram", "user", "100 things", None)
        self.assertEqual(self.get("100%"), ["100% done"])
        self.assertEqual(self.get("_"), [])

    def test_other_sections_stay_out(self):
        db.add_message("desktop", "user", "Legs hurt from squats", {"section": "workout"})
        db.add_message("telegram", "user", "Squats on Friday", None)
        self.assertEqual(self.get("squats"), ["Squats on Friday"])

    def test_one_thread_and_a_limit(self):
        for n in range(5):
            db.add_message("telegram", "user", f"lights {n}", {"threads": ["a" if n % 2 else "b"]})
        self.assertEqual(self.get("lights", thread="a"), ["lights 3", "lights 1"])
        self.assertEqual(self.get("lights", limit=2), ["lights 4", "lights 3"])

    def test_an_empty_query_finds_nothing_and_threads_ride_along(self):
        db.add_message("telegram", "user", "anything", {"threads": ["a"]})
        self.assertEqual(self.get("   "), [])
        response = self.client.get("/v1/search", params={"q": "anything"}, headers=HEADERS).json()
        self.assertEqual(response["messages"][0]["threads"], ["a"])

    def test_the_token_is_needed(self):
        self.assertEqual(self.client.get("/v1/search", params={"q": "x"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
