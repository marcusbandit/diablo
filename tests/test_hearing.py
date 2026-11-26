import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import hearing


class TestParse(unittest.TestCase):
    def test_lines_become_rules(self):
        raw = "# names\nAnnelise = Anna Lisa, Analise\n\nnot a rule\nBo = bo\n"
        self.assertEqual(hearing.parse(raw), [{"right": "Annelise", "wrong": ["Anna Lisa", "Analise"]}])

    def test_empty_setting_has_no_rules(self):
        self.assertEqual(hearing.parse(""), [])


class TestFix(unittest.TestCase):
    table = hearing.parse("Annelise = Anna Lisa, Analise\nKastrup = cast rope")

    def test_whole_words_in_any_case(self):
        self.assertEqual(hearing.fix("Christmas at ANALISE's place", self.table), "Christmas at Annelise's place")

    def test_a_phrase_across_spaces(self):
        self.assertEqual(hearing.fix("pick her up at cast  rope", self.table), "pick her up at Kastrup")

    def test_part_of_a_longer_word_stays(self):
        self.assertEqual(hearing.fix("analised the data", self.table), "analised the data")

    def test_the_right_form_stays(self):
        self.assertEqual(hearing.fix("Annelise and Anna", self.table), "Annelise and Anna")


if __name__ == "__main__":
    unittest.main()
