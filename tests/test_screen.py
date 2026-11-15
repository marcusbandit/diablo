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

    def test_a_list_goes_to_the_central_card_as_clean_sections(self):
        sections = [{"title": " Clothes ", "items": ["Socks", {"text": "Hoodie", "done": True}, "  ", 7]},
                    {"title": "Empty", "items": []}, "junk", {"items": [{"text": "Untitled item"}]}]
        with mock.patch.object(server, "publish") as published:
            reply = self.client.post("/v1/screen", headers=HEADERS, json={"title": "Packing list", "sections": sections})
        self.assertEqual(reply.status_code, 200)
        event = published.call_args.args[0]
        self.assertEqual(event["size"], "large")
        self.assertEqual(event["card"]["sections"], [
            {"title": "Clothes", "items": [{"text": "Socks", "done": False}, {"text": "Hoodie", "done": True}, {"text": "7", "done": False}]},
            {"title": "", "items": [{"text": "Untitled item", "done": False}]},
        ])
        self.assertEqual(event["card"]["ttl"], 60.0)

    def test_a_list_alone_is_enough_and_never_runs_past_the_cap(self):
        many = [{"title": f"S{n}", "items": [f"item {i}" for i in range(40)]} for n in range(10)]
        with mock.patch.object(server, "publish") as published:
            self.assertEqual(self.client.post("/v1/screen", headers=HEADERS, json={"sections": many}).status_code, 200)
        sections = published.call_args.args[0]["card"]["sections"]
        self.assertEqual(sum(len(x["items"]) for x in sections), server.SCREEN_ITEMS)
        self.assertEqual(self.client.post("/v1/screen", headers=HEADERS, json={"sections": [{"items": [" "]}]}).status_code, 400)

    def test_odd_item_shapes_never_spill_into_letters(self):
        sections = [{"title": "A", "items": "Socks"}, {"title": "B", "items": {"x": 1}}, {"title": "C", "items": [0, None, "x"]}]
        with mock.patch.object(server, "publish") as published:
            self.client.post("/v1/screen", headers=HEADERS, json={"sections": sections})
        self.assertEqual(published.call_args.args[0]["card"]["sections"], [
            {"title": "A", "items": [{"text": "Socks", "done": False}]},
            {"title": "C", "items": [{"text": "0", "done": False}, {"text": "x", "done": False}]},
        ])

    def test_a_list_and_a_page_together_are_refused(self):
        body = {"sections": [{"items": ["a"]}], "html": "<p>a</p>"}
        reply = self.client.post("/v1/screen", headers=HEADERS, json=body)
        self.assertEqual(reply.status_code, 400)
        self.assertIn("sections", reply.json()["detail"])

    def test_only_the_hub_token_may_show(self):
        self.assertEqual(self.client.post("/v1/screen", json={"title": "x"}).status_code, 403)

    def test_opening_a_settings_page_goes_out_as_a_screen_event(self):
        for page in ("settings", "voice", "history"):
            with mock.patch.object(server, "publish") as published:
                reply = self.client.post("/v1/screen/open", headers=HEADERS, json={"page": page})
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(published.call_args.args[0], {"kind": "screen", "open": page})

    def test_opening_an_unknown_page_or_without_the_hub_token_is_refused(self):
        self.assertEqual(self.client.post("/v1/screen/open", headers=HEADERS, json={"page": "secrets"}).status_code, 400)
        self.assertEqual(self.client.post("/v1/screen/open", json={"page": "voice"}).status_code, 403)


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

    def test_open_puts_the_central_card_on_a_settings_page(self):
        busy.show({"card": {"id": "big", "title": "Atom"}, "size": "large"}, now=5)
        self.assertEqual(busy.show({"open": "voice"}, now=6), "voice")
        panel = self.read("panel.json")
        self.assertEqual((panel["open"], panel["view"], panel["page"], panel["page_at"]), (True, "settings", "voice", 6))
        self.assertEqual(panel["card"]["title"], "Atom")
        busy.show({"open": "settings"}, now=7)
        self.assertEqual(self.read("panel.json")["page"], "settings")
        busy.show({"card": {"id": "b2", "title": "Next"}, "size": "large"}, now=8)
        self.assertEqual((self.read("panel.json")["view"], self.read("panel.json")["page"]), ("now", ""))

    def test_open_history_puts_the_central_card_on_history(self):
        self.assertEqual(busy.show({"open": "history"}, now=9), "history")
        panel = self.read("panel.json")
        self.assertEqual((panel["open"], panel["view"], panel["page"]), (True, "history", ""))

    def test_an_unknown_page_writes_nothing(self):
        self.assertIsNone(busy.show({"open": "../x"}, now=5))
        self.assertFalse((Path(self.tmp.name) / "panel.json").exists())

    def test_a_list_always_opens_the_central_card(self):
        sections = [{"title": "Clothes", "items": [{"text": "Socks", "done": False}]}]
        self.assertEqual(busy.show({"card": {"id": "l1", "title": "Packing", "sections": sections}, "size": "small"}, now=5), "large")
        self.assertEqual(self.read("panel.json")["card"]["sections"], sections)

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

    def test_the_tool_passes_a_list_on_and_says_where_it_went(self):
        reply = mock.Mock(status_code=200)
        sections = [{"title": "Clothes", "items": [{"text": "Socks"}]}]
        with mock.patch.object(mcp_server, "get_config", return_value={"hub_token": "t"}), \
                mock.patch.object(mcp_server.httpx, "post", return_value=reply) as post:
            text = mcp_server.call("show_on_screen", {"title": "Packing", "sections": sections})
        self.assertIn("central card", text)
        self.assertEqual(post.call_args.kwargs["json"], {"title": "Packing", "sections": sections})
        tool = next(t for t in mcp_server.EXTRA if t["name"] == "show_on_screen")
        self.assertIn("sections", tool["inputSchema"]["properties"])
        self.assertIn("never in html", tool["description"])

    def test_the_tool_is_listed_and_allowed_while_locked(self):
        from hub import lockguard

        for name in ("show_on_screen", "open_settings"):
            self.assertIn(name, [t["name"] for t in mcp_server.EXTRA])
            self.assertIn(name, lockguard.SAFE_HOUSE)

    def test_open_settings_posts_the_page(self):
        reply = mock.Mock(status_code=200)
        with mock.patch.object(mcp_server, "get_config", return_value={"hub_token": "t"}), \
                mock.patch.object(mcp_server.httpx, "post", return_value=reply) as post:
            self.assertIn("Your voice", mcp_server.call("open_settings", {"page": "voice"}))
            self.assertEqual(post.call_args.kwargs["json"], {"page": "voice"})
            self.assertTrue(post.call_args.args[0].endswith("/v1/screen/open"))
            mcp_server.call("open_settings", {})
            self.assertEqual(post.call_args.kwargs["json"], {"page": "settings"})
            self.assertIn("History", mcp_server.call("open_settings", {"page": "history"}))
            self.assertEqual(post.call_args.kwargs["json"], {"page": "history"})

    def test_the_charter_says_when_to_open_settings(self):
        from hub import charter

        self.assertIn("open_settings with page voice", charter.CHARTER)
        self.assertIn("open_settings with page history", charter.CHARTER)


class TestHubPair(unittest.TestCase):
    def test_the_hub_pair_goes_out_before_each_connection(self):
        said = []
        cfg = {"hub_url": "http://100.64.0.9:8755", "hub_token": "pair-token"}
        with mock.patch.object(busy, "config", return_value=cfg), mock.patch.object(busy, "say", side_effect=said.append), \
                mock.patch.object(busy, "follow", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                busy.main()
        self.assertEqual(said, [{"hub": {"url": "http://100.64.0.9:8755", "token": "pair-token"}}])


if __name__ == "__main__":
    unittest.main()


class TestDesktopSideIsCareful(TestDesktopSide):
    def test_a_strange_id_never_leaves_pages(self):
        busy.show({"card": {"id": "../../x", "html": "<b>hi</b>", "ttl": 60}, "size": "small"}, now=1000)
        card = self.read("presence.json")["cards"][0]
        self.assertEqual(Path(card["page"]).parent, Path(self.tmp.name) / "pages")

    def test_other_keys_and_odd_stamps_survive(self):
        (Path(self.tmp.name) / "presence.json").write_text(json.dumps(
            {"cards": [{"id": "ears", "at": "soon", "ttl": None}], "extra": 1}))
        busy.show({"card": {"id": "new", "ttl": 60}, "size": "small"}, now=1000)
        body = self.read("presence.json")
        self.assertEqual(body["extra"], 1)
        self.assertEqual([c["id"] for c in body["cards"]], ["ears", "new"])
