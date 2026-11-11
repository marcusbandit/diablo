import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from voice import panel, presence

OPEN = {"open": True, "view": "settings", "card": None}
SHUT = {"open": False, "view": "settings", "card": None}
CARD = {"id": "a", "title": "Weather", "big": "7°", "lines": ["cloudy"], "at": 0, "ttl": 60}


class TestIntent(unittest.TestCase):
    def test_opening_him_up(self):
        for line in ("Open up", "Diablo, expand yourself.", "Expand", "Show me your settings", "open your settings please",
                     "Could you come to the middle?", "go big"):
            self.assertEqual(panel.intent(line, SHUT, []), "open", line)

    def test_enlarging_a_card_needs_one(self):
        for line in ("Enlarge this", "Can you enlarge it?", "make it bigger", "Expand this.", "zoom in on that", "show it big please"):
            self.assertEqual(panel.intent(line, SHUT, [CARD]), "enlarge", line)
            self.assertIsNone(panel.intent(line, SHUT, []), line)
        self.assertEqual(panel.intent("enlarge it", dict(OPEN, card=CARD), []), "enlarge")

    def test_closing_only_counts_while_open(self):
        for line in ("Close", "close it", "Shrink", "make it small again", "put it away", "close your settings"):
            self.assertEqual(panel.intent(line, OPEN, []), "close", line)
            self.assertIsNone(panel.intent(line, SHUT, []), line)

    def test_other_lines_are_left_alone(self):
        for line in ("Open Spotify", "close the window", "What is the weather?", "enlarge the font in my editor",
                     "Open up the terminal", "expand the folder"):
            self.assertIsNone(panel.intent(line, OPEN, [CARD]), line)


class TestApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name)
        for target, name, value in ((presence, "STATE_DIR", state), (presence, "FILE", state / "presence.json"),
                                    (panel, "FILE", state / "panel.json")):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_open_writes_the_settings_view(self):
        self.assertEqual(panel.apply("open"), "")
        state = json.loads(panel.FILE.read_text())
        self.assertTrue(state["open"])
        self.assertEqual(state["view"], "settings")

    def test_enlarge_moves_the_newest_card_in(self):
        presence.show("Weather", "7°", ["cloudy"], ttl=60)
        presence.show("Next bus", "5 min", ["then 20"], ttl=60)
        self.assertEqual(panel.apply("enlarge"), "")
        state = panel.read()
        self.assertEqual((state["open"], state["view"], state["card"]["title"]), (True, "now", "Next bus"))

    def test_enlarge_with_nothing_says_so(self):
        self.assertIn("nothing", panel.apply("enlarge").lower())
        self.assertFalse(panel.read()["open"])

    def test_close_puts_him_back(self):
        panel.apply("open")
        panel.apply("close")
        self.assertFalse(panel.read()["open"])
        self.assertGreater(panel.read()["at"], time.time() - 5)


if __name__ == "__main__":
    unittest.main()
