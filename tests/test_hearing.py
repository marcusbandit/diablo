import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import hearing


class TestParse(unittest.TestCase):
    def test_lines_become_rules(self):
        raw = "# names\nZorvane = Sore Vane, Zorvana\n\nnot a rule\nBo = bo\n"
        self.assertEqual(hearing.parse(raw), [{"right": "Zorvane", "wrong": ["Sore Vane", "Zorvana"]}])

    def test_empty_setting_has_no_rules(self):
        self.assertEqual(hearing.parse(""), [])


class TestFix(unittest.TestCase):
    table = hearing.parse("Zorvane = Sore Vane, Zorvana\nPlimbeck = plim back")

    def test_whole_words_in_any_case(self):
        self.assertEqual(hearing.fix("Supper at ZORVANA's place", self.table), "Supper at Zorvane's place")

    def test_a_phrase_across_spaces(self):
        self.assertEqual(hearing.fix("pick her up at plim  back", self.table), "pick her up at Plimbeck")

    def test_part_of_a_longer_word_stays(self):
        self.assertEqual(hearing.fix("zorvanaed the data", self.table), "zorvanaed the data")

    def test_a_backslash_in_the_right_form_is_kept_as_written(self):
        table = hearing.parse("A\\B = ab")
        self.assertEqual(hearing.fix("say ab now", table), "say A\\B now")

    def test_the_right_form_stays(self):
        self.assertEqual(hearing.fix("Zorvane and Anna", self.table), "Zorvane and Anna")


if __name__ == "__main__":
    unittest.main()
