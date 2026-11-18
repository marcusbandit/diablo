import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import showcase


class Showcase(unittest.TestCase):
    def setUp(self):
        showcase._offered.clear()

    def test_asking_to_be_shown_starts_the_tour(self):
        for line in ("Diablo, show me what you can do", "give me a demo", "Demo your abilities for me",
                     "show me a tour", "vis mig hvad du kan"):
            with self.subTest(line=line):
                self.assertEqual(showcase.note("main", line), showcase.SHOW_NOTE)

    def test_asking_what_he_can_do_offers_first(self):
        for line in ("What can you do?", "what are you capable of", "What are your abilities", "hvad kan du"):
            with self.subTest(line=line):
                self.assertEqual(showcase.note("main", line), showcase.ASK_NOTE)

    def test_a_yes_to_the_offer_starts_the_tour(self):
        showcase.note("main", "what can you do", now=100)
        self.assertEqual(showcase.note("main", "Yes, go ahead", now=130), showcase.SHOW_NOTE)

    def test_a_late_or_unrelated_answer_does_not(self):
        showcase.note("main", "what can you do", now=100)
        self.assertIsNone(showcase.note("main", "yes", now=100 + showcase.OFFER_SECONDS + 1))
        showcase.note("main", "what can you do", now=100)
        self.assertIsNone(showcase.note("main", "turn off the lights", now=110))
        self.assertIsNone(showcase.note("main", "yes", now=115))

    def test_the_offer_belongs_to_its_conversation(self):
        showcase.note("main", "what can you do", now=100)
        self.assertIsNone(showcase.note("web-1", "yes", now=110))

    def test_ordinary_lines_are_left_alone(self):
        for line in ("show me the weather", "what can you tell me about rust", "yes", "show me my reminders"):
            with self.subTest(line=line):
                self.assertIsNone(showcase.note("main", line))

    def test_the_skill_ships(self):
        skill = Path(showcase.__file__).parent / "skills" / "showcase" / "SKILL.md"
        self.assertIn("name: showcase", skill.read_text())


if __name__ == "__main__":
    unittest.main()
