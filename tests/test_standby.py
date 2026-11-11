import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi.testclient import TestClient

from hub import brains, db, devices, sections, server, standby

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
PRIMARY = "http://pi.test:8755"
STANDBY_CONFIG = {"hub_token": TOKEN, "standby": {"primary": PRIMARY}}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "diablo.db")
        db.init(self.path)
        db.set_setting("brain.backend", "api")
        self.config = {"hub_token": TOKEN}
        for module in (server, standby, devices):
            patch = mock.patch.object(module, "get_config", side_effect=lambda: self.config)
            patch.start()
            self.addCleanup(patch.stop)
        standby._state = {"state": "mirror"}
        standby._probe.update(fails=0, oks=0, last_ok=None)
        self.client = TestClient(server.app)


class TestPrimary(Case):
    def test_health_says_primary_and_status_is_open(self):
        health = self.client.get("/v1/health").json()
        self.assertEqual((health["ok"], health["role"]), (True, "primary"))
        self.assertIn(health["stt"], ("", "local", "cloud"))
        self.assertEqual(self.client.get("/v1/standby/status").json(), {"role": "primary", "state": "primary"})

    def test_snapshot_is_a_real_database_and_answers_304_when_unchanged(self):
        db.add_message("phone", "user", "hello", {"section": "diablo"})
        response = self.client.get("/v1/standby/snapshot", headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        copy = Path(self.tmp.name) / "copy.db"
        copy.write_bytes(response.content)
        conn = sqlite3.connect(copy)
        self.assertEqual(conn.execute("SELECT text FROM messages").fetchall(), [("hello",)])
        conn.close()
        again = self.client.get("/v1/standby/snapshot", headers={**HEADERS, "If-None-Match": response.headers["etag"]})
        self.assertEqual(again.status_code, 304)

    def test_snapshot_needs_the_token(self):
        self.assertEqual(self.client.get("/v1/standby/snapshot").status_code, 403)

    def test_replay_keeps_ids_and_times_and_can_repeat(self):
        db.add_message("telegram", "user", "before", None)
        body = {
            "host": "nemurenai",
            "messages": [
                {"id": 2000000, "ts": 100.0, "source": "phone", "role": "user", "text": "lights?",
                 "meta": {"section": "diablo", "client_id": "a"}},
                {"id": 2000001, "ts": 101.0, "source": "phone", "role": "assistant", "text": "On, sir.",
                 "meta": {"section": "diablo", "client_id": "a"}},
            ],
            "facts": [{"ts": 102.0, "text": "likes tea", "tags": ["home"]}],
            "sections": [],
        }
        first = self.client.post("/v1/standby/replay", headers=HEADERS, json=body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["messages"], 2)
        second = self.client.post("/v1/standby/replay", headers=HEADERS, json=body)
        self.assertEqual(second.json()["messages"], 0)
        rows = db.section_messages("diablo", 0, 10, server.MAIN_SOURCES)
        self.assertEqual([(m["id"], m["ts"]) for m in rows[-2:]], [(2000000, 100.0), (2000001, 101.0)])
        self.assertEqual(len(db.get_facts()), 1)
        self.assertGreater(db.add_message("telegram", "user", "after", None), 2000001)
        note = brains.take_handover(brains.MAIN)
        self.assertIn("nemurenai", note)
        self.assertIn("he: lights?", note)
        self.assertIn("you: On, sir.", note)
        self.assertIsNone(brains.take_handover(brains.MAIN))

    def test_replay_does_not_overwrite_a_different_row_with_the_same_id(self):
        taken = db.add_message("telegram", "user", "mine", None)
        body = {"host": "n", "messages": [{"id": taken, "ts": 1.0, "source": "phone", "role": "user", "text": "theirs"}]}
        self.assertEqual(self.client.post("/v1/standby/replay", headers=HEADERS, json=body).json()["messages"], 1)
        texts = [m["text"] for m in db.get_messages(10)]
        self.assertEqual(texts, ["mine", "theirs"])

    def test_a_repeated_replay_of_a_clashing_id_adds_it_once(self):
        taken = db.add_message("telegram", "user", "mine", None)
        body = {"host": "n", "messages": [{"id": taken, "ts": 1.0, "source": "phone", "role": "user", "text": "theirs"}]}
        self.client.post("/v1/standby/replay", headers=HEADERS, json=body)
        self.assertEqual(self.client.post("/v1/standby/replay", headers=HEADERS, json=body).json()["messages"], 0)
        self.assertEqual(len(db.get_messages(10)), 2)

    def test_replay_drops_the_unanswered_copy_the_pi_died_on(self):
        db.add_message("phone", "user", "lights?", {"section": "diablo", "client_id": "a"})
        body = {"host": "n", "messages": [
            {"id": 2000000, "ts": 5.0, "source": "phone", "role": "user", "text": "lights?",
             "meta": {"section": "diablo", "client_id": "a"}},
            {"id": 2000001, "ts": 6.0, "source": "phone", "role": "assistant", "text": "On.",
             "meta": {"section": "diablo", "client_id": "a"}},
        ]}
        self.client.post("/v1/standby/replay", headers=HEADERS, json=body)
        self.assertEqual([m["id"] for m in db.get_messages(10)], [2000000, 2000001])

    def test_replay_merges_sections_newest_wins(self):
        mine = dict(sections.find("workout"), name="Gym", updated=50.0)
        body = {"host": "n", "sections": [mine]}
        self.assertEqual(self.client.post("/v1/standby/replay", headers=HEADERS, json=body).status_code, 200)
        self.assertEqual(sections.find("workout")["name"], "Gym")

    def test_replay_rejects_bad_rows(self):
        body = {"host": "n", "messages": [{"id": "x", "ts": 1, "source": "phone", "role": "user", "text": "t"}]}
        self.assertEqual(self.client.post("/v1/standby/replay", headers=HEADERS, json=body).status_code, 400)


class TestGate(Case):
    def setUp(self):
        super().setUp()
        self.config = STANDBY_CONFIG

    def test_mirror_answers_nothing_but_status(self):
        self.assertEqual(self.client.get("/v1/health").status_code, 503)
        self.assertEqual(self.client.get("/v1/sections", headers=HEADERS).status_code, 503)
        self.assertEqual(self.client.post("/v1/chat", headers=HEADERS, json={"text": "hi"}).status_code, 503)
        self.assertEqual(self.client.get("/v1/standby/status").json()["state"], "mirror")

    def test_active_takes_phone_writes_only(self):
        standby._state = {"state": "active"}
        health = self.client.get("/v1/health").json()
        self.assertEqual((health["ok"], health["role"]), (True, "standby"))
        self.assertIn(health["stt"], ("", "local", "cloud"))
        self.assertEqual(self.client.get("/v1/sections", headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.post("/v1/facts", headers=HEADERS, json={"text": "x"}).status_code, 200)
        self.assertEqual(self.client.put("/v1/settings/a", headers=HEADERS, json={"value": "b"}).status_code, 503)
        self.assertEqual(self.client.put("/v1/brain", headers=HEADERS, json={}).status_code, 503)

    def test_an_unanswered_message_from_the_pi_does_not_block_the_resend(self):
        db.add_message("phone", "user", "hi", {"section": "diablo", "client_id": "a"})
        standby._state = {"state": "active", "first_message": 2_000_000, "first_fact": 1}
        db.start_messages_at(2_000_000)
        with mock.patch.object(server.brains, "ask", new=mock.AsyncMock(return_value=brains.Turn(backend="api", text="Hello."))):
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": "hi", "source": "phone", "client_id": "a"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], 2_000_000)
        self.assertEqual([m["id"] for m in db.get_messages(10)], [2_000_000, 2_000_001])

    def test_a_standby_refuses_replays(self):
        standby._state = {"state": "active"}
        self.assertEqual(self.client.post("/v1/standby/replay", headers=HEADERS, json={"host": "n"}).status_code, 503)


class TestHandover(Case):
    def test_a_failed_turn_gives_the_note_back(self):
        db.set_setting("handover.main", "recap")
        failed = brains.Turn(backend="api", error="boom")
        with mock.patch.object(brains, "run_api", new=mock.AsyncMock(return_value=failed)):
            asyncio.run(brains.ask(brains.MAIN, "hi", origin="phone app"))
        self.assertEqual(db.get_setting("handover.main"), "recap")
        worked = brains.Turn(backend="api", text="Hi.")
        with mock.patch.object(brains, "run_api", new=mock.AsyncMock(return_value=worked)):
            asyncio.run(brains.ask(brains.MAIN, "hi", origin="phone app"))
        self.assertEqual(db.get_setting("handover.main"), "")


class FakePi:
    def __init__(self, folder):
        self.up = True
        self.replays = []
        self.path = str(Path(folder) / "pi.db")
        conn = sqlite3.connect(self.path)
        conn.executescript(db._SCHEMA)
        conn.execute("INSERT INTO messages (ts, source, role, text, meta) VALUES (1, 'phone', 'user', 'from the pi', ?)",
                     (json.dumps({"section": "diablo"}),))
        conn.execute("INSERT INTO settings VALUES ('brain.session.claude.main', 'pi-session')")
        conn.commit()
        conn.close()

    def handle(self, request):
        if not self.up:
            raise httpx.ConnectError("down")
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/v1/standby/snapshot":
            return httpx.Response(200, content=Path(self.path).read_bytes(), headers={"ETag": '"1"'})
        if request.url.path == "/v1/standby/replay":
            self.replays.append(json.loads(request.content))
            return httpx.Response(200, json={"messages": 0, "facts": 0})
        return httpx.Response(404)


class TestFailover(Case):
    def setUp(self):
        super().setUp()
        self.config = STANDBY_CONFIG
        self.pi = FakePi(self.tmp.name)

    def step(self, now):
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.pi.handle)) as client:
                await standby.step(client, server.MAIN_SOURCES, now=now)
        asyncio.run(go())

    def test_a_full_outage_and_handover(self):
        self.step(1000)
        self.assertEqual([m["text"] for m in db.get_messages(10)], ["from the pi"])
        self.assertEqual(standby.state(), "mirror")
        self.pi.up = False
        for i in range(standby.FAIL_AFTER - 1):
            self.step(1010 + i)
            self.assertEqual(standby.state(), "mirror")
        self.step(1100)
        self.assertEqual(standby.state(), "active")
        self.assertEqual(db.get_setting("brain.session.claude.main"), "")
        self.assertIn("standby hub", db.get_setting("handover.main"))
        self.assertIn("from the pi", db.get_setting("handover.main"))
        written = db.add_message("phone", "user", "while down", {"section": "diablo"})
        self.assertEqual(written, 2_000_000)
        saved = json.loads((Path(self.tmp.name) / "standby.json").read_text())
        self.assertEqual(saved["first_message"], 2_000_000)
        self.pi.up = True
        self.step(1200)
        self.assertEqual(standby.state(), "active")
        self.step(1210)
        self.assertEqual(standby.state(), "mirror")
        self.assertEqual(len(self.pi.replays), 1)
        self.assertEqual([m["text"] for m in self.pi.replays[0]["messages"]], ["while down"])
        self.assertEqual(self.pi.replays[0]["messages"][0]["id"], 2_000_000)
        self.assertEqual([m["text"] for m in db.get_messages(10)], ["from the pi"])
        self.assertNotIn("first_message", standby._state)

    def test_never_activates_without_a_snapshot(self):
        self.pi.up = False
        for i in range(standby.FAIL_AFTER + 2):
            self.step(1000 + i)
        self.assertEqual(standby.state(), "mirror")

    def test_a_failed_replay_keeps_the_queue(self):
        self.step(1000)
        self.pi.up = False
        for i in range(standby.FAIL_AFTER):
            self.step(1010 + i)
        db.add_message("phone", "user", "queued", {"section": "diablo"})
        self.pi.up = True
        original = self.pi.handle

        def broken(request):
            if request.url.path == "/v1/standby/replay":
                return httpx.Response(500)
            return original(request)

        self.pi.handle = broken
        self.step(1100)
        with self.assertRaises(httpx.HTTPStatusError):
            self.step(1110)
        self.assertEqual(standby.state(), "draining")
        self.pi.handle = original
        self.step(1120)
        self.assertEqual(standby.state(), "mirror")
        self.assertEqual([m["text"] for m in self.pi.replays[0]["messages"]], ["queued"])


if __name__ == "__main__":
    unittest.main()
