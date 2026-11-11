import unittest
from unittest import mock

from voice import eyes


class ClauseTest(unittest.TestCase):
    def test_split_at_commas_and_stops(self):
        self.assertEqual(eyes.clauses("Okay, I moved it to Thursday. Want a reminder?"),
                         [("Okay,", 0), ("I moved it to Thursday.", 1), ("Want a reminder?", 6)])

    def test_titles_do_not_end_a_clause(self):
        self.assertEqual(eyes.clauses("Hello there Mr. Man. I did it."),
                         [("Hello there Mr. Man.", 0), ("I did it.", 4)])

    def test_a_short_no_still_ends_its_sentence(self):
        self.assertEqual([c for c, _ in eyes.clauses("Is it broken? No. It works now.")],
                         ["Is it broken?", "No.", "It works now."])

    def test_empty(self):
        self.assertEqual(eyes.clauses("  "), [])


class CueTest(unittest.TestCase):
    def test_looks_start_on_their_first_word(self):
        text = "Okay, I moved it. Want a reminder?"
        starts = [0.1, 0.6, 0.8, 0.9, 1.4, 1.7, 1.9]
        self.assertEqual(eyes.cues(text, ["happy", "neutral", "expectant"], starts, 2.5),
                         [(0.0, "happy"), (0.6, "neutral"), (1.4, "expectant")])

    def test_same_look_twice_is_one_cue(self):
        self.assertEqual(eyes.cues("a, b", ["proud", "proud"], [0.0, 0.5], 1.0), [(0.0, "proud")])

    def test_without_word_times_the_letters_share_the_clip(self):
        out = eyes.cues("aaaa, bbbb", ["neutral", "worried"], None, 2.0)
        self.assertEqual(out[0], (0.0, "neutral"))
        self.assertEqual(out[1][1], "worried")
        self.assertAlmostEqual(out[1][0], 1.0, delta=0.15)


class TagTest(unittest.TestCase):
    def test_no_jev_means_neutral(self):
        from voice import jevfast
        with mock.patch.dict(jevfast._bound, {"settings": None}):
            self.assertEqual(eyes.tag("Hello, Marcus."), ["neutral", "neutral"])

    def test_jev_answers_become_looks_and_glance_picks_a_side(self):
        from voice import jevfast
        answer = {"c1": "happy", "c2": "glance", "c3": "nonsense"}
        with mock.patch.object(jevfast, "ask", return_value=answer):
            looks = eyes.tag("Done, look at this, right?")
        self.assertEqual(looks[0], "happy")
        self.assertIn(looks[1], ("glance-left", "glance-right"))
        self.assertEqual(looks[2], "neutral")


if __name__ == "__main__":
    unittest.main()
