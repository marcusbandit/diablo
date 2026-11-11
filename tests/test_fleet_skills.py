import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from voice import skills


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)


class TestPhoneAndFleetSkills(unittest.TestCase):
    def test_phone_status_is_a_tell_and_survives_no_report(self):
        with mock.patch("voice.skills.httpx.get", return_value=FakeResponse({"report": None, "age": None})) as get:
            kind, ok, text = skills.run("phone_status", {}, {})
        self.assertEqual((kind, ok), ("tell", True))
        self.assertEqual(text, "no phone report yet, the phone has not checked in with the fleet viewer")
        self.assertIsNone(get.call_args.kwargs["params"])

    def test_fresh_asks_the_phone(self):
        payload = {"report": {"device": "Pixel 9", "battery": {"level": 80}}, "age": 2, "fresh": True}
        with mock.patch("voice.skills.httpx.get", return_value=FakeResponse(payload)) as get:
            kind, ok, text = skills.run("phone_status", {"fresh": True}, {})
        self.assertTrue(ok)
        self.assertTrue(text.startswith("Pixel 9, report 2 s old; battery 80%"))
        self.assertEqual(get.call_args.kwargs["params"], {"fresh": 1, "wait": 8})

    def test_unreachable_viewer_fails_cleanly(self):
        with mock.patch("voice.skills.httpx.get", side_effect=httpx.ConnectError("refused")):
            kind, ok, text = skills.run("phone_status", {}, {})
        self.assertEqual((kind, ok), ("tell", False))
        self.assertIn("did not answer", text)

    def test_power_is_an_act_with_a_plain_line(self):
        with mock.patch("voice.skills.httpx.post", return_value=FakeResponse({"ok": True, "detail": "magic packet sent"})) as post:
            kind, ok, text = skills.run("fleet_power", {"host": "banditbox", "action": "wake"}, {})
        self.assertEqual((kind, ok), ("act", True))
        self.assertEqual(text, "wake sent to banditbox: magic packet sent")
        self.assertEqual(post.call_args.kwargs["json"], {"host": "banditbox", "action": "wake"})

    def test_power_refuses_nonsense_before_asking(self):
        with mock.patch("voice.skills.httpx.post") as post:
            self.assertFalse(skills.run("fleet_power", {"host": "banditbox", "action": "explode"}, {})[1])
            self.assertFalse(skills.run("fleet_power", {"action": "wake"}, {})[1])
            post.assert_not_called()

    def test_where_tolerates_a_missing_seat(self):
        state = {"devices": [{"name": "banditbox", "online": True, "self": True}, {"name": "homehub", "online": True}]}

        def get(url, **kwargs):
            if url.endswith("/api/seat"):
                return FakeResponse({"detail": "not found"}, status_code=404)
            return FakeResponse(state)

        with mock.patch("voice.skills.httpx.get", side_effect=get):
            kind, ok, text = skills.run("fleet_where", {}, {})
        self.assertEqual((kind, ok), ("tell", True))
        self.assertEqual(
            text,
            "The fleet viewer does not report where Diablo attends. banditbox is this very machine. Online: homehub.",
        )


if __name__ == "__main__":
    unittest.main()
