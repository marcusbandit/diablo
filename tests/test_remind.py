import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import remind
from remind import parse_when


def mktime(y, mo, d, h, mi, s=0):
    return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))


class TestParseWhen(unittest.TestCase):
    def test_relative_minutes(self):
        self.assertAlmostEqual(parse_when("+20m", now=1000.0), 2200.0, delta=0.001)

    def test_relative_naked(self):
        self.assertAlmostEqual(parse_when("5m", now=1000.0), 1300.0, delta=0.001)

    def test_relative_combined(self):
        self.assertAlmostEqual(parse_when("+1h30m", now=0.0), 5400.0, delta=0.001)

    def test_clock_later_today(self):
        now = mktime(2026, 9, 13, 10, 0)
        self.assertEqual(parse_when("14:30", now=now), mktime(2026, 9, 13, 14, 30))

    def test_clock_tomorrow_when_past(self):
        now = mktime(2026, 9, 13, 15, 0)
        self.assertEqual(parse_when("14:30", now=now), mktime(2026, 9, 14, 14, 30))

    def test_clock_exactly_now_becomes_tomorrow(self):
        now = mktime(2026, 9, 13, 14, 30)
        self.assertEqual(parse_when("14:30", now=now), mktime(2026, 9, 14, 14, 30))

    def test_clock_with_seconds(self):
        now = mktime(2026, 9, 13, 1, 0)
        self.assertEqual(parse_when("1:05:09", now=now), mktime(2026, 9, 13, 1, 5, 9))

    def test_datetime_explicit(self):
        self.assertEqual(
            parse_when("2026-09-14 09:05", now=0.0), mktime(2026, 9, 14, 9, 5)
        )

    def test_datetime_explicit_with_seconds(self):
        self.assertEqual(
            parse_when("2026-09-14T09:05:30", now=0.0), mktime(2026, 9, 14, 9, 5, 30)
        )

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_when("sometime soon", now=0.0)

    def test_impossible_clock_raises(self):
        with self.assertRaises(ValueError):
            parse_when("25:61", now=0.0)

    def test_impossible_date_raises(self):
        with self.assertRaises(ValueError):
            parse_when("2026-13-40 09:00", now=0.0)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "reminders.json"

    def test_add_and_pending_roundtrip(self):
        r = remind.add(2000.0, "the tea", path=self.path)
        self.assertEqual([p["id"] for p in remind.pending(path=self.path)], [r["id"]])
        self.assertEqual(remind.pending(path=self.path)[0]["message"], "the tea")

    def test_pending_sorted_by_when(self):
        remind.add(9000.0, "later", path=self.path)
        remind.add(1000.0, "sooner", path=self.path)
        self.assertEqual(
            [r["message"] for r in remind.pending(path=self.path)], ["sooner", "later"]
        )

    def test_due_only_past_pending(self):
        past = remind.add(1000.0, "past", path=self.path)
        remind.add(9000.0, "future", path=self.path)
        due = remind.due(now=5000.0, path=self.path)
        self.assertEqual([d["id"] for d in due], [past["id"]])

    def test_tick_fires_and_marks(self):
        remind.add(1000.0, "speak", path=self.path)
        spoken = []
        served = remind.tick(
            now=5000.0, path=self.path, announce=lambda t: spoken.append(t) or True
        )
        self.assertEqual(served, 1)
        self.assertEqual(spoken, ["Sir, a reminder: speak"])
        self.assertEqual(remind.pending(path=self.path), [])
        self.assertEqual(remind.load(self.path)[0]["status"], "fired")

    def test_tick_retries_when_voice_is_down(self):
        remind.add(1000.0, "speak", path=self.path)
        served = remind.tick(now=5000.0, path=self.path, announce=lambda t: False)
        self.assertEqual(served, 0)
        self.assertEqual(len(remind.pending(path=self.path)), 1)
        self.assertEqual(remind.load(self.path)[0]["attempts"], 1)

    def test_tick_gives_up_after_max_attempts(self):
        r = remind.add(1000.0, "speak", path=self.path)
        items = remind.load(self.path)
        items[0]["attempts"] = remind.MAX_ATTEMPTS
        remind.save(items, path=self.path)
        served = remind.tick(now=5000.0, path=self.path, announce=lambda t: False)
        self.assertEqual(served, 0)
        self.assertEqual(remind.pending(path=self.path), [])
        self.assertEqual(remind.load(self.path)[0]["status"], "missed")

    def test_cancel_removes_pending(self):
        r = remind.add(1000.0, "tea", path=self.path)
        self.assertTrue(remind.cancel(r["id"], path=self.path))
        self.assertEqual(remind.pending(path=self.path), [])

    def test_cancel_prefix_ambiguous_fails(self):
        remind.add(1000.0, "one", path=self.path)
        remind.add(2000.0, "two", path=self.path)
        self.assertFalse(remind.cancel("", path=self.path))
        self.assertEqual(len(remind.pending(path=self.path)), 2)

    def test_cancel_missing_id_fails(self):
        remind.add(1000.0, "one", path=self.path)
        self.assertFalse(remind.cancel("deadbeef", path=self.path))

    def test_survives_reload(self):
        r = remind.add(1234.0, "outlive the restart", path=self.path)
        again = remind.load(self.path)
        self.assertEqual(again[0]["id"], r["id"])
        self.assertEqual(again[0]["message"], "outlive the restart")


if __name__ == "__main__":
    unittest.main(verbosity=2)
