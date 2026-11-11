import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import brain


class BrainCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "vault"
        self.root.mkdir()
        self.env = mock.patch.dict(os.environ, {"DIABLO_BRAIN": str(self.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def note(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class TestSearch(BrainCase):
    def test_hit_is_case_insensitive_and_names_the_note(self):
        self.note("Home.md", "# brain\n\n| [[fleet-viewer]] | Diablo dashboard |\n")
        self.note("Projects/diablo.md", "the voice loop\nFleet-Viewer talks to it\n")
        result = brain.search("fleet-viewer")
        self.assertIn("Home: | [[fleet-viewer]] | Diablo dashboard |", result)
        self.assertIn("diablo: Fleet-Viewer talks to it", result)
        self.assertNotIn("the voice loop", result)

    def test_miss_and_empty_query(self):
        self.note("Home.md", "nothing here\n")
        self.assertEqual(brain.search("kangaeru"), "nothing found")
        self.assertEqual(brain.search("   "), "nothing found")

    def test_skip_rules(self):
        self.note(".obsidian/workspace.md", "secret hit\n")
        self.note(".trash/old.md", "secret hit\n")
        self.note(".stversions/x.md", "secret hit\n")
        self.note("Memory/note.sync-conflict-20260913.md", "secret hit\n")
        self.note("Memory/keep.txt", "secret hit\n")
        self.assertEqual(brain.search("secret hit"), "nothing found")
        self.note("Memory/keep.md", "secret hit\n")
        self.assertEqual(brain.search("secret hit"), "keep: secret hit")

    def test_limits(self):
        for i in range(12):
            self.note(f"n{i:02d}.md", "hit\n")
        self.assertEqual(len(brain.search("hit", limit=3).splitlines()), 3)
        self.note("long.md", "x" * 500 + "\n")
        line = brain.search("xxx").splitlines()[0]
        self.assertEqual(len(line), len("long: ") + 200)
        for i in range(30):
            self.note(f"big{i:02d}.md", ("bulk " * 60 + "\n") * 5)
        self.assertLessEqual(len(brain.search("bulk", limit=30)), 3000)

    def test_missing_root(self):
        with mock.patch.dict(os.environ, {"DIABLO_BRAIN": str(self.root / "nope")}):
            self.assertEqual(brain.search("x"), "nothing found")


class TestRead(BrainCase):
    def test_by_stem_case_insensitive(self):
        self.note("Core/conventions.md", "rules\n")
        self.assertEqual(brain.read("Conventions"), "rules\n")
        self.assertEqual(brain.read("conventions"), "rules\n")

    def test_by_relative_path_with_and_without_suffix(self):
        self.note("Projects/diablo.md", "voice loop\n")
        self.assertEqual(brain.read("Projects/diablo.md"), "voice loop\n")
        self.assertEqual(brain.read("Projects/diablo"), "voice loop\n")

    def test_missing_note(self):
        self.assertEqual(brain.read("ghost"), "no such note")
        self.assertEqual(brain.read(""), "no such note")

    def test_outside_root_refused(self):
        outside = self.root.parent / "outside.md"
        outside.write_text("leak\n")
        self.assertEqual(brain.read("../outside.md"), "no such note")
        self.assertEqual(brain.read("../outside"), "no such note")
        self.assertEqual(brain.read(str(outside)), "no such note")

    def test_skipped_dirs_do_not_resolve_by_stem(self):
        self.note(".trash/gone.md", "old\n")
        self.assertEqual(brain.read("gone"), "no such note")

    def test_truncation(self):
        self.note("big.md", "a" * 7000)
        text = brain.read("big")
        self.assertTrue(text.endswith("\n[truncated]"))
        self.assertEqual(len(text), 6000 + len("\n[truncated]"))
        self.note("small.md", "b" * 6000)
        self.assertEqual(brain.read("small"), "b" * 6000)

    def test_default_root_expands_home(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIABLO_BRAIN", None)
            self.assertEqual(brain.root(), Path.home() / "Obsidian" / "Agent")


class TestLog(BrainCase):
    def test_creates_with_frontmatter_then_appends(self):
        fixed = datetime.datetime(2026, 9, 13, 16, 45)
        with mock.patch.object(brain.datetime, "datetime") as fake:
            fake.now.return_value = fixed
            self.assertEqual(brain.log("first thing"), "logged")
            self.assertEqual(brain.log("second\nthing"), "logged")
        path = self.root / "Log" / "2026-09-13.md"
        self.assertEqual(
            path.read_text(),
            "---\ntype: log\nstatus: current\nupdated: 2026-09-13\n---\n"
            "- 16:45 hub: first thing\n- 16:45 hub: second thing\n",
        )

    def test_appends_to_existing_file_without_a_trailing_newline(self):
        fixed = datetime.datetime(2026, 9, 13, 9, 5)
        self.note("Log/2026-09-13.md", "---\ntype: log\n---\n- old line")
        with mock.patch.object(brain.datetime, "datetime") as fake:
            fake.now.return_value = fixed
            brain.log("new line")
        self.assertEqual(
            (self.root / "Log" / "2026-09-13.md").read_text(),
            "---\ntype: log\n---\n- old line\n- 09:05 hub: new line\n",
        )

    def test_empty_text_writes_nothing(self):
        self.assertEqual(brain.log("  "), "nothing to log")
        self.assertFalse((self.root / "Log").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
