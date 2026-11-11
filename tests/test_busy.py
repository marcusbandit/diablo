"""The hub's running turns for the orb beside the cursor: hub/busy.py, GET /v1/busy, presence/busy.py."""

import asyncio
import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hub import brains, busy, events

spec = importlib.util.spec_from_file_location("presence_busy", ROOT / "presence" / "busy.py")
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(json.loads(json.dumps(event)))


class BusyCase(unittest.TestCase):
    def setUp(self):
        busy.running.clear()
        self.sent = Recorder()
        patch = mock.patch.object(events, "publish", self.sent)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(busy.running.clear)

    def busy_events(self):
        return [e for e in self.sent.events if e.get("kind") == "busy"]


class TestWords(unittest.TestCase):
    def test_steps_read_as_what_he_is_doing(self):
        self.assertEqual(busy.words("WebSearch: weather lyngby"), "searching the web")
        self.assertEqual(busy.words("t3_thread_launch on banditbox: fix the orb"), "launching on banditbox")
        self.assertEqual(busy.words("create_threads on nemurenai"), "launching on nemurenai")
        self.assertEqual(busy.words("t3_thread_read on banditbox: abc"), "T3 on banditbox")
        self.assertEqual(busy.words("hue_light: desk off"), "the lights")
        self.assertEqual(busy.words("Bash: ssh -p 22 bandit@nemurenai uptime"), "on nemurenai")
        self.assertEqual(busy.words("Bash: ls"), "running a command")
        self.assertEqual(busy.words("mail_search: invoices"), "checking mail")
        self.assertEqual(busy.words("some_new_tool"), "some new tool")
        self.assertEqual(busy.words("device_screenshot on banditbox"), "device screenshot")


class TestRunning(BusyCase):
    def test_a_turn_is_busy_from_start_to_end_with_its_steps(self):
        key = busy.start("main", "banditbox desktop app")
        busy.step(key, "WebSearch: weather")
        self.assertEqual(busy.snapshot()["turns"][0]["words"], "searching the web")
        busy.end(key)
        shots = self.busy_events()
        self.assertEqual([[t["words"] for t in e["turns"]] for e in shots], [["thinking"], ["searching the web"], []])
        self.assertEqual(shots[0]["turns"][0]["origin"], "banditbox desktop app")

    def test_a_turn_older_than_the_cap_is_left_out(self):
        key = busy.start("main", "voice")
        busy.running[key]["since"] = time.time() - busy.MAX_AGE_S - 1
        self.assertEqual(busy.snapshot()["turns"], [])
        self.assertEqual(busy.running, {})

    def test_a_step_after_the_end_is_ignored(self):
        key = busy.start("main", "voice")
        busy.end(key)
        busy.step(key, "Read: x")
        busy.end(key)
        self.assertEqual(len(self.busy_events()), 2)


class TestAsk(BusyCase):
    def test_every_turn_through_ask_is_shown_and_its_steps_reach_both(self):
        seen = []

        async def fake(conv, text, origin, images, on_step, **kwargs):
            seen.append([t["words"] for t in busy.snapshot()["turns"]])
            await on_step("WebFetch: https://example.com")
            await on_step("WebFetch: https://example.org")
            seen.append([t["words"] for t in busy.snapshot()["turns"]])
            return brains.Turn(text="ok")

        caller_steps = []

        async def mine(label):
            caller_steps.append(label)

        async def go():
            with mock.patch.object(brains, "_ask", fake):
                await brains.ask("main", "hi", origin="t3 watcher", machine=True)
                await brains.ask("main", "hi", origin="voice", on_step=mine)

        asyncio.run(go())
        self.assertEqual(seen, [["thinking"], ["reading the web"]] * 2)
        self.assertEqual(caller_steps, ["WebFetch: https://example.com", "WebFetch: https://example.org"])
        self.assertEqual([[t["words"] for t in e["turns"]] for e in self.busy_events()],
                         [["thinking"], ["reading the web"], []] * 2)
        self.assertEqual(busy.snapshot()["turns"], [])

    def test_a_failing_turn_still_ends(self):
        async def broken(conv, text, *args, **kwargs):
            raise RuntimeError("boom")

        async def go():
            with mock.patch.object(brains, "_ask", broken):
                with self.assertRaises(RuntimeError):
                    await brains.ask("main", "hi", origin="voice")

        asyncio.run(go())
        self.assertEqual(busy.snapshot()["turns"], [])
        self.assertEqual(self.busy_events()[-1]["turns"], [])


class TestEndpoint(BusyCase):
    def test_get_busy_needs_a_token_and_returns_the_snapshot(self):
        from fastapi.testclient import TestClient

        from hub import devices, server

        client = TestClient(server.app)
        self.assertEqual(client.get("/v1/busy").status_code, 403)
        key = busy.start("main", "voice")
        busy.step(key, "Grep: orb")
        with mock.patch.object(devices, "authorized", return_value={"name": "test"}):
            got = client.get("/v1/busy", headers={"X-Diablo-Token": "x"}).json()
        self.assertEqual(got["kind"], "busy")
        self.assertEqual([t["words"] for t in got["turns"]], ["searching files"])


class TestFeed(unittest.TestCase):
    def test_the_feed_keeps_only_what_the_orb_needs(self):
        event = {"kind": "busy", "turns": [{"id": 3, "conv": "main", "origin": "voice", "since": 5.0, "words": "the lights", "step": "hue_light: x"},
                                           "junk"]}
        self.assertEqual(feed.shown(event), {"turns": [{"words": "the lights", "origin": "voice", "since": 5.0}]})
        self.assertEqual(feed.shown({"kind": "busy"}), {"turns": []})


if __name__ == "__main__":
    unittest.main()
