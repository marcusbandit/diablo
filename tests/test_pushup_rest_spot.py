"""The rest bubble can be dragged anywhere and opens where it was left (#146)."""
import os
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
class TestRestSpot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(pushup_ui, "REST_SPOT", Path(self.tmp.name) / "pushup" / "rest_spot.json")
        patch.start()
        self.addCleanup(patch.stop)

    def test_first_rest_sits_in_the_corner(self):
        self.assertEqual(pushup_ui.load_rest_spot(), (pushup_ui.BUBBLE_MARGIN, pushup_ui.BUBBLE_MARGIN))

    def test_a_dragged_spot_comes_back(self):
        pushup_ui.save_rest_spot(310, 1200)
        self.assertEqual(pushup_ui.load_rest_spot(), (310, 1200))

    def test_a_broken_file_falls_back_to_the_corner(self):
        pushup_ui.REST_SPOT.parent.mkdir(parents=True)
        pushup_ui.REST_SPOT.write_text("{not json")
        self.assertEqual(pushup_ui.load_rest_spot(), (pushup_ui.BUBBLE_MARGIN, pushup_ui.BUBBLE_MARGIN))

    def test_the_bubble_stays_whole_on_screen(self):
        size, area = (660, 130), (5120, 1440)
        self.assertEqual(pushup_ui.clamp_spot(-40, -10, size, area), (0, 0))
        self.assertEqual(pushup_ui.clamp_spot(5000, 9000, size, area), (1440 - 130, 5120 - 660))
        self.assertEqual(pushup_ui.clamp_spot(400, 2000, size, area), (400, 2000))

    def test_bars_take_their_edges_off_the_area(self):
        self.assertEqual(pushup_ui.usable_area(5120, 1440, (32, 10, 10, 10)), (5078, 1420))

    def test_no_hyprland_means_no_cursor(self):
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": ""}):
            self.assertIsNone(pushup_ui.cursor_pos())


if __name__ == "__main__":
    unittest.main()
