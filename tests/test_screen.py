import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import mcp_server, server
from tests.test_hub_api import HEADERS, ApiCase

_spec = importlib.util.spec_from_file_location("presence_busy", Path(__file__).resolve().parent.parent / "presence" / "busy.py")
busy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(busy)


class TestEndpoint(ApiCase):
    def test_a_card_goes_out_as_a_screen_event(self):
        with mock.patch.object(server, "publish") as published:
            reply = self.client.post("/v1/screen", headers=HEADERS, json={"title": "Weather", "big": "7°", "lines": ["cloudy"]})
        self.assertEqual(reply.status_code, 200)
        event = published.call_args.args[0]
        self.assertEqual((event["kind"], event["size"]), ("screen", "small"))
        self.assertEqual(event["card"]["title"], "Weather")
        self.assertEqual(event["card"]["id"], reply.json()["id"])
        self.assertNotIn("size", event["card"])

    def test_large_and_html_pass_through(self):
        with mock.patch.object(server, "publish") as published:
            self.client.post("/v1/screen", headers=HEADERS, json={"title": "Atom", "html": "<h1>hi</h1>", "size": "large"})
        event = published.call_args.args[0]
        self.assertEqual(event["size"], "large")
        self.assertEqual(event["card"]["html"], "<h1>hi</h1>")
        self.assertEqual(event["card"]["ttl"], 60.0)

    def test_empty_or_huge_is_refused(self):
        self.assertEqual(self.client.post("/v1/screen", headers=HEADERS, json={}).status_code, 400)
        huge = "x" * (600 * 1024)
        self.assertEqual(self.client.post("/v1/screen", headers=HEADERS, json={"html": huge}).status_code, 400)

    def test_only_the_hub_token_may_show(self):
        self.assertEqual(self.client.post("/v1/screen", json={"title": "x"}).status_code, 403)


class TestDesktopSide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(busy, "STATE", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, name):
        return json.loads((Path(self.tmp.name) / name).read_text())

    def test_a_small_card_joins_the_others(self):
        for i in range(5):
            busy.show({"card": {"id": f"c{i}", "title": str(i), "ttl": 60}, "size": "small"}, now=1000)
        cards = self.read("presence.json")["cards"]
        self.assertEqual([c["id"] for c in cards], ["c2", "c3", "c4"])
        self.assertEqual(cards[-1]["at"], 1000)

    def test_expired_cards_are_dropped(self):
        busy.show({"card": {"id": "old", "ttl": 5}, "size": "small"}, now=1000)
        busy.show({"card": {"id": "new", "ttl": 5}, "size": "small"}, now=2000)
        self.assertEqual([c["id"] for c in self.read("presence.json")["cards"]], ["new"])

    def test_large_opens_the_central_card(self):
        self.assertEqual(busy.show({"card": {"id": "big", "title": "Atom"}, "size": "large"}, now=5), "large")
        panel = self.read("panel.json")
        self.assertEqual((panel["open"], panel["view"], panel["card"]["title"]), (True, "now", "Atom"))

    def test_a_page_is_saved_and_referenced(self):
        busy.show({"card": {"id": "p1", "html": "<b>3d</b>", "ttl": 60}, "size": "small"}, now=1000)
        card = self.read("presence.json")["cards"][0]
        self.assertNotIn("html", card)
        self.assertEqual(Path(card["page"]).read_text(), "<b>3d</b>")


class TestTool(unittest.TestCase):
    def test_the_tool_posts_to_the_hub(self):
        reply = mock.Mock(status_code=200)
        with mock.patch.object(mcp_server, "get_config", return_value={"hub_token": "t"}), \
                mock.patch.object(mcp_server.httpx, "post", return_value=reply) as post:
            text = mcp_server.call("show_on_screen", {"title": "Weather", "lines": "cloudy", "size": "large"})
        self.assertIn("central card", text)
        self.assertEqual(post.call_args.kwargs["json"], {"title": "Weather", "lines": ["cloudy"], "size": "large"})
        self.assertTrue(post.call_args.args[0].endswith("/v1/screen"))

    def test_the_tool_is_listed_and_allowed_while_locked(self):
        from hub import lockguard

        self.assertIn("show_on_screen", [t["name"] for t in mcp_server.EXTRA])
        self.assertIn("show_on_screen", lockguard.SAFE_HOUSE)


if __name__ == "__main__":
    unittest.main()
