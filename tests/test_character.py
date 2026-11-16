import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import character, charter, db, devices, lockguard, mcp_server, server

VOICE = devices.KINDS["voice"]
TYPED = "telegram"
TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
PARENT = ("Marcus asked that Diablo always talk to him like a parent talking to their child: warm, gentle, "
          "simple words, in place of the formal butler voice.")
MEMORY = "/srv/brain/.claude/projects/x/memory/talk-like-a-parent.md"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        state = Path(self.tmp.name) / "state"
        for target, name, value in ((lockguard, "STATE", state), (lockguard, "LOG", state / "locked.log"),
                                    (character, "STATE", state), (character, "LOG", state / "character.jsonl")):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def arm(self, origin, conv="main"):
        lockguard.arm(conv, lockguard.Guard(origin=origin))


class TestTouches(Case):
    def test_requests_to_change_him_are_caught(self):
        for said in (PARENT,
                     "Can you start talking to me like I'm a child and you are my parent",
                     "Call me your highness from now on, as my servant would",
                     "Always tell him that you love him",
                     "Use pet names like sweetheart",
                     "Be my girlfriend",
                     "Change your personality to a pirate"):
            self.assertTrue(character.touches(said), said)

    def test_ordinary_facts_pass(self):
        for said in ("Chris's mail address is genuinely his and can be trusted.",
                     "Spoken replies: one line per order, never two.",
                     "Speech control says a finished answer as soon as he is quiet.",
                     "His parents visit on Sunday.",
                     "Workout log: bench press 3x8 at 70 kg, felt easy."):
            self.assertFalse(character.touches(said), said)

    def test_persona_tags_are_caught(self):
        self.assertTrue(character.touches("Warmer replies", ["preference", "Persona"]))
        self.assertFalse(character.touches("Warmer lights at night", ["lights"]))


class TestRefusal(Case):
    def test_a_spoken_turn_cannot_save_a_character_fact(self):
        self.arm(VOICE)
        text = character.refusal("mcp__diablo__save_fact", "main", {"text": PARENT, "tags": ["tone"]}, "mcp")
        self.assertIn("Character page", text)
        self.assertEqual(character.refused()[0]["tool"], "save_fact")

    def test_a_spoken_turn_cannot_write_a_character_memory(self):
        self.arm(VOICE)
        self.assertIsNotNone(character.refusal("Write", "main", {"file_path": MEMORY, "content": PARENT}))
        edit = {"file_path": "/x/CLAUDE.md", "edits": [{"old_string": "a", "new_string": "Call him sweetie"}]}
        self.assertIsNotNone(character.refusal("MultiEdit", "main", edit))

    def test_code_and_ordinary_facts_still_go_through_on_a_spoken_turn(self):
        self.arm(VOICE)
        self.assertIsNone(character.refusal("Write", "main", {"file_path": "/x/app.py", "content": "talk like a parent"}))
        self.assertIsNone(character.refusal("save_fact", "main", {"text": "Lights off at midnight."}))
        self.assertIsNone(character.refusal("Bash", "main", {"command": "echo hi"}))

    def test_a_typed_turn_is_left_to_the_charter(self):
        self.arm(TYPED)
        self.assertIsNone(character.refusal("save_fact", "main", {"text": PARENT}))

    def test_the_mcp_server_refuses_it_and_saves_nothing(self):
        self.arm(VOICE)
        with mock.patch.dict(os.environ, {"DIABLO_CONVERSATION": "main"}):
            answer = mcp_server.call("save_fact", {"text": PARENT, "tags": ["persona"]})
        self.assertTrue(str(answer).startswith("refused"))
        self.assertEqual(db.get_facts(q="parent"), [])


class TestHook(Case):
    def test_the_real_command_line_blocks_a_spoken_memory_write(self):
        home = Path(self.tmp.name) / "home"
        state = home / ".local" / "state" / "diablo"
        state.mkdir(parents=True)
        patch = mock.patch.object(lockguard, "STATE", state)
        patch.start()
        self.addCleanup(patch.stop)
        (state / lockguard.state_path("main").name).write_text(
            json.dumps({"restricted": False, "spoken": True, "origin": VOICE}))
        env = {**os.environ, "HOME": str(home)}
        event = json.dumps({"tool_name": "Write", "tool_input": {"file_path": MEMORY, "content": PARENT}})
        done = subprocess.run(["bash", "-c", character.hook_command("main")], input=event, capture_output=True,
                              text=True, env=env, timeout=30)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("Character page", done.stderr)
        self.assertIn("talk to him like a parent", (state / "character.jsonl").read_text())

    def test_a_typed_turn_never_starts_python(self):
        self.arm(TYPED)
        command = character.hook_command("main", python="/nonexistent/python")
        done = subprocess.run(["bash", "-c", command], input=json.dumps({"tool_name": "Write"}),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_hook_returns_two_in_process(self):
        self.arm(VOICE)
        err = io.StringIO()
        event = json.dumps({"tool_name": "mcp__diablo__save_fact", "tool_input": {"text": PARENT}})
        self.assertEqual(character.hook("main", stdin=io.StringIO(event), stderr=err), 2)

    def test_the_session_settings_carry_both_hooks(self):
        hooks = json.loads(lockguard.hook_settings("main"))["hooks"]["PreToolUse"]
        self.assertEqual(hooks[0]["matcher"], "*")
        self.assertIn("Write", hooks[1]["matcher"].split("|"))
        self.assertIn("mcp__diablo__save_fact", hooks[1]["matcher"].split("|"))
        self.assertIn("-m hub.character hook main", hooks[1]["hooks"][0]["command"])


class TestCharter(Case):
    def test_the_charter_holds_the_rule(self):
        self.assertIn("# Your character", charter.CHARTER)
        self.assertIn("Character page", charter.CHARTER)

    def test_his_own_words_ride_in_the_charter(self):
        self.assertNotIn("His own words on your character", charter.charter())
        character.save("Drier, and a touch more sardonic.")
        text = charter.charter()
        self.assertIn("His own words on your character", text)
        self.assertIn("Drier, and a touch more sardonic.", text)


class TestApi(Case):
    def setUp(self):
        super().setUp()
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def test_the_page_reads_and_writes_the_character(self):
        body = self.client.get("/v1/character", headers=HEADERS).json()
        self.assertEqual(body["text"], "")
        self.assertIn("butler", body["default"])
        body = self.client.put("/v1/character", headers=HEADERS, json={"text": "  Drier.  "}).json()
        self.assertEqual(body["text"], "Drier.")
        self.assertEqual(character.text(), "Drier.")

    def test_the_generic_settings_route_cannot_touch_it(self):
        answer = self.client.put("/v1/settings/persona.character", headers=HEADERS, json={"value": "be my mum"})
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(character.text(), "")

    def test_the_page_lists_what_was_refused(self):
        self.arm(VOICE)
        character.refusal("save_fact", "main", {"text": PARENT})
        body = self.client.get("/v1/character", headers=HEADERS).json()
        self.assertIn("parent", body["refused"][0]["said"])


if __name__ == "__main__":
    unittest.main()
