import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import tour


class Tour(unittest.TestCase):
    def setUp(self):
        tour._offered["at"] = 0.0

    def test_asking_to_be_shown_and_asking_what_he_can_do(self):
        self.assertTrue(tour.wanted("Diablo, show me what you can do"))
        self.assertTrue(tour.asked("What can you do?"))
        self.assertFalse(tour.asked("What can you do about the lights in the hall?"))

    def test_an_offer_takes_one_yes_or_no(self):
        tour.offer()
        self.assertEqual(tour.answer("yes please"), "yes")
        self.assertIsNone(tour.answer("yes"))
        tour.offer()
        self.assertEqual(tour.answer("nah"), "no")
        tour.offer()
        self.assertIsNone(tour.answer("turn the lights off"))

    def test_an_old_offer_is_gone(self):
        tour._offered["at"] = 1.0
        self.assertIsNone(tour.answer("yes", now=1.0 + tour.OFFER_SECONDS + 1))

    def test_it_points_at_a_named_button_or_tab_and_never_a_risky_one(self):
        controls = [{"role": "button", "name": "Delete", "box": [0, 0, 40, 20]},
                    {"role": "label", "name": "Title", "box": [0, 0, 40, 20]},
                    {"role": "button", "name": "Export", "box": [10, 10, 60, 24]},
                    {"role": "tab", "name": "Settings", "box": [80, 10, 60, 24]}]
        self.assertEqual(tour.pick_target(controls)["name"], "Settings")
        self.assertIsNone(tour.pick_target(controls[:2]))

    def test_app_names_read_well(self):
        self.assertEqual(tour.app_name({"class": "com.t3tools.T3Code"}), "T3 Code")
        self.assertEqual(tour.app_name({"class": "zen"}), "Zen")

    def test_every_line_is_spoken_and_shown_in_order(self):
        said = []
        with mock.patch.object(tour.presence, "show_reply", return_value="card"), \
                mock.patch.object(tour.Screen, "later"), mock.patch.object(tour.Screen, "close_picture"):
            for text, cue in tour.lines({}, {"name": "Export", "role": "button",
                                             "box": {"x": 1, "y": 1, "w": 50, "h": 20}}, said.append):
                cue()
        self.assertEqual(said[1], "I can point at anything on your screen, like your Export button.")
        self.assertTrue(said[-1].startswith("That's the tour"))


if __name__ == "__main__":
    unittest.main()
