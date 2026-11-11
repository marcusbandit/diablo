"""Diablo on screen: quick answers with no model, the cards the orb draws, and the state it reads."""

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voice import presence, quick


class Reply:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


HOME = {"home_name": "Testby", "home_lat": 55.0, "home_lon": 12.0}
BUS = {"bus_stop_id": "000000001", "bus_stop_name": "Teststop", "bus_line": "42", "bus_toward": "Centrum"}


class TestQuick(unittest.TestCase):
    def test_no_home_or_stop_set_means_not_quick(self):
        self.assertIsNone(quick.weather_answer(cfg={}))
        self.assertIsNone(quick.bus_answer(cfg={}))

    def test_the_time_needs_nothing(self):
        words, card = quick.answer("What time is it?", now=datetime.datetime(2026, 11, 11, 5, 2))
        self.assertEqual(words, "It's 05:02.")
        self.assertEqual(card["big"], "05:02")

    def test_the_bus_reads_the_board_for_his_line_and_direction(self):
        now = datetime.datetime(2026, 11, 11, 7, 50)
        board = {"DepartureBoard": {"Departure": [
            {"line": "42", "direction": "Centrum St.", "time": "07:54", "date": "11.11.26", "rtTime": "07:55"},
            {"line": "42", "direction": "Havnen St.", "time": "07:52", "date": "11.11.26"},
            {"line": "7A", "direction": "Centrum St.", "time": "07:53", "date": "11.11.26"},
            {"line": "42", "direction": "Centrum St.", "time": "08:10", "date": "11.11.26"}]}}
        client = mock.Mock(get=mock.Mock(return_value=Reply(board)))
        words, card = quick.bus_answer(cfg=BUS, now=now, client=client)
        self.assertEqual(words, "The 42 to Centrum comes in 5 minutes, then 20.")
        self.assertEqual(card["big"], "5 min")
        self.assertNotIn("no live data", card["lines"][1])

    def test_the_weather_card_and_line(self):
        body = {"current": {"temperature_2m": 7.2, "apparent_temperature": 4.1, "weather_code": 3, "wind_speed_10m": 4},
                "daily": {"temperature_2m_min": [6, 4], "temperature_2m_max": [8, 8], "weather_code": [3, 61],
                          "precipitation_probability_max": [40, 71]}, "hourly": {}}
        client = mock.Mock(get=mock.Mock(return_value=Reply(body)))
        words, card = quick.weather_answer(cfg=HOME, client=client)
        self.assertEqual(words, "7 degrees and cloudy.")
        self.assertEqual(card["big"], "7°")
        words, card = quick.weather_answer(tomorrow=True, cfg=HOME, client=client)
        self.assertTrue(words.startswith("Tomorrow 4 to 8 degrees, light rain"))

    def test_other_lines_are_not_quick(self):
        for line in ("Turn off the lights", "Open the bus map in the browser please now", "Dictate."):
            self.assertIsNone(quick.answer(line), line)


class TestCards(unittest.TestCase):
    def test_cards_are_written_kept_short_and_expire(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "presence.json"
            with mock.patch.object(presence, "FILE", path), mock.patch.object(presence, "STATE_DIR", Path(tmp)):
                for n in range(5):
                    presence.show(f"card {n}", "1", ["a line"])
                cards = json.loads(path.read_text())["cards"]
                self.assertEqual([c["title"] for c in cards], ["card 2", "card 3", "card 4"])
                presence.show_reply("Spoken words to read along.")
                self.assertEqual(json.loads(path.read_text())["cards"][-1]["kind"], "reply")
                with mock.patch.object(presence.time, "time", return_value=cards[-1]["at"] + 3600):
                    self.assertEqual(presence._read(), [])


class TestOrbState(unittest.TestCase):
    def test_modes_stick_across_state_writes(self):
        from voice import ears

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "ears.json"
            with mock.patch.object(ears, "STATE_FILE", state), mock.patch.object(ears, "STATE_DIR", Path(tmp)), \
                    mock.patch.object(ears, "LIVE", None), mock.patch.object(ears, "REPORT", None), \
                    mock.patch.dict(ears.STICKY, {"loop": False, "dictation": False, "thinking": 0}):
                ears.set_state("listening", loop=True)
                ears.set_state("speaking")
                self.assertTrue(json.loads(state.read_text())["loop"])
                ears.set_state("speaking", loop=False)
                self.assertFalse(json.loads(state.read_text())["loop"])


if __name__ == "__main__":
    unittest.main()


class TestRecordings(unittest.TestCase):
    def test_each_request_is_kept_with_its_transcript_and_found_again(self):
        import numpy as np

        from voice import recordings

        clock = iter([1000.0, 1001.0, 1002.0, 1003.0, 1004.0, 1005.0])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(recordings, "DIR", Path(tmp)), \
                mock.patch.object(recordings, "KEEP", 2), \
                mock.patch.object(recordings.time, "time", side_effect=lambda: next(clock)):
            ids = [recordings.keep(np.zeros(16000, dtype=np.float32), f"line {n}") for n in range(3)]
            self.assertEqual(len(list(Path(tmp).glob("*.wav"))), 2)
            rows = recordings.listed()
            self.assertEqual([r["text"] for r in rows], ["line 1", "line 2"])
            self.assertEqual(recordings.path("last").name, f"{ids[-1]}.wav")
            self.assertIsNone(recordings.path("nope"))
