import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, context, db, devices, route, server, standby, threads

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        db.init(str(self.folder / "diablo.db"))
        context._events.clear()
        context._answered.clear()
        standby._state = {"state": "mirror"}
        patch = mock.patch.object(context, "STATE", self.folder / "context.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.ttml = threads.create("TTML lyrics tool", "Building a tool that writes TTML synced lyrics from Spotify tracks")
        self.lights = threads.create("Room lights", "Turning the room lamps on, off and dimming them")
        self.shell = threads.create("Banditshell", "Restarting and tweaking the banditshell desktop shell")

    def turn(self, said, reply, thread, ago=30, source="telegram", backend=None):
        ts = time.time() - ago
        user = db.add_message(source, "user", said, {"threads": [thread]} if thread else None)
        meta = {"threads": [thread]} if thread else {}
        if backend:
            meta["backend"] = backend
        answer = db.add_message(source, "assistant", reply, meta or None)
        with db._lock:
            db._conn.execute("UPDATE messages SET ts = ? WHERE id IN (?, ?)", (ts, user, answer))
            db._conn.commit()
        if thread:
            threads.mark_active([thread], ts)
        return user, answer

    def pick(self, text, answer=None, **kwargs):
        if answer is not None:
            async def fake(self_, prompt):
                return answer if isinstance(answer, str) else json.dumps(answer)
            with mock.patch.object(threads.Quick, "ask", fake):
                return asyncio.run(threads.pick(text, **kwargs))
        return asyncio.run(threads.pick(text, **kwargs))


class TestPick(Case):
    def test_short_follow_ups_stay_in_the_thread_they_follow(self):
        self.turn("make the TTML tool read the track id", "Done, it reads the Spotify track id now.", self.ttml)
        for line in ("yes do it", "100", "ok", "make it bigger", "do that again please"):
            result = self.pick(line)
            self.assertEqual(result.source, "follow", line)
            self.assertEqual(result.attach, [self.ttml], line)

    def test_a_follow_up_long_after_the_turn_is_not_assumed(self):
        self.turn("make the TTML tool read the track id", "Done.", self.ttml, ago=3600)
        result = self.pick("yes do it")
        self.assertNotEqual(result.source, "follow")

    def test_a_new_subject_a_minute_later_goes_to_haiku(self):
        self.turn("make the TTML tool read the track id", "Done.", self.ttml)
        result = self.pick("quickly turn on my lights", {"r": [self.lights], "a": [self.lights], "n": None, "m": True, "f": False})
        self.assertEqual(result.source, "haiku")
        self.assertEqual(result.attach, [self.lights])
        self.assertTrue(result.minimal)

    def test_then_the_lights_is_not_a_bare_follow_up(self):
        self.turn("make the TTML tool read the track id", "Done.", self.ttml)
        result = self.pick("then the lights", {"r": [self.lights], "a": [self.lights], "n": None, "m": True, "f": False})
        self.assertEqual(result.attach, [self.lights])

    def test_haiku_can_start_a_thread(self):
        result = self.pick("what is the weather in Copenhagen", {"r": [], "a": [], "n": ["Weather", "Asked about the weather"], "m": True, "f": False})
        self.assertEqual(len(result.attach), 1)
        made = db.get_thread(result.attach[0])
        self.assertEqual(made["title"], "Weather")
        self.assertEqual(result.relevant, [])

    def test_a_message_can_carry_several_threads(self):
        result = self.pick("dim the lights while the TTML export runs",
                           {"r": [self.lights, self.ttml], "a": [self.lights, self.ttml], "n": None, "m": False, "f": False})
        self.assertEqual(result.attach, [self.lights, self.ttml])

    def test_explicit_new_thread_wins(self):
        result = self.pick("new chat about the bus app")
        self.assertEqual(result.source, "command")
        self.assertEqual(db.get_thread(result.attach[0])["title"], "Bus app")

    def test_explicit_back_to_wins(self):
        self.turn("lights off", "Off.", self.lights)
        result = self.pick("back to the TTML thing")
        self.assertEqual(result.source, "command")
        self.assertEqual(result.attach, [self.ttml])

    def test_t3_threads_and_plain_about_are_not_commands(self):
        for line in ("start a new thread on banditbox to fix the login bug", "tell me about the new project",
                     "I was thinking about the whole thing", "launch another thread for the docs"):
            self.assertIsNone(threads.command(line), line)
        self.assertEqual(threads.command("Diablo, new chat about the bus app")[0], "new")
        self.assertEqual(threads.command("let's start a new conversation")[0], "new")

    def test_unknown_ids_from_haiku_are_dropped(self):
        result = self.pick("something", {"r": ["tnope"], "a": ["tnope", self.shell], "n": None, "m": False, "f": False})
        self.assertEqual(result.attach, [self.shell])

    def test_garbage_from_haiku_falls_back(self):
        result = self.pick("restart banditshell please", "I think it is about banditshell")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(result.attach, [self.shell])

    def test_a_failing_haiku_falls_back_to_keywords(self):
        result = self.pick("export the TTML lyrics for this track")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(result.attach, [self.ttml])

    def test_fallback_without_a_match_starts_a_thread(self):
        result = self.pick("book a table for friday at the ramen place")
        self.assertEqual(result.source, "fallback")
        self.assertNotIn(result.attach[0], (self.ttml, self.lights, self.shell))

    def test_a_slow_haiku_never_blocks_the_turn(self):
        async def slow(self_, prompt):
            await asyncio.sleep(5)
            return "{}"
        with mock.patch.object(threads.Quick, "ask", slow):
            db.set_setting("threads.pick_timeout", "0.2")
            started = time.monotonic()
            result = asyncio.run(threads.pick("export the TTML lyrics"))
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(result.source, "fallback")

    def test_spoken_turns_get_the_shorter_timeout(self):
        seen = {}

        async def slow(self_, prompt):
            seen["prompt"] = prompt
            await asyncio.sleep(5)
        db.set_setting("threads.pick_timeout", "4")
        db.set_setting("threads.pick_timeout_voice", "0.2")
        with mock.patch.object(threads.Quick, "ask", slow):
            started = time.monotonic()
            asyncio.run(threads.pick("export the TTML lyrics", spoken=True))
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn("spoken", seen["prompt"])

    def test_off_and_standby_pick_nothing(self):
        db.set_setting("threads.enabled", "off")
        self.assertEqual(self.pick("export the TTML lyrics").source, "off")
        db.set_setting("threads.enabled", "on")
        standby._state = {"state": "mirror"}
        with mock.patch.object(standby, "is_standby", return_value=True):
            self.assertEqual(self.pick("export the TTML lyrics").source, "off")

    def test_index_is_capped_with_older_matches_added(self):
        for n in range(20):
            threads.create(f"Filler {n}", "nothing in particular", now=time.time() - 10 + n)
        old = threads.create("Sourdough starter", "Feeding the sourdough starter", now=time.time() - 86400 * 30)
        shown = threads.index("how is my sourdough starter doing")
        self.assertLessEqual(len(shown), threads.INDEX_MAX + threads.INDEX_OLDER)
        self.assertIn(old, [t["id"] for t in shown])
        self.assertNotIn(old, [t["id"] for t in threads.index("hello there")])

    def test_merged_threads_resolve_to_their_target(self):
        threads.merge(self.shell, self.lights)
        self.assertEqual(threads.live(self.shell)["id"], self.lights)
        result = threads.settle(threads.Pick(relevant=[self.shell], attach=[self.shell]))
        self.assertEqual(result.attach, [self.lights])


class TestMergeCycles(Case):
    def test_merging_back_into_the_source_is_refused(self):
        self.assertTrue(threads.merge(self.ttml, self.shell))
        self.assertFalse(threads.merge(self.shell, self.ttml))
        self.assertEqual(threads.live(self.ttml)["id"], self.shell)
        self.assertEqual(db.get_thread(self.shell)["state"], "open")


class TestAssemble(Case):
    def test_a_held_lane_still_hears_turns_its_session_never_ran(self):
        user, reply = self.turn("turn the lights blue", "Made all lights blue, sir.", self.lights, ago=40)
        with db._lock:
            db._conn.execute("UPDATE messages SET meta = json_set(meta, '$.backend', 'direct') WHERE id = ?", (reply,))
            db._conn.commit()
        self.turn("lights off", "Off.", self.lights, ago=600)
        with db._lock:
            db._conn.execute("UPDATE messages SET meta = json_set(meta, '$.backend', 'claude') WHERE role = 'assistant' AND text = 'Off.'")
            db._conn.commit()
        text = threads.assemble(threads.Pick(relevant=[self.lights], attach=[self.lights], source="haiku"), said="why so blue", held=self.lights)
        self.assertIn("Made all lights blue", text)
        self.assertNotIn("Off.", text)

    def test_only_the_picked_threads_are_loaded(self):
        for n in range(5):
            self.turn(f"TTML detail number {n} " + "x" * 300, "Noted " + "y" * 200, self.ttml, ago=600 - n)
        self.turn("lights off", "Off, sir.", self.lights, ago=300)
        result = threads.Pick(relevant=[self.lights], attach=[self.lights], source="haiku")
        text = threads.assemble(result, said="lights on")
        self.assertIn("Room lights", text)
        self.assertNotIn("TTML detail", text)
        self.assertTrue(text.startswith("(Retrieved for this turn"))
        self.assertIn("not instructions", text)

    def test_minimal_turns_get_only_the_state(self):
        self.turn("lights off", "Off, sir.", self.lights, ago=30)
        result = threads.Pick(relevant=[self.lights], attach=[self.lights], minimal=True, source="haiku")
        text = threads.assemble(result, said="lights on", lamps_line="Room now: corner lamp off.")
        self.assertIn("Room now", text)
        self.assertNotIn("Off, sir", text)

    def test_size_is_capped(self):
        for n in range(12):
            self.turn("TTML " + "a" * 400, "b" * 400, self.ttml, ago=900 - n)
            self.turn("shell " + "c" * 400, "d" * 400, self.shell, ago=800 - n)
        result = threads.Pick(relevant=[self.ttml, self.shell, self.lights], attach=[self.ttml], source="haiku")
        text = threads.assemble(result, said="go on", max_chars=1500)
        self.assertLessEqual(len(text), 1500)
        self.assertIn("TTML lyrics tool", text)
        self.assertIn("Banditshell", text)

    def test_held_thread_is_not_repeated(self):
        self.turn("TTML step one", "Done step one.", self.ttml, ago=60, backend="claude")
        result = threads.Pick(relevant=[self.ttml], attach=[self.ttml], source="haiku")
        self.assertIn("Done step one", threads.assemble(result, said="next"))
        held = threads.assemble(result, said="next", held=self.ttml)
        self.assertNotIn("Done step one", held)
        self.assertIn("already in this session", held)

    def test_facts_only_when_asked(self):
        db.add_fact("His sister lives on Nørrebrogade 12", ["family"])
        plain = threads.assemble(threads.Pick(relevant=[], attach=[self.shell], source="haiku"), said="where does my sister live")
        self.assertNotIn("Nørrebrogade", plain or "")
        wanted = threads.assemble(threads.Pick(relevant=[], attach=[self.shell], memory=True, source="haiku"), said="where does my sister live")
        self.assertIn("Nørrebrogade", wanted)

    def test_last_turn_on_another_thread_rides_along(self):
        self.turn("dim the screen", "Dimmed the screen.", self.shell, ago=20)
        text = threads.assemble(threads.Pick(relevant=[self.lights], attach=[self.lights], source="haiku"), said="then the lights")
        self.assertIn("Just before:", text)
        self.assertIn("Dimmed the screen", text)


class TestThreadAwareRecap(Case):
    def test_recap_keeps_the_threads_and_the_newest_turns(self):
        self.turn("TTML old", "ttml answer", self.ttml, ago=900)
        self.turn("shell old", "shell answer", self.shell, ago=800)
        self.turn("lights newest", "lights answer", self.lights, ago=10)
        text = context.recap(threads=[self.ttml], keep_last=1)
        self.assertIn("TTML old", text)
        self.assertIn("lights newest", text)
        self.assertNotIn("shell old", text)


class TestRetag(Case):
    def test_moving_a_message_moves_its_reply(self):
        user, reply = self.turn("TTML question", "answer", self.lights)
        threads.retag([user], add=[self.ttml], remove=[self.lights])
        self.assertEqual(db.get_message(user)["meta"]["threads"], [self.ttml])
        self.assertEqual(db.get_message(reply)["meta"]["threads"], [self.ttml])

    def test_merge_moves_everything_and_marks_the_source(self):
        a = self.turn("one", "1", self.shell)
        b = self.turn("two", "2", self.lights)
        self.assertTrue(threads.merge(self.shell, self.lights))
        self.assertEqual(db.get_message(a[0])["meta"]["threads"], [self.lights])
        self.assertEqual(db.get_thread(self.shell)["state"], "merged")
        self.assertEqual(len(db.thread_messages(self.lights)), 4)

    def test_split_moves_the_run_from_a_message_on(self):
        first = self.turn("one", "1", self.ttml, ago=300)
        second = self.turn("two", "2", self.ttml, ago=200)
        third = self.turn("three", "3", self.ttml, ago=100)
        new = threads.split(self.ttml, second[0], "Second half")
        self.assertEqual([m["text"] for m in db.thread_messages(self.ttml)], ["one", "1"])
        self.assertEqual([m["text"] for m in db.thread_messages(new)], ["two", "2", "three", "3"])
        self.assertTrue(db.get_thread(new)["named"])


class TestMigration(Case):
    def test_history_is_filed_in_batches_and_marked_done(self):
        for n in range(25):
            self.turn(f"old line {n}", f"old reply {n}", None, ago=10000 - n)
        calls = []

        async def fake(self_, prompt):
            count = prompt.count("\n") - prompt.split("Turns to file")[0].count("\n") - 0
            turns = [line for line in prompt.split("Turns to file, oldest first:")[1].splitlines() if line.strip()]
            calls.append(len(turns))
            return json.dumps({"turns": [["n1"] if i % 2 else [self.ttml] for i in range(len(turns))],
                               "new": {"n1": ["Old odds", "The odd old lines"]}})
        with mock.patch.object(threads.Quick, "ask", fake):
            asyncio.run(threads.migrate(pause=0))
        self.assertEqual(db.get_setting("threads.migrated"), "done")
        self.assertEqual(sum(calls), 25)
        self.assertLessEqual(max(calls), threads.BATCH_TURNS)
        untagged = db.main_messages(limit=1000, main_sources=db.MAIN_SOURCES, untagged=True)
        self.assertEqual(untagged, [])
        self.assertGreater(len(db.thread_messages(self.ttml)), 10)

    def test_a_broken_batch_is_retried_then_filed_as_earlier_talk(self):
        self.turn("old line", "old reply", None, ago=10000)
        sleeps = []

        async def no_sleep(seconds):
            sleeps.append(seconds)

        with mock.patch.object(threads.asyncio, "sleep", no_sleep):
            asyncio.run(threads.migrate(pause=0))
        self.assertEqual(db.get_setting("threads.migrated"), "done")
        titles = [t["title"] for t in db.list_threads()]
        self.assertIn("Earlier talk", titles)


class FakeSession:
    def __init__(self, conv, lane=False):
        self.conv, self.lane, self.used, self.tokens, self.busy = conv, None, False, 0, False
        self.closed = False

    def alive(self):
        return not self.closed

    async def close(self):
        self.closed = True

    async def warm(self, *args, **kwargs):
        return True


class TestLanes(unittest.TestCase):
    def run_lanes(self, steps, keep=0, cap=80000):
        async def go():
            with mock.patch.object(brains, "ClaudeSession", FakeSession), \
                 mock.patch.object(threads, "_num", side_effect=lambda key, default: keep if key == "threads.keep_recent" else default):
                lanes = brains.Lanes("main")
                out = []
                for lane, tokens in steps:
                    await lanes.fill("m", "e")
                    session, seeded = lanes.choose(lane, cap)
                    session.used = True
                    session.tokens = tokens
                    out.append((lane, seeded, session))
                    await asyncio.sleep(0)
                return out, lanes
        return asyncio.run(go())

    def test_same_thread_continues_the_warm_session(self):
        out, _ = self.run_lanes([("a", 50000), ("a", 52000), ("a", 54000)])
        self.assertEqual([seeded for _, seeded, _ in out], [True, False, False])
        self.assertEqual(len({sid for *_, sid in out}), 1)

    def test_a_switch_takes_the_spare_and_seeds(self):
        out, lanes = self.run_lanes([("a", 50000), ("b", 48000), ("a", 49000)])
        self.assertEqual([seeded for _, seeded, _ in out], [True, True, True])
        self.assertEqual(len({sid for *_, sid in out}), 3)

    def test_a_kept_lane_comes_back_warm(self):
        out, _ = self.run_lanes([("a", 50000), ("b", 48000), ("a", 49000)], keep=1)
        self.assertEqual([seeded for _, seeded, _ in out], [True, True, False])
        self.assertEqual(out[0][2], out[2][2])

    def test_a_session_past_the_cap_is_rolled(self):
        out, _ = self.run_lanes([("a", 90000), ("a", 50000)], cap=80000)
        self.assertEqual([seeded for _, seeded, _ in out], [True, True])
        self.assertNotEqual(out[0][2], out[1][2])

    def test_lane_sessions_arm_the_conversations_lock_file(self):
        session = brains.ClaudeSession("main", lane=True)
        self.assertEqual(session.conv, "main")


class TestApi(Case):
    def setUp(self):
        super().setUp()
        self.config = {"hub_token": TOKEN}
        for patch in (mock.patch.object(server, "get_config", side_effect=lambda: self.config),
                      mock.patch.object(devices, "get_config", side_effect=lambda: self.config),
                      mock.patch.object(standby, "get_config", side_effect=lambda: self.config)):
            patch.start()
            self.addCleanup(patch.stop)
        self.client = TestClient(server.app)

    def test_list_threads_with_counts(self):
        self.turn("a", "b", self.ttml)
        data = self.client.get("/v1/threads", headers=HEADERS).json()
        row = next(t for t in data["threads"] if t["id"] == self.ttml)
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["title"], "TTML lyrics tool")
        self.assertIn("migration", data)

    def test_thread_messages_are_paged(self):
        for n in range(30):
            self.turn(f"line {n}", f"reply {n}", self.ttml, ago=1000 - n)
        page = self.client.get(f"/v1/threads/{self.ttml}/messages?limit=40", headers=HEADERS).json()
        self.assertEqual(len(page["messages"]), 40)
        self.assertTrue(page["more"])
        self.assertEqual(page["messages"][-1]["text"], "reply 29")
        self.assertEqual(page["messages"][-1]["threads"], [self.ttml])
        older = self.client.get(f"/v1/threads/{self.ttml}/messages?limit=40&before={page['messages'][0]['id']}", headers=HEADERS).json()
        self.assertEqual(len(older["messages"]), 20)
        self.assertFalse(older["more"])
        self.assertEqual(older["messages"][0]["text"], "line 0")

    def test_section_messages_page_backwards(self):
        for n in range(10):
            db.add_message("telegram", "user", f"m{n}", None)
        newest = self.client.get("/v1/sections/diablo/messages?limit=4", headers=HEADERS).json()["messages"]
        self.assertEqual([m["text"] for m in newest], ["m6", "m7", "m8", "m9"])
        older = self.client.get(f"/v1/sections/diablo/messages?limit=4&before={newest[0]['id']}", headers=HEADERS).json()["messages"]
        self.assertEqual([m["text"] for m in older], ["m2", "m3", "m4", "m5"])

    def test_tag_rename_merge_archive_and_split(self):
        user, reply = self.turn("q", "a", self.lights)
        moved = self.client.post("/v1/threads/tag", headers=HEADERS, json={"messages": [user], "add": [self.ttml], "remove": [self.lights]}).json()
        self.assertEqual(moved["tags"][str(reply)], [self.ttml])
        self.assertEqual(self.client.post("/v1/threads/tag", headers=HEADERS, json={"messages": [user], "add": ["tnope"]}).status_code, 404)
        renamed = self.client.patch(f"/v1/threads/{self.ttml}", headers=HEADERS, json={"title": "Lyrics"}).json()
        self.assertEqual(renamed["title"], "Lyrics")
        self.assertTrue(renamed["named"])
        merged = self.client.patch(f"/v1/threads/{self.shell}", headers=HEADERS, json={"merge_into": self.ttml}).json()
        self.assertEqual(merged["state"], "merged")
        archived = self.client.patch(f"/v1/threads/{self.lights}", headers=HEADERS, json={"state": "archived"}).json()
        self.assertEqual(archived["state"], "archived")
        ids = [t["id"] for t in self.client.get("/v1/threads", headers=HEADERS).json()["threads"]]
        self.assertEqual(ids, [self.ttml])
        split = self.client.post("/v1/threads", headers=HEADERS, json={"title": "Part two", "split_from": self.ttml, "messages": [reply]}).json()
        self.assertEqual(split["count"], 1)
        only = self.client.post("/v1/threads", headers=HEADERS, json={"title": "Alone", "messages": [user], "move": True}).json()
        self.assertEqual(db.get_message(user)["meta"]["threads"], [only["id"]])
        self.assertEqual(db.get_message(reply)["meta"]["threads"], [only["id"]])

    def test_chat_files_the_turn_and_returns_the_threads(self):
        async def fake_ask(conv, text, **kwargs):
            threads.attach_message(kwargs.get("message_id"), [self.ttml])
            return brains.Turn(text="sure", backend="claude", threads=[self.ttml])
        with mock.patch.object(brains, "ask", fake_ask):
            data = self.client.post("/v1/chat", headers=HEADERS, json={"text": "more TTML", "client_id": "c1"}).json()
        self.assertEqual(data["threads"], [{"id": self.ttml, "title": "TTML lyrics tool"}])
        self.assertEqual(db.get_message(data["id"])["meta"]["threads"], [self.ttml])
        self.assertEqual(db.get_message(data["user_id"])["meta"]["threads"], [self.ttml])


class TestAskUsesThreads(Case):
    def run_ask(self, text, message_id=None, machine=False, haiku=None):
        db.set_setting("brain.backend", "claude")
        db.set_setting("jev.enabled", "off")
        sent = {}

        async def fake_claude(conv, message, images, on_step, decision=None, session=None):
            sent["text"], sent["session"] = message, session
            return brains.Turn(text="Done.", backend="claude")

        async def ask_haiku(self_, prompt):
            if haiku is None:
                raise RuntimeError("off")
            return json.dumps(haiku)

        with mock.patch.object(brains, "run_claude", fake_claude), \
             mock.patch.object(threads.Quick, "ask", ask_haiku), \
             mock.patch.object(brains, "STATE", self.folder), \
             mock.patch.object(route, "LOG", self.folder / "route.jsonl"), \
             mock.patch.object(brains, "ClaudeSession", FakeSession), \
             mock.patch.object(brains.lockguard, "for_turn", return_value=brains.lockguard.Guard() if hasattr(brains.lockguard, "Guard") else mock.Mock(line="", restricted=False)):
            turn = asyncio.run(brains.ask("main", text, origin="t3 watcher" if machine else "telegram", message_id=message_id, machine=machine))
        return turn, sent

    def test_machine_turns_make_no_threads_and_keep_the_lane(self):
        before = len(db.list_threads())
        turn, sent = self.run_ask("[t3 event] The thread Login fix finished", machine=True,
                                  haiku={"r": [], "a": [], "n": ["Login fix", "x"], "m": False, "f": False})
        self.assertEqual(len(db.list_threads()), before)
        self.assertEqual(turn.threads, [])
        self.assertIsNotNone(sent["session"])

    def test_a_failed_pick_gets_the_old_recap(self):
        self.turn("TTML step", "TTML reply", self.ttml, ago=60)
        said = db.add_message("telegram", "user", "and then?", None)
        with mock.patch.object(threads, "command", side_effect=RuntimeError("db gone")):
            turn, sent = self.run_ask("and then?", message_id=said)
        self.assertIn("Recap of the recent turns", sent["text"])
        self.assertEqual(turn.threads, [])

    def test_a_turn_carries_only_its_thread_and_files_the_message(self):
        db.set_setting("brain.backend", "claude")
        db.set_setting("jev.enabled", "off")
        for n in range(6):
            self.turn(f"TTML detail {n}", f"ttml reply {n}", self.ttml, ago=900 - n)
        self.turn("lights off", "Off, sir.", self.lights, ago=400)
        said = db.add_message("telegram", "user", "turn on my lights and make them warm", None)
        sent = {}

        async def fake_claude(conv, text, images, on_step, decision=None, session=None):
            sent["text"] = text
            sent["session"] = session
            return brains.Turn(text="Done.", backend="claude")

        async def haiku(self_, prompt):
            return json.dumps({"r": [self.lights], "a": [self.lights], "n": None, "m": False, "f": False})

        with mock.patch.object(brains, "run_claude", fake_claude), \
             mock.patch.object(threads.Quick, "ask", haiku), \
             mock.patch.object(brains, "STATE", self.folder), \
             mock.patch.object(route, "LOG", self.folder / "route.jsonl"), \
             mock.patch.object(brains, "ClaudeSession", FakeSession), \
             mock.patch.object(brains.lockguard, "for_turn", return_value=brains.lockguard.Guard() if hasattr(brains.lockguard, "Guard") else mock.Mock(line="", restricted=False)):
            turn = asyncio.run(brains.ask("main", "turn on my lights and make them warm", origin="telegram", message_id=said))
        self.assertEqual(turn.threads, [self.lights])
        self.assertIn("Room lights", sent["text"])
        self.assertNotIn("TTML detail", sent["text"])
        self.assertEqual(db.get_message(said)["meta"]["threads"], [self.lights])
        self.assertIsNotNone(sent["session"])
        usage = [json.loads(line) for line in (self.folder / "usage.jsonl").read_text().splitlines()]
        self.assertEqual(usage[-1]["pick"]["source"], "haiku")


if __name__ == "__main__":
    unittest.main()
