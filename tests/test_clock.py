"""The now tool: home time as before, the local time in a named place, and no guess for an unknown one."""

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import agent, clock, mcp_server
from voice import quick

WINTER = datetime.datetime(2026, 11, 26, 12, 30, tzinfo=datetime.timezone.utc)
SUMMER = datetime.datetime(2026, 7, 1, 12, 30, tzinfo=datetime.timezone.utc)


class TestResolve(unittest.TestCase):
    def test_quebec_and_montreal_use_eastern_time(self):
        for place in ("Quebec", "quebec city", "Montreal", "Montréal", "Montreal, Canada", "in Quebec"):
            self.assertEqual(clock.resolve(place), "America/Toronto", place)

    def test_cities_resolve_from_the_zone_database(self):
        self.assertEqual(clock.resolve("Tokyo"), "Asia/Tokyo")
        self.assertEqual(clock.resolve("Copenhagen"), "Europe/Copenhagen")
        self.assertEqual(clock.resolve("new york"), "America/New_York")
        self.assertEqual(clock.resolve("Asia/Kolkata"), "Asia/Kolkata")

    def test_an_unknown_place_or_a_country_that_spans_zones_is_not_guessed(self):
        for place in ("Atlantis", "Canada", "Testby", "", None):
            self.assertIsNone(clock.resolve(place), place)


class TestNow(unittest.TestCase):
    def test_no_place_is_home_time_exactly_as_before(self):
        moment = datetime.datetime(2026, 11, 26, 13, 30, 5)
        self.assertEqual(clock.now(at=moment), "2026-11-26 13:30:05")
        self.assertEqual(clock.now("", at=moment), "2026-11-26 13:30:05")
        self.assertRegex(clock.now(), r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_quebec_gets_eastern_time_with_weekday_date_and_zone(self):
        self.assertEqual(clock.now("Quebec", at=WINTER),
                         "Quebec: Thursday 26 November 2026, 07:30, America/Toronto (EST).")
        self.assertEqual(clock.now("Quebec", at=SUMMER),
                         "Quebec: Wednesday 1 July 2026, 08:30, America/Toronto (EDT).")

    def test_tokyo_and_copenhagen(self):
        self.assertEqual(clock.now("Tokyo", at=WINTER), "Tokyo: Thursday 26 November 2026, 21:30, Asia/Tokyo (JST).")
        self.assertEqual(clock.now("Copenhagen", at=WINTER),
                         "Copenhagen: Thursday 26 November 2026, 13:30, Europe/Copenhagen (CET).")

    def test_an_unknown_place_says_so_plainly(self):
        self.assertEqual(clock.now("Atlantis", at=WINTER), "I do not know which time zone Atlantis is in.")

    def test_the_reply_stays_short_enough_to_speak(self):
        self.assertLess(len(clock.now("Quebec City", at=WINTER).split()), 12)


class TestTool(unittest.TestCase):
    def test_the_tool_takes_an_optional_place(self):
        listed = {tool["name"]: tool for tool in mcp_server.tools()}
        schema = listed["now"]["inputSchema"]
        self.assertIn("place", schema["properties"])
        self.assertNotIn("place", schema.get("required", []))
        self.assertIn("now", mcp_server.ALWAYS_LOADED)

    def test_the_tool_answers_with_and_without_a_place(self):
        self.assertRegex(agent._call_tool("now", {}), r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertIn("America/Toronto", agent._call_tool("now", {"place": "Quebec"}))
        self.assertEqual(agent._call_tool("now", {"place": "Atlantis"}), "I do not know which time zone Atlantis is in.")


class TestQuickVoice(unittest.TestCase):
    def test_a_named_place_gets_that_place_time(self):
        words, card = quick.place_time_answer("quebec", at=WINTER)
        self.assertEqual(words, "It's 07:30 in Quebec.")
        self.assertEqual(card["lines"], ["Thursday 26 November", "America/Toronto"])
        words, card = quick.answer("What time is it in Quebec?")
        self.assertTrue(words.endswith("in Quebec."))

    def test_no_place_or_home_is_the_local_clock(self):
        moment = datetime.datetime(2026, 11, 11, 5, 2)
        self.assertEqual(quick.answer("What time is it?", now=moment)[0], "It's 05:02.")
        self.assertEqual(quick.answer("What time is it at home?", now=moment)[0], "It's 05:02.")

    def test_an_unknown_place_goes_on_to_the_hub(self):
        self.assertIsNone(quick.answer("What time is it in Atlantis?"))


if __name__ == "__main__":
    unittest.main()
