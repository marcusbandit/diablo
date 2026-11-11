import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import persona

BOTH = """# Diablo

You are Diablo.

## Voice rules, always

- Two sentences.

## Tongues

English, Japanese, Danish only.

## Glyphs

Never read codepoints aloud.

## Memory

The vault at ~/Obsidian/Agent is shared.
Its rules start at Home.md.
"""


class TestPersonaText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "PERSONA.md"
        persona._cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_sections_in_file_order(self):
        self.path.write_text(BOTH)
        text = persona.persona_text(self.path)
        self.assertEqual(
            text,
            "English, Japanese, Danish only.\n\nThe vault at ~/Obsidian/Agent is shared.\nIts rules start at Home.md.",
        )
        self.assertNotIn("Two sentences", text)
        self.assertNotIn("codepoints", text)

    def test_one_section(self):
        self.path.write_text("## Memory\n\nOnly memory here.\n\n## Glyphs\n\nno\n")
        self.assertEqual(persona.persona_text(self.path), "Only memory here.")

    def test_no_wanted_section(self):
        self.path.write_text("# Diablo\n\n## Glyphs\n\nno\n")
        self.assertEqual(persona.persona_text(self.path), "")

    def test_missing_file(self):
        self.assertEqual(persona.persona_text(self.path), "")

    def test_deeper_headings_stay_inside_the_body(self):
        self.path.write_text("## Memory\n\nintro\n\n### Detail\n\nmore\n\n## Other\n\nno\n")
        self.assertEqual(persona.persona_text(self.path), "intro\n\n### Detail\n\nmore")

    def test_env_path_and_default_expand_home(self):
        self.path.write_text("## Tongues\n\nfrom env\n")
        with mock.patch.dict(os.environ, {"DIABLO_PERSONA_MD": str(self.path)}):
            self.assertEqual(persona.persona_text(), "from env")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIABLO_PERSONA_MD", None)
            os.environ.pop("DIABLO_AGENTS_MD", None)
            self.assertEqual(persona.persona_path(), Path.home() / "Projects" / "diablo" / "PERSONA.md")

    def test_cache_by_mtime(self):
        self.path.write_text("## Tongues\n\nfirst\n")
        self.assertEqual(persona.persona_text(self.path), "first")
        stamp = self.path.stat().st_mtime
        self.path.write_text("## Tongues\n\nsecond\n")
        os.utime(self.path, (stamp, stamp))
        self.assertEqual(persona.persona_text(self.path), "first")
        os.utime(self.path, (stamp + 10, stamp + 10))
        self.assertEqual(persona.persona_text(self.path), "second")


if __name__ == "__main__":
    unittest.main(verbosity=2)
