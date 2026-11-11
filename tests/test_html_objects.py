import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import agent, brains, charter, db, devices, mcp_server, server

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
PAGE = "<h1>Fleet</h1><table><tr><td>banditbox</td><td>up</td></tr></table>"


def page(**extra):
    return {"kind": "html", "title": "Fleet", "html": PAGE, **extra}


class TestHtmlObjectRules(unittest.TestCase):
    def test_a_small_page_is_fine(self):
        self.assertEqual(agent.object_problem(page()), "")

    def test_other_kinds_are_never_judged(self):
        self.assertEqual(agent.object_problem({"kind": "readout", "cells": []}), "")

    def test_an_empty_page_is_refused(self):
        self.assertIn("needs the page", agent.object_problem({"kind": "html", "html": "  "}))
        self.assertIn("needs the page", agent.object_problem({"kind": "html"}))

    def test_a_page_over_the_limit_is_refused_with_its_size(self):
        big = page(html="x" * (agent.HTML_LIMIT + 1))
        problem = agent.object_problem(big)
        self.assertIn("524,289 bytes", problem)
        self.assertIn("limit is 524,288 bytes", problem)

    def test_the_limit_counts_bytes_not_characters(self):
        wide = page(html="æ" * (agent.HTML_LIMIT // 2 + 1))
        self.assertNotEqual(agent.object_problem(wide), "")

    def test_clean_object_tidies_title_and_height(self):
        self.assertEqual(agent.clean_object(page(height="320"))["height"], 320)
        self.assertEqual(agent.clean_object(page(height=99999))["height"], agent.HTML_HEIGHT_MAX)
        self.assertNotIn("height", agent.clean_object(page(height=-4)))
        self.assertNotIn("height", agent.clean_object(page(height="tall")))
        self.assertNotIn("height", agent.clean_object(page(height=float("inf"))))
        self.assertNotIn("height", agent.clean_object(page(height="Infinity")))
        self.assertNotIn("height", agent.clean_object(page(height=10 ** 400)))
        self.assertEqual(agent.clean_object(page(title="  Fleet  "))["title"], "Fleet")
        self.assertEqual(agent.clean_object({"kind": "html", "html": PAGE})["title"], "")

    def test_the_tool_schema_offers_the_html_kind(self):
        tool = next(t["function"] for t in agent.TOOLS if t["function"]["name"] == "emit_object")
        self.assertIn("html", tool["parameters"]["properties"]["kind"]["enum"])
        self.assertIn("height", tool["parameters"]["properties"])
        self.assertIn("512 KB", tool["description"])

    def test_the_charter_says_when_to_draw_a_page(self):
        self.assertIn("emit_object html page", charter.CHARTER)


class TestHtmlObjectPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))

    def test_the_house_tool_shows_a_page(self):
        ctx = {"workspace": "scratch"}
        self.assertIn("html object shown", agent._call_tool("emit_object", page(height=300), ctx))
        self.assertEqual(ctx["_objects"], [page(height=300)])

    def test_the_house_tool_refuses_an_oversized_page_with_an_error(self):
        ctx = {"workspace": "scratch"}
        with self.assertRaises(ValueError) as caught:
            agent._call_tool("emit_object", page(html="x" * (agent.HTML_LIMIT + 10)), ctx)
        self.assertIn("not shown", str(caught.exception))
        self.assertNotIn("_objects", ctx)

    def test_mcp_reports_an_oversized_page_as_a_tool_error(self):
        big = page(html="x" * (agent.HTML_LIMIT + 10))
        out = mcp_server.handle({"method": "tools/call", "params": {"name": "emit_object", "arguments": big}})
        self.assertTrue(out["isError"])
        self.assertIn("limit is 524,288 bytes", out["content"][0]["text"])

    def test_a_cli_turn_collects_a_page_and_drops_an_oversized_one(self):
        turn = brains.Turn()
        brains.collect_object(turn, "mcp__diablo__emit_object", page(height="200"))
        brains.collect_object(turn, "mcp__diablo__emit_object", page(html="x" * (agent.HTML_LIMIT + 10)))
        self.assertEqual(turn.objects, [page(height=200)])

    def test_the_api_agent_keeps_only_pages_within_the_limit(self):
        calls = [
            {"id": "1", "function": {"name": "emit_object", "arguments": json.dumps(page())}},
            {"id": "2", "function": {"name": "emit_object", "arguments": json.dumps(page(html="x" * (agent.HTML_LIMIT + 10)))}},
        ]
        answers = iter([{"role": "assistant", "content": "", "tool_calls": calls}, {"role": "assistant", "content": "Here."}])
        with mock.patch.object(agent.llm, "chat", side_effect=lambda *a, **k: next(answers)):
            reply, objects = agent.run_agent("fleet?", {}, None, {})
        self.assertEqual(reply, "Here.")
        self.assertEqual(objects, [page()])


class TestHtmlObjectIsStored(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def test_the_page_is_kept_with_the_reply_and_comes_back_in_history(self):
        turn = brains.Turn(text="The fleet, sir.", backend="claude", objects=[page(height=240)])
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=turn)), mock.patch.object(server, "publish"):
            body = self.client.post("/v1/chat", headers=HEADERS, json={"text": "fleet?", "source": "desktop", "client_id": "h1"}).json()
        self.assertEqual(body["objects"], [page(height=240)])
        stored = [row for row in db.get_messages(10) if row["role"] == "assistant"]
        self.assertEqual(stored[0]["meta"]["objects"], [page(height=240)])
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        again = [row for row in db.get_messages(10) if row["role"] == "assistant"]
        self.assertEqual(again[0]["meta"]["objects"][0]["html"], PAGE)


if __name__ == "__main__":
    unittest.main()
