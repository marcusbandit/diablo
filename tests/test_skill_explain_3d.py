import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "hub" / "skills" / "explain-3d"


class TestExplain3d(unittest.TestCase):
    def test_the_skill_says_when_to_load(self):
        head = (SKILL / "SKILL.md").read_text().split("---")[1]
        self.assertIn("name: explain-3d", head)
        self.assertIn("description:", head)

    def test_the_example_fits_a_card(self):
        page = (SKILL / "example.html").read_text()
        self.assertLess(len(page.encode()), 512 * 1024)
        self.assertIn("three@0.170.0", page)
        self.assertNotRegex(page, r"file://|localhost|127\.0\.0\.1")
        self.assertFalse(re.search("[–—]", page))


if __name__ == "__main__":
    unittest.main()
