import unittest
from datetime import datetime, timezone

from diablo_reminders import schedule as s


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


NOW = utc(2026, 11, 3, 19, 0)  # Tuesday 3 November 2026, 20:00 in Copenhagen


class ParseDue(unittest.TestCase):
    def test_naive_iso_is_copenhagen(self):
        wall = s.parse_due("2026-11-08T13:00", NOW)
        self.assertEqual(wall, datetime(2026, 11, 8, 13, 0))
        self.assertEqual(s.to_utc(wall), utc(2026, 11, 8, 12, 0))

    def test_space_separator_and_seconds(self):
        self.assertEqual(s.parse_due("2026-11-08 13:00:45", NOW), datetime(2026, 11, 8, 13, 0))

    def test_offset_is_honoured(self):
        self.assertEqual(s.parse_due("2026-11-08T12:00Z", NOW), datetime(2026, 11, 8, 13, 0))
        self.assertEqual(s.parse_due("2026-07-01T09:00+00:00", NOW), datetime(2026, 7, 1, 11, 0))

    def test_clock_time_today_or_tomorrow(self):
        self.assertEqual(s.parse_due("21:00", NOW), datetime(2026, 11, 3, 21, 0))
        self.assertEqual(s.parse_due("19:30", NOW), datetime(2026, 11, 4, 19, 30))

    def test_relative(self):
        self.assertEqual(s.parse_due("+20m", NOW), datetime(2026, 11, 3, 20, 20))
        self.assertEqual(s.parse_due("+1h30m", NOW), datetime(2026, 11, 3, 21, 30))
        self.assertEqual(s.parse_due("+2d", NOW), datetime(2026, 11, 5, 20, 0))

    def test_relative_rounds_up_to_the_minute(self):
        now = utc(2026, 11, 3, 19, 0, 30)
        self.assertEqual(s.parse_due("+1m", now), datetime(2026, 11, 3, 20, 2))

    def test_rejects_rubbish(self):
        for bad in ("", "soon", "2026-11-08", "25:00", "+0m"):
            with self.assertRaises(ValueError, msg=bad):
                s.parse_due(bad, NOW)

    def test_recurrence_names(self):
        self.assertEqual(s.normalize_recurrence(None), "none")
        self.assertEqual(s.normalize_recurrence("Weekday"), "weekdays")
        with self.assertRaises(ValueError):
            s.normalize_recurrence("monthly")


class Recurrence(unittest.TestCase):
    def test_one_off_has_no_next(self):
        anchor = datetime(2026, 11, 8, 13, 0)
        self.assertEqual(s.next_after(anchor, "none", NOW), anchor)
        self.assertIsNone(s.next_after(anchor, "none", s.to_utc(anchor)))

    def test_daily(self):
        anchor = datetime(2026, 11, 1, 9, 0)
        self.assertEqual(s.next_after(anchor, "daily", NOW), datetime(2026, 11, 4, 9, 0))

    def test_weekly_keeps_the_weekday(self):
        anchor = datetime(2026, 11, 1, 13, 0)  # a Sunday
        self.assertEqual(s.next_after(anchor, "weekly", NOW), datetime(2026, 11, 8, 13, 0))

    def test_weekdays_skip_the_weekend(self):
        anchor = datetime(2026, 11, 2, 8, 30)  # Monday
        friday = s.to_utc(datetime(2026, 11, 6, 8, 30))
        self.assertEqual(s.next_after(anchor, "weekdays", friday), datetime(2026, 11, 9, 8, 30))

    def test_weekend_start_moves_to_monday(self):
        self.assertEqual(s.first_occurrence(datetime(2026, 11, 7, 8, 30), "weekdays"), datetime(2026, 11, 9, 8, 30))
        self.assertEqual(s.first_occurrence(datetime(2026, 11, 7, 8, 30), "daily"), datetime(2026, 11, 7, 8, 30))

    def test_far_behind_cursor_is_fast(self):
        anchor = datetime(2020, 1, 1, 9, 0)
        self.assertEqual(s.next_after(anchor, "daily", NOW), datetime(2026, 11, 4, 9, 0))

    def test_count_between(self):
        anchor = datetime(2026, 11, 1, 9, 0)
        start = s.to_utc(anchor)
        self.assertEqual(s.occurrences_between(anchor, "daily", start, NOW), (2, datetime(2026, 11, 3, 9, 0)))
        self.assertEqual(s.occurrences_between(anchor, "none", start, NOW), (0, None))


class DST(unittest.TestCase):
    """2026: summer time starts 29 March at 02:00, ends 25 October at 03:00."""

    def test_offsets_on_each_side(self):
        self.assertEqual(s.to_utc(datetime(2026, 3, 28, 13, 0)), utc(2026, 3, 28, 12, 0))
        self.assertEqual(s.to_utc(datetime(2026, 3, 29, 13, 0)), utc(2026, 3, 29, 11, 0))
        self.assertEqual(s.to_utc(datetime(2026, 10, 24, 13, 0)), utc(2026, 10, 24, 11, 0))
        self.assertEqual(s.to_utc(datetime(2026, 10, 25, 13, 0)), utc(2026, 10, 25, 12, 0))

    def test_daily_keeps_wall_time_across_spring_forward(self):
        anchor = datetime(2026, 3, 27, 13, 0)
        after = s.to_utc(datetime(2026, 3, 28, 13, 0))
        nxt = s.next_after(anchor, "daily", after)
        self.assertEqual(nxt, datetime(2026, 3, 29, 13, 0))
        self.assertEqual(s.to_utc(nxt) - after, s.to_utc(datetime(2026, 3, 29, 13, 0)) - after)
        self.assertEqual((s.to_utc(nxt) - after).total_seconds(), 23 * 3600)

    def test_weekly_keeps_wall_time_across_fall_back(self):
        anchor = datetime(2026, 10, 18, 13, 0)
        nxt = s.next_after(anchor, "weekly", s.to_utc(anchor))
        self.assertEqual(nxt, datetime(2026, 10, 25, 13, 0))
        self.assertEqual((s.to_utc(nxt) - s.to_utc(anchor)).total_seconds(), 7 * 86400 + 3600)

    def test_time_in_spring_gap_fires_an_hour_on(self):
        gap = datetime(2026, 3, 29, 2, 30)
        self.assertEqual(s.to_utc(gap), utc(2026, 3, 29, 1, 30))
        self.assertEqual(s.effective(gap), datetime(2026, 3, 29, 3, 30))
        self.assertIn("03:30", s.describe(gap, NOW))

    def test_daily_through_the_gap_fires_every_day(self):
        anchor = datetime(2026, 3, 28, 2, 30)
        first = s.to_utc(anchor)
        second = s.to_utc(s.next_after(anchor, "daily", first))
        third = s.to_utc(s.next_after(anchor, "daily", second))
        self.assertEqual(second, utc(2026, 3, 29, 1, 30))
        self.assertEqual(third, utc(2026, 3, 30, 0, 30))

    def test_time_in_autumn_overlap_fires_once_at_the_first(self):
        anchor = datetime(2026, 10, 24, 2, 30)
        sat = s.to_utc(anchor)
        sun = s.to_utc(s.next_after(anchor, "daily", sat))
        mon = s.to_utc(s.next_after(anchor, "daily", sun))
        self.assertEqual(sun, utc(2026, 10, 25, 0, 30))  # 02:30 summer time
        self.assertEqual(mon, utc(2026, 10, 26, 1, 30))
        self.assertEqual(s.occurrences_between(anchor, "daily", sat, mon)[0], 2)

    def test_relative_time_into_the_overlap_keeps_its_instant(self):
        before = utc(2026, 10, 24, 23, 50)  # 01:50 summer time, an hour before the overlap
        wall = s.parse_due("+2h", before)
        self.assertEqual(s.to_utc(wall), utc(2026, 10, 25, 1, 50))  # the second 02:50, winter time
        during = utc(2026, 10, 25, 1, 10)  # the second 02:10
        self.assertEqual(s.to_utc(s.parse_due("+20m", during)), utc(2026, 10, 25, 1, 30))

    def test_offset_time_in_the_overlap_keeps_its_instant(self):
        wall = s.parse_due("2026-10-25T02:30+01:00", NOW)
        self.assertEqual(s.to_utc(wall), utc(2026, 10, 25, 1, 30))


class Words(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(s.describe(datetime(2026, 11, 8, 13, 0), NOW), "Sunday 8 November at 13:00")
        self.assertEqual(s.describe(datetime(2027, 1, 4, 9, 5), NOW), "Monday 4 January 2027 at 09:05")

    def test_describe_recurrence(self):
        sunday = datetime(2026, 11, 8, 13, 0)
        self.assertEqual(s.describe_recurrence("weekly", sunday), "every Sunday at 13:00")
        self.assertEqual(s.describe_recurrence("weekdays", sunday), "every weekday at 13:00")


if __name__ == "__main__":
    unittest.main()
