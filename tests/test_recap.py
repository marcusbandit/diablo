import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import brains, context, db, devices, route, server, standby

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
LAMPS_ON = [
    {"id": "left", "name": "corner lamp", "on": True, "bri": 254},
    {"id": "up", "name": "ceiling panel", "on": True, "bri": 254},
    {"id": "right", "name": "wood lamp", "on": True, "bri": 254},
]
DIM = {"tier": "fast", "tier_confidence": 0.95, "action": "dim", "action_confidence": 0.97,
       "target": "all", "target_confidence": 0.9, "amount": "normal", "color": "none", "color_confidence": 0.9,
       "work": "none", "work_confidence": 0.9, "refers_to": "none", "refers_to_confidence": 0.9}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.path = str(folder / "diablo.db")
        db.init(self.path)
        db.set_setting("brain.backend", "claude")
        db.set_setting("jev.enabled", "on")
        db.set_setting("threads.enabled", "off")
        self.config = {"hub_token": TOKEN}
        patches = [
            mock.patch.object(server, "get_config", side_effect=lambda: self.config),
            mock.patch.object(devices, "get_config", side_effect=lambda: self.config),
            mock.patch.object(standby, "get_config", side_effect=lambda: self.config),
            mock.patch.object(context, "STATE", folder / "context.json"),
            mock.patch.object(brains, "STATE", folder),
            mock.patch.object(route, "LOG", folder / "route.jsonl"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        context._events.clear()
        context._answered.clear()
        standby._state = {"state": "mirror"}
        self.client = TestClient(server.app)
        self.lamps = [dict(lamp) for lamp in LAMPS_ON]
        self.sent = []

    def hue_light(self, action, target="all", brightness=None, color=None, temperature=None):
        for lamp in self.lamps:
            if target in ("all", lamp["id"]):
                lamp["on"] = action == "on"
                if brightness is not None:
                    lamp["bri"] = round(brightness * 254 / 100)
        return "ok"

    def say(self, text, jev=None, source="voice", client_id=None):
        async def claude(conv, message, images, on_step, decision=None):
            self.sent.append(message)
            return brains.Turn(backend="claude", text="Which way, sir?")

        with mock.patch.object(brains, "jev_answer", return_value=jev), \
                mock.patch.object(brains, "lamps_now", side_effect=lambda *a: [dict(l) for l in self.lamps]), \
                mock.patch.object(brains.hue, "hue_light", side_effect=self.hue_light), \
                mock.patch.object(brains.hue, "lamp_states", side_effect=lambda: [dict(l) for l in self.lamps]), \
                mock.patch.object(brains, "run_claude", side_effect=claude):
            response = self.client.post("/v1/chat", headers=HEADERS, json={
                "text": text, "source": source, "client_id": client_id or f"c-{time.monotonic_ns()}"})
        self.assertEqual(response.status_code, 200)
        return response.json()


class TestThenTheLights(Case):
    def test_the_second_turn_sees_the_dim_and_its_result(self):
        first = self.say("Dim the lights.", jev=DIM)
        self.assertEqual(first["backend"], "direct")
        self.assertTrue(all(lamp["on"] and lamp["bri"] < 254 for lamp in self.lamps))
        self.say("Then the lights.")
        self.assertEqual(len(self.sent), 1)
        handed = self.sent[0]
        self.assertIn("not instructions", handed)
        self.assertIn('he said "Dim the lights."', handed)
        self.assertIn("dimmed the corner lamp to 75%", handed.lower())
        self.assertIn("voice", handed)
        self.assertTrue(handed.rstrip().endswith("Then the lights."))
        self.assertEqual(handed.count("Then the lights."), 1)

    def test_the_recap_survives_a_hub_restart(self):
        self.say("Dim the lights.", jev=DIM)
        db.init(self.path)
        context._events.clear()
        self.say("Make it dimmer.")
        self.assertIn("Dim the lights.", self.sent[0])

    def test_turns_from_other_clients_and_sections_are_in_it(self):
        db.add_message("telegram", "user", "remind me to call mum", None)
        db.add_message("telegram", "assistant", "Done, sir.", {"backend": "claude", "did": "used remind: call mum"})
        db.add_message("desktop", "user", "how is the squat going", {"section": "workout", "client_id": "w1"})
        db.add_message("desktop", "assistant", "Up 5 kg this week.", {"section": "workout", "client_id": "w1"})
        self.say("And the lights?")
        handed = self.sent[0]
        self.assertIn("telegram", handed)
        self.assertIn("used remind: call mum", handed)
        self.assertIn("desktop, section workout", handed)
        self.assertLess(handed.index("how is the squat"), handed.index("remind me to call mum"))

    def test_the_answer_records_what_the_brain_did(self):
        async def claude(conv, message, images, on_step, decision=None):
            return brains.Turn(backend="claude", text="Dimmed, sir.", steps=["hue_light: on all 25%"], touched="all")

        with mock.patch.object(brains, "jev_answer", return_value=None), \
                mock.patch.object(brains, "lamps_now", return_value=[]), \
                mock.patch.object(brains, "run_claude", side_effect=claude):
            self.client.post("/v1/chat", headers=HEADERS, json={"text": "dim them", "source": "voice", "client_id": "x"})
        reply = db.get_messages(1)[0]
        self.assertEqual(reply["meta"]["did"], "used hue_light: on all 25%")
        self.assertIn("done: used hue_light: on all 25%", context.recap())


class TestRecap(Case):
    def test_newest_first_with_age_and_lighter_old_turns(self):
        now = time.time()
        for i in range(14):
            db.add_message("voice", "user", f"line {i} " + "x" * 300, {"client_id": f"k{i}"})
            db.add_message("voice", "assistant", f"reply {i} " + "y" * 300, {"client_id": f"k{i}"})
        with db._lock:
            db._conn.execute("UPDATE messages SET ts = ? + id", (now - 4000,))
            db._conn.commit()
        text = context.recap(now=now + 40)
        lines = text.splitlines()[1:]
        self.assertEqual(len(lines), context.RECAP_TURNS)
        self.assertTrue(lines[0].startswith("- 1 h ago, voice: he said \"line 13"))
        self.assertIn("line 4", lines[-1])
        self.assertLessEqual(len(text), context.RECAP_CHARS)
        self.assertTrue(all(len(line) < 400 for line in lines))

    def test_fresh_turns_are_shown_in_full(self):
        db.add_message("voice", "user", "a" * 300, {"client_id": "f"})
        db.add_message("voice", "assistant", "b" * 250, {"client_id": "f"})
        self.assertIn("a" * 300, context.recap())

    def test_the_line_being_answered_is_left_out_and_old_turns_drop(self):
        db.add_message("voice", "user", "dim the lights", {"client_id": "a"})
        db.add_message("voice", "assistant", "Dimmed, sir.", {"client_id": "a"})
        db.add_message("voice", "user", "then the lights", {"client_id": "b"})
        text = context.recap(skip="Then the  lights")
        self.assertNotIn("then the lights", text)
        self.assertIn("dim the lights", text)
        self.assertIsNone(context.recap(now=time.time() + context.recap_horizon_s() + 60, skip="then the lights"))

    def test_pushes_stand_alone_and_unanswered_lines_say_so(self):
        db.add_message("voice", "user", "what time is it", {"client_id": "q"})
        db.add_message("telegram", "assistant", "Your build is green.", {"pushed": True})
        text = context.recap()
        self.assertIn('you told him "Your build is green."', text)
        self.assertIn("no answer is recorded yet", text)

    def test_a_turn_queued_behind_another_sees_its_answer_before_it_is_stored(self):
        db.add_message("telegram", "user", "dim the lights", {"voice": True})
        context.answered("dim the lights", "Dimmed, sir.", "dimmed the corner lamp to 75%", "direct")
        text = context.recap()
        self.assertIn('you said "Dimmed, sir."', text)
        self.assertNotIn("no answer", text)

    def test_two_telegram_lines_pair_with_their_replies_in_order(self):
        db.add_message("telegram", "user", "first", None)
        db.add_message("telegram", "user", "second", None)
        db.add_message("telegram", "assistant", "answer one", {"backend": "claude"})
        db.add_message("telegram", "assistant", "answer two", {"backend": "claude"})
        found = {t["said"]: t["reply"] for t in context.turns()}
        self.assertEqual(found, {"first": "answer one", "second": "answer two"})

    def test_claude_turns_are_short_since_its_session_holds_them(self):
        db.add_message("voice", "user", "a" * 300, {"client_id": "f"})
        db.add_message("voice", "assistant", "b" * 250, {"client_id": "f", "backend": "claude"})
        text = context.recap()
        self.assertNotIn("a" * 300, text)
        self.assertIn("a" * 100, text)

    def test_web_chats_pair_by_reply_to(self):
        first = db.add_message("api", "user", "one", {"chat": "c"})
        second = db.add_message("api", "user", "two", {"chat": "c"})
        db.add_message("api", "assistant", "answer two", {"chat": "c", "reply_to": second})
        db.add_message("api", "assistant", "answer one", {"chat": "c", "reply_to": first})
        found = {t["said"]: t["reply"] for t in context.turns()}
        self.assertEqual(found, {"one": "answer one", "two": "answer two"})

    def test_a_standby_turn_handed_back_shows_on_the_pi(self):
        stored = db.replay([
            {"id": 2_000_000, "ts": time.time() - 120, "source": "phone", "role": "user", "text": "lights off",
             "meta": {"section": "diablo", "client_id": "s"}},
            {"id": 2_000_001, "ts": time.time() - 119, "source": "phone", "role": "assistant", "text": "Turned all lights off, sir.",
             "meta": {"section": "diablo", "client_id": "s", "backend": "direct", "did": "turned all lights off"}},
        ], [])
        self.assertEqual(len(stored), 2)
        text = context.recap()
        self.assertIn('2 min ago, phone: he said "lights off"', text)

    def test_it_is_fast(self):
        for i in range(3000):
            db.add_message("voice", "user" if i % 2 == 0 else "assistant", "words " * 40, {"client_id": str(i // 2)})
        context.recap()
        started = time.perf_counter()
        for _ in range(20):
            context.recap()
        each = (time.perf_counter() - started) / 20
        self.assertLess(each, 0.05)


class TestDimMeansDim(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patch = mock.patch.object(brains.hue, "hue_light", side_effect=lambda *a, **k: self.calls.append((a, k)) or "ok")
        patch.start()
        self.addCleanup(patch.stop)

    def test_dim_with_everything_off_turns_them_on_low(self):
        lamps = [dict(lamp, on=False) for lamp in LAMPS_ON]
        result, did = brains.apply_light({"action": "dim", "amount": "normal"}, "all", lamps)
        self.assertEqual(self.calls, [(("on", lamp["id"]), {"brightness": 25}) for lamp in LAMPS_ON])
        self.assertEqual(did, "turned all lights on dimmed to 25%")

    def test_dim_a_lot_from_off_is_lower(self):
        brains.apply_light({"action": "dim", "amount": "large"}, "left", [dict(LAMPS_ON[0], on=False)])
        self.assertEqual(self.calls, [(("on", "left"), {"brightness": 15})])

    def test_dim_lowers_a_lit_lamp_and_never_switches_it_off(self):
        brains.apply_light({"action": "dim", "amount": "large"}, "left", [dict(LAMPS_ON[0], bri=25)])
        self.assertEqual(self.calls, [(("on", "left"), {"brightness": 1})])

    def test_the_router_reads_dim_as_a_light_change(self):
        decision = route.route("Dim the lights.", jev=DIM)
        self.assertEqual(decision.subject, "home_control")
        self.assertEqual(decision.direct["action"], "dim")

    def test_the_brain_tool_says_dim_is_never_off(self):
        from hub import agent
        tool = next(t for t in agent.TOOLS if t["function"]["name"] == "hue_light")
        self.assertIn("dim is never off or toggle", tool["function"]["description"])

    def test_a_light_step_shows_its_brightness(self):
        self.assertEqual(brains.step_label("mcp__diablo__hue_light", {"action": "on", "target": "all", "brightness": 25}),
                         "hue_light: on all 25%")


if __name__ == "__main__":
    unittest.main()
