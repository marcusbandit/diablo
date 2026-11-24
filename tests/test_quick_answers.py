import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import charter, db, fleetview, mcp_server


class TestPreloadedTools(unittest.TestCase):
    def test_the_quick_tools_skip_tool_search_and_the_rest_wait_for_it(self):
        listed = {tool["name"]: tool for tool in mcp_server.tools()}
        for name in mcp_server.ALWAYS_LOADED:
            self.assertIn(name, listed)
            self.assertEqual(listed[name].get("_meta"), {"anthropic/alwaysLoad": True}, name)
        deferred = [name for name, tool in listed.items() if "_meta" not in tool]
        self.assertIn("emit_object", deferred)
        self.assertGreater(len(deferred), len(mcp_server.ALWAYS_LOADED))

    def test_listing_the_tools_leaves_the_shared_definitions_alone(self):
        mcp_server.tools()
        self.assertFalse(any("_meta" in tool for tool in mcp_server.EXTRA))

    def test_the_charter_names_exactly_the_preloaded_tools(self):
        line = next(line for line in charter.CHARTER.splitlines() if "quick tools are loaded already" in line)
        named = line.split("loaded already: ", 1)[1].split(". ", 1)[0].replace(" and ", ", ")
        self.assertEqual({n.strip() for n in named.split(",")}, set(mcp_server.ALWAYS_LOADED))


class TestCheckFirst(unittest.TestCase):
    def test_the_charter_checks_before_it_says_it_does_not_know(self):
        text = charter.CHARTER
        self.assertIn("Check before you say you do not know.", text)
        self.assertIn("Weather and temperature: search the web at once", text)
        self.assertIn("Never call phone_status for the weather", text)
        self.assertIn("Ask which town only when no home town is saved", text)
        self.assertIn("keep them to a few plain sentences", text)

    def test_the_charter_stays_free_of_dashes(self):
        self.assertNotIn("—", charter.CHARTER)
        self.assertNotIn("–", charter.CHARTER)


class TestPhoneNeverReported(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.init(os.path.join(self.tmp.name, "hub.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_fresh_ask_does_not_wait_for_a_phone_that_never_reported(self):
        with mock.patch.object(fleetview.urllib.request, "urlopen") as urlopen, \
                mock.patch.object(fleetview, "_stored_phone", return_value=(None, 0.0)):
            started = time.monotonic()
            answer = fleetview.phone_status(fresh=True)
        self.assertEqual(answer, "the phone has not reported to the hub yet")
        self.assertLess(time.monotonic() - started, 1.0)
        urlopen.assert_called_once()

    def test_a_phone_that_reported_before_is_still_waited_for(self):
        calls = iter([({"ts": 1}, 10.0), ({"ts": 1}, 10.0), ({"ts": 2}, 20.0)])
        with mock.patch.object(fleetview.urllib.request, "urlopen"), \
                mock.patch.object(fleetview, "_stored_phone", side_effect=lambda: next(calls)), \
                mock.patch.object(fleetview.time, "sleep"), \
                mock.patch.object(fleetview, "phone_summary", side_effect=lambda row: row["received"]):
            self.assertEqual(fleetview.phone_status(fresh=True), 20.0)


if __name__ == "__main__":
    unittest.main()
