import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import db, fleetview

NOW = 1_800_000_000.0


def report(**overrides):
    base = {
        "device": "Pixel 9",
        "ts": NOW - 90,
        "battery": {"level": 63, "charging": True, "status": "charging"},
        "location": {
            "lat": 55.000123, "lon": 12.003456, "accuracy_m": 12.4, "altitude_m": 30.0,
            "speed_mps": 0.0, "bearing": 0.0, "provider": "fused", "ts": NOW - 300,
            "place": "Testby",
        },
        "time": {"local": "2027-01-15 14:30", "tz": "Europe/Copenhagen", "uptime_s": 4000},
        "network": {"type": "wifi", "ssid": "TestNet", "tailscale_ip": "100.64.0.7"},
        "device_info": {"model": "Pixel 9", "manufacturer": "Google", "android": "16", "app_version": "0.3"},
        "storage": {"free_mb": 51200, "total_mb": 131072},
        "memory": {"free_mb": 2048, "total_mb": 8192},
        "screen_on": False,
        "permissions": {"location": True},
    }
    base.update(overrides)
    return base


class TestAge(unittest.TestCase):
    def test_units_climb(self):
        self.assertEqual(fleetview.fmt_age(None), "unknown age")
        self.assertEqual(fleetview.fmt_age(-3), "0 s")
        self.assertEqual(fleetview.fmt_age(42), "42 s")
        self.assertEqual(fleetview.fmt_age(150), "2 min")
        self.assertEqual(fleetview.fmt_age(3600), "1 h")
        self.assertEqual(fleetview.fmt_age(3600 * 2 + 300), "2 h 5 min")
        self.assertEqual(fleetview.fmt_age(86400 * 3), "3 d")
        self.assertEqual(fleetview.fmt_age(86400 + 7200), "1 d 2 h")


class TestPhoneSummary(unittest.TestCase):
    def test_full_report(self):
        payload = {"report": report(), "received": NOW - 90, "age": 90, "fresh": True}
        text = fleetview.phone_summary(payload, now=NOW)
        self.assertEqual(
            text,
            "Pixel 9, report 1 min old; battery 63%, charging; "
            "location Testby (55.00012, 12.00346, ±12 m), 5 min old; "
            "local time 2027-01-15 14:30 Europe/Copenhagen; network wifi (TestNet); "
            "screen off; storage 50.0 GB free of 128.0 GB",
        )

    def test_no_report_is_said_plainly(self):
        empty = {"report": None, "received": None, "age": None, "fresh": False}
        self.assertEqual(fleetview.phone_summary(empty), fleetview.NO_REPORT)
        self.assertEqual(fleetview.phone_summary(None), fleetview.NO_REPORT)
        self.assertEqual(fleetview.phone_summary({"detail": "not found"}), fleetview.NO_REPORT)

    def test_no_location(self):
        payload = {"report": report(location=None, screen_on=True), "age": 7200, "fresh": False}
        text = fleetview.phone_summary(payload, now=NOW)
        self.assertTrue(text.startswith("Pixel 9, report 2 h old; "))
        self.assertIn("; no location shared; ", text)
        self.assertIn("; screen on; ", text)
        self.assertNotIn("±", text)

    def test_age_falls_back_to_the_report_stamp(self):
        payload = {"report": report(ts=(NOW - 600) * 1000, location=None)}
        self.assertIn("report 10 min old", fleetview.phone_summary(payload, now=NOW))

    def test_thin_report_uses_the_model_and_skips_missing_parts(self):
        thin = {"device_info": {"model": "Fairphone"}, "battery": {"level": 5}}
        text = fleetview.phone_summary({"report": thin, "age": 3}, now=NOW)
        self.assertEqual(text, "Fairphone, report 3 s old; battery 5%; no location shared")


class TestWhereSummary(unittest.TestCase):
    def state(self, desktop_online=True):
        return {
            "devices": [
                {"name": "homehub", "role": "hub", "online": True, "self": True},
                {"name": "banditbox", "role": "desktop", "online": desktop_online, "self": False},
                {"name": "kangaeru", "role": "laptop", "online": True, "self": False},
                {"name": "computer", "role": "other", "online": False, "self": False},
            ],
            "rtt": {"banditbox": 3.6, "kangaeru": 41.2},
        }

    def test_desktop_up(self):
        seat = {"mode": "desktop", "at": "banditbox", "reason": "voice loop active"}
        self.assertEqual(
            fleetview.where_summary(seat, self.state()),
            "Diablo attends at banditbox (desktop, voice loop active). banditbox is reachable (4 ms). "
            "Online: kangaeru; offline: computer.",
        )

    def test_desktop_down_and_no_seat(self):
        text = fleetview.where_summary({}, self.state(desktop_online=False))
        self.assertEqual(
            text,
            "The fleet viewer does not report where Diablo attends. banditbox is down. "
            "Online: kangaeru; offline: computer.",
        )

    def test_desktop_is_self(self):
        state = {"devices": [{"name": "banditbox", "online": True, "self": True}]}
        text = fleetview.where_summary({"mode": "desktop", "at": "banditbox"}, state)
        self.assertEqual(text, "Diablo attends at banditbox (desktop). banditbox is this very machine. Online: nobody.")

    def test_desktop_reports_itself_under_self(self):
        state = {
            "self": {"name": "banditbox", "ip": "100.64.0.5"},
            "devices": [{"name": "homehub", "online": True}, {"name": "shared", "online": False}, {"name": "shared", "online": False}],
        }
        text = fleetview.where_summary({"mode": "auto", "at": "banditbox", "reason": "voice loop here"}, state)
        self.assertEqual(
            text,
            "Diablo attends at banditbox (auto, voice loop here). banditbox is this very machine. "
            "Online: homehub; offline: shared.",
        )

    def test_garbage_in(self):
        self.assertEqual(
            fleetview.where_summary("nope", None),
            "The fleet viewer does not report where Diablo attends. banditbox is not in the fleet list. Online: nobody.",
        )


class TestPowerLine(unittest.TestCase):
    def test_sent_and_failed(self):
        self.assertEqual(fleetview.power_line("banditbox", "wake", {"ok": True, "detail": "magic packet sent"}), "wake sent to banditbox: magic packet sent")
        self.assertEqual(fleetview.power_line("homehub", "shutdown", {"ok": True}), "shutdown sent to homehub")
        self.assertEqual(fleetview.power_line("kangaeru", "reboot", {"ok": False, "detail": "no ssh"}), "reboot for kangaeru failed: no ssh")
        self.assertEqual(fleetview.power_line("x", "wake", None), "wake for x failed")


class TestGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))

    def test_power_refuses_bad_input_without_a_request(self):
        with mock.patch.object(fleetview, "_request") as request:
            self.assertEqual(fleetview.fleet_power("", "wake"), "which host?")
            self.assertEqual(fleetview.fleet_power("banditbox", "explode"), "action must be wake, shutdown or reboot")
            request.assert_not_called()

    def test_unreachable_viewer_is_a_sentence(self):
        with mock.patch.dict(os.environ, {"DIABLO_FLEET_URL": "http://127.0.0.1:1/"}):
            self.assertEqual(fleetview.fleet_url(), "http://127.0.0.1:1")
            self.assertEqual(fleetview.phone_status(), "the phone has not reported to the hub yet")
            self.assertTrue(fleetview.fleet_where().startswith("the fleet viewer at http://127.0.0.1:1 did not answer"))
            self.assertTrue(fleetview.fleet_power("banditbox", "wake").startswith("the fleet viewer at http://127.0.0.1:1 did not answer"))

    def test_tools_route_through_the_module(self):
        from hub import agent

        with mock.patch.object(fleetview, "_request", return_value={"report": None, "age": None}):
            self.assertEqual(agent._call_tool("phone_status", {}), "the phone has not reported to the hub yet")
        with mock.patch.object(fleetview, "_request", return_value={"ok": True, "detail": "sent"}):
            self.assertEqual(agent._call_tool("fleet_power", {"host": "banditbox", "action": "wake"}), "wake sent to banditbox: sent")
        names = [tool["function"]["name"] for tool in agent.TOOLS]
        for name in ("phone_status", "fleet_power", "fleet_where"):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
