import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import tester


def live():
    progress = tester.blank()
    progress["started"] = 100.0
    return progress


class TestRank(unittest.TestCase):
    def test_best_first_and_gate_marked(self):
        rows = tester.rank({"Marcus": 0.81, "Diablo": 0.42, "captain": 0.7}, 0.7)
        self.assertEqual([r[0] for r in rows], ["Marcus", "captain", "Diablo"])
        self.assertEqual([r[2] for r in rows], [True, True, False])


class TestFold(unittest.TestCase):
    def test_first_sight_seeds_smooth_and_peak_with_raw(self):
        progress = live()
        tester.fold(progress, {"Marcus": 0.8, "Diablo": 0.3}, dt=0.1, now=10.0)
        self.assertEqual(progress["smooth"], {"Marcus": 0.8, "Diablo": 0.3})
        self.assertEqual(progress["peak"], {"Marcus": 0.8, "Diablo": 0.3})
        self.assertEqual(progress["ticks"], 1)
        self.assertEqual(progress["leader"], "Marcus")

    def test_smooth_moves_toward_raw_by_the_exponential_factor(self):
        progress = live()
        tester.fold(progress, {"Marcus": 0.0}, dt=0.1, now=10.0)
        tester.fold(progress, {"Marcus": 1.0}, dt=0.25, now=10.25)
        factor = 1 - math.exp(-0.25 / tester.TAU_S)
        self.assertAlmostEqual(progress["smooth"]["Marcus"], factor, places=6)

    def test_peak_holds_then_falls(self):
        progress = live()
        tester.fold(progress, {"Marcus": 0.9}, dt=0.1, now=10.0)
        tester.fold(progress, {"Marcus": 0.1}, dt=0.5, now=10.5)
        self.assertEqual(progress["peak"]["Marcus"], 0.9)
        tester.fold(progress, {"Marcus": 0.1}, dt=1.0, now=11.5)
        self.assertAlmostEqual(progress["peak"]["Marcus"], 0.9 - 0.5 * tester.PEAK_FALL_PER_S, places=6)

    def test_a_new_high_resets_the_hold(self):
        progress = live()
        tester.fold(progress, {"Marcus": 0.5}, dt=0.1, now=10.0)
        tester.fold(progress, {"Marcus": 0.95}, dt=2.0, now=12.0)
        self.assertEqual(progress["peak"]["Marcus"], 0.95)
        self.assertEqual(progress["peak_at"]["Marcus"], 12.0)

    def test_peak_never_falls_below_the_current_smooth(self):
        progress = live()
        tester.fold(progress, {"Marcus": 0.9}, dt=0.1, now=10.0)
        tester.fold(progress, {"Marcus": 0.9}, dt=10.0, now=20.0)
        self.assertGreaterEqual(progress["peak"]["Marcus"], progress["smooth"]["Marcus"])

    def test_lead_counts_the_top_smooth_each_tick(self):
        progress = live()
        tester.fold(progress, {"Marcus": 0.8, "Diablo": 0.3}, dt=0.1, now=10.0)
        tester.fold(progress, {"Marcus": 0.8, "Diablo": 0.3}, dt=0.1, now=10.1)
        tester.fold(progress, {"Marcus": 0.1, "Diablo": 0.9}, dt=100.0, now=110.1)
        self.assertEqual(progress["stats"]["Marcus"]["lead"], 2)
        self.assertEqual(progress["stats"]["Diablo"]["lead"], 1)
        self.assertEqual(progress["stats"]["Marcus"]["n"], 3)
        self.assertAlmostEqual(progress["stats"]["Marcus"]["sum"], 1.7)
        self.assertEqual(progress["stats"]["Marcus"]["max"], 0.8)
        self.assertEqual(progress["leader"], "Diablo")
        self.assertEqual(progress["ticks"], 3)


class TestState(unittest.TestCase):
    def test_idle_state_is_empty_but_complete(self):
        snap = tester.state(tester.blank(), {"voiceprint_min": 0.75})
        self.assertEqual(snap["rows"], [])
        self.assertEqual(snap["summary"], [])
        self.assertEqual(snap["leader"], "")
        self.assertIsNone(snap["margin"])
        self.assertEqual(snap["elapsed"], 0)
        self.assertEqual(snap["ticks"], 0)
        self.assertEqual(snap["minimum"], 0.75)
        self.assertFalse(snap["active"])

    def test_rows_sorted_by_smooth_with_margin_and_summary(self):
        progress = live()
        tester.fold(progress, {"Marcus": 0.81234, "Diablo": 0.4, "captain": 0.7}, dt=0.1, now=10.0)
        tester.fold(progress, {"Marcus": 0.81234, "Diablo": 0.4, "captain": 0.7}, dt=0.1, now=10.1)
        snap = tester.state(progress, {"voiceprint_min": 0.75})
        self.assertEqual(
            snap["rows"][0],
            {"name": "Marcus", "score": 0.812, "raw": 0.812, "peak": 0.812, "passes": True},
        )
        self.assertEqual([r["name"] for r in snap["rows"]], ["Marcus", "captain", "Diablo"])
        self.assertEqual([r["passes"] for r in snap["rows"]], [True, False, False])
        self.assertEqual(snap["leader"], "Marcus")
        self.assertEqual(snap["margin"], 0.112)
        self.assertEqual(snap["ticks"], 2)
        self.assertEqual(snap["summary"][0], {"name": "Marcus", "mean": 0.812, "max": 0.812, "lead": 1.0})
        self.assertEqual([s["name"] for s in snap["summary"]], ["Marcus", "captain", "Diablo"])
        self.assertEqual(snap["summary"][2]["lead"], 0.0)
        self.assertGreater(snap["elapsed"], 0)

    def test_margin_needs_two_voices(self):
        progress = live()
        tester.fold(progress, {"Marcus": 0.8}, dt=0.1, now=10.0)
        snap = tester.state(progress, {})
        self.assertIsNone(snap["margin"])
        self.assertEqual(snap["minimum"], 0.70)


if __name__ == "__main__":
    unittest.main(verbosity=2)
