"""The set card can be dragged anywhere and opens where it was left (#148)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pushup"))

try:
    import pushup_ui
except (ImportError, ValueError):  # no GTK 4 on the box
    pushup_ui = None


@unittest.skipIf(pushup_ui is None, "needs GTK 4")
class TestCardSpot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(pushup_ui, "CARD_SPOT", Path(self.tmp.name) / "pushup" / "card_spot.json")
        patch.start()
        self.addCleanup(patch.stop)

    def test_first_card_sits_in_the_middle(self):
        self.assertEqual(pushup_ui.load_card_spot(), (0, 0))

    def test_a_dragged_spot_comes_back(self):
        pushup_ui.save_card_spot(-640, 210)
        self.assertEqual(pushup_ui.load_card_spot(), (-640, 210))

    def test_a_broken_file_falls_back_to_the_middle(self):
        pushup_ui.CARD_SPOT.parent.mkdir(parents=True)
        pushup_ui.CARD_SPOT.write_text("{not json")
        self.assertEqual(pushup_ui.load_card_spot(), (0, 0))

    def test_the_card_stays_whole_on_screen(self):
        size, area = (1000, 800), (5120, 1440)
        self.assertEqual(pushup_ui.clamp_offset((9000, -9000), size, area), (2060, -320))
        self.assertEqual(pushup_ui.clamp_offset((-9000, 9000), size, area), (-2060, 320))
        self.assertEqual(pushup_ui.clamp_offset((300, -100), size, area), (300, -100))

    def test_a_card_bigger_than_the_screen_stays_centred(self):
        self.assertEqual(pushup_ui.clamp_offset((50, 50), (1200, 900), (1000, 800)), (0, 0))


if __name__ == "__main__":
    unittest.main()
