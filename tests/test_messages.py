import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import messages, skills


class TestDiscordHelpers(unittest.TestCase):
    def test_newest_after_filters_and_orders_by_snowflake(self):
        rows = [{"id": "30"}, {"id": "10"}, {"id": "20"}]
        self.assertEqual([m["id"] for m in messages.newest_after(rows, "10")], ["20", "30"])
        self.assertEqual(messages.newest_after(rows, "30"), [])

    def test_dm_name_prefers_group_name_then_recipients(self):
        self.assertEqual(messages.dm_name({"name": "study crew", "recipients": []}), "study crew")
        self.assertEqual(
            messages.dm_name({"recipients": [{"username": "anna", "global_name": "Anna B"}, {"username": "ben"}]}),
            "Anna B, ben",
        )

    def test_message_rows_mark_his_own_and_skip_empty(self):
        raw = [
            {"id": "1", "content": "salut", "author": {"id": "u2", "username": "anna"}, "timestamp": "2026-09-13T08:00:00Z"},
            {"id": "2", "content": "", "author": {"id": "u1"}, "timestamp": "2026-09-13T08:01:00Z"},
            {"id": "3", "content": "moi", "author": {"id": "u1", "username": "marcus"}, "timestamp": "2026-09-13T08:02:00Z"},
        ]
        rows = messages._discord_message_rows(raw, "Anna", "u1")
        self.assertEqual([r["from"] for r in rows], ["anna", "marcus"])
        self.assertEqual([r["mine"] for r in rows], [False, True])
        self.assertGreater(rows[0]["when"], 0)

    def test_wanted_guilds_by_name_or_id(self):
        cfg = {"discord": {"servers": ["french with friends", "999"]}}
        guilds = [{"id": "1", "name": "French With Friends"}, {"id": "999", "name": "x"}, {"id": "5", "name": "no"}]
        self.assertEqual([g["id"] for g in messages._wanted_guilds(cfg, guilds)], ["1", "999"])
        self.assertEqual(messages._wanted_guilds({}, guilds), [])


class TestSummary(unittest.TestCase):
    def test_nothing_new(self):
        self.assertEqual(messages.summarize([]), "nothing new")

    def test_groups_by_chat_newest_chat_first(self):
        rows = [
            messages.row("telegram", "Anna", "Anna", "are we still on?", 100),
            messages.row("telegram", "Anna", "Anna", "hello?", 90),
            messages.row("discord", "French #general", "Luc", "bonjour a tous", 200),
        ]
        text = messages.summarize(rows, per_chat=1)
        self.assertTrue(text.startswith("discord French #general, 1 new: Luc: bonjour a tous"))
        self.assertIn("telegram Anna, 2 new and 1 more: Anna: are we still on?", text)

    def test_problems_are_spoken(self):
        self.assertIn("could not read telegram: not logged in", messages.summarize([], ["telegram: not logged in"]))


class TestSkillWiring(unittest.TestCase):
    def test_check_messages_is_a_tell_skill_that_speaks_the_summary(self):
        rows = [messages.row("telegram", "Anna", "Anna", "hi", 1)]
        with mock.patch("voice.messages.check", return_value=(rows, [])):
            kind, ok, text = skills.run("check_messages", {"source": "telegram"}, {})
        self.assertEqual((kind, ok), ("tell", True))
        self.assertIn("Anna: hi", text)

    def test_check_with_only_problems_fails_so_he_explains(self):
        with mock.patch("voice.messages.check", return_value=([], ["discord: needs a token"])):
            kind, ok, text = skills.run("check_messages", {}, {})
        self.assertFalse(ok)
        self.assertIn("needs a token", text)

    def test_read_without_a_chat_fails(self):
        self.assertFalse(skills.run("read_messages", {}, {})[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
