import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, charter, db, devices, server

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


class StreamCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def stream(self, first, final, source="voice", stream=True):
        async def ask(conv, text, **kwargs):
            on_text = kwargs.get("on_text")
            if on_text and first:
                await on_text(first)
            return brains.Turn(text=final, backend="claude", first_text=first)

        with mock.patch.object(server.brains, "ask", side_effect=ask) as asked, mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS,
                                        json={"text": "launch it", "source": source, "stream": stream})
        return response, asked

    def test_the_first_line_streams_before_the_answer_and_the_chat_keeps_both(self):
        response, _ = self.stream("On it, launching that.", "Done, the thread is running.")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ndjson", response.headers["content-type"])
        lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        self.assertEqual(lines[0], {"say": "On it, launching that."})
        done = lines[-1]["done"]
        self.assertEqual(done["spoken_first"], "On it, launching that.")
        self.assertEqual(done["rest"], "Done, the thread is running.")
        self.assertEqual(done["reply"], "On it, launching that.\n\nDone, the thread is running.")
        self.assertEqual(db.get_message(done["id"])["text"], done["reply"])

    def test_an_answer_that_was_the_first_line_is_not_said_twice(self):
        response, _ = self.stream("It is half past nine.", "It is half past nine.")
        done = [json.loads(line) for line in response.text.splitlines() if line.strip()][-1]["done"]
        self.assertEqual(done["rest"], "")
        self.assertEqual(done["reply"], "It is half past nine.")

    def test_only_spoken_turns_get_the_early_line(self):
        response, asked = self.stream("On it.", "Done.", source="desktop", stream=False)
        self.assertNotIn("on_text", asked.call_args.kwargs)
        self.assertEqual(response.json()["reply"], "Done.")
        self.assertNotIn("spoken_first", response.json())

    def test_a_refused_request_comes_back_as_an_error_line(self):
        with mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS,
                                        json={"text": "x", "source": "voice", "section": "nope", "stream": True})
        self.assertEqual(json.loads(response.text.splitlines()[-1]), {"error": {"status": 404, "detail": "no such section"}})


class TestSplit(unittest.TestCase):
    def test_split(self):
        self.assertEqual(server.spoken_split("", "Done."), ("Done.", "Done."))
        self.assertEqual(server.spoken_split("On it.", ""), ("On it.", ""))
        self.assertEqual(server.spoken_split("On it.", "On it. Done."), ("On it. Done.", "Done."))
        self.assertEqual(server.spoken_split("On it.", "Done."), ("On it.\n\nDone.", "Done."))


class FakeOut:
    def __init__(self, events):
        self.lines = [(json.dumps(e) + "\n").encode() for e in events]

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""


class TestReader(unittest.TestCase):
    def test_the_first_text_block_is_handed_over_once_with_its_time(self):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking the weather."}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch", "input": {"query": "weather"}}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Sunny."}]}},
            {"type": "result", "result": "Sunny."},
        ]
        session = brains.ClaudeSession("test")
        session.proc = mock.Mock(stdout=FakeOut(events))
        result, heard = brains.Turn(), []

        async def on_text(text):
            heard.append((text, list(result.steps)))

        with mock.patch.object(brains.usage, "record_claude", return_value={}):
            asyncio.run(session._read(result, None, on_text))
        self.assertEqual(heard, [("Checking the weather.", [])])
        self.assertEqual(result.first_text, "Checking the weather.")
        self.assertGreater(result.first_text_at, 0)
        self.assertEqual(result.text, "Sunny.")


class TestCharter(unittest.TestCase):
    def test_the_charter_asks_for_a_first_line_on_spoken_turns(self):
        text = charter.charter()
        self.assertIn("decide first, say it, then do it", text)
        self.assertNotIn("—", text)
        self.assertNotIn("–", text)


if __name__ == "__main__":
    unittest.main()
