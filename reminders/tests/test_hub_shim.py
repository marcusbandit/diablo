"""The hub side: the shim Diablo's MCP server calls, run against the real CLI."""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT.parent / "hub" / "modules" / "reminders.py"


def load_shim(remind_bin):
    os.environ["DIABLO_REMIND_BIN"] = remind_bin
    sys.path.insert(0, str(ROOT.parent))
    import hub.modules.reminders as module

    module.REMIND = remind_bin
    module._cache.clear()
    return module


class Shim(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        db = os.path.join(self.dir.name, "reminders.db")
        wrapper = Path(self.dir.name) / "remind"
        wrapper.write_text(f"#!/bin/sh\nREMIND_DB='{db}' PYTHONPATH='{ROOT}' exec '{sys.executable}' -m diablo_reminders \"$@\"\n")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        self.shim = load_shim(str(wrapper))

    def tearDown(self):
        os.environ.pop("DIABLO_REMIND_BIN", None)
        self.dir.cleanup()

    def test_tools_round_trip(self):
        said = self.shim.call("add_reminder", {"text": "-dinner with Sofus", "due": "2099-11-08T13:00", "recurrence": "weekly"})
        self.assertIn("Reminder #1 set for Sunday 8 November 2099 at 13:00", said)
        self.assertIn("every Sunday at 13:00: -dinner with Sofus", said)
        self.assertIn("#1 Sunday 8 November 2099 at 13:00", self.shim.call("list_reminders", {}))
        self.assertIn("cancelled", self.shim.call("cancel_reminder", {"id": 1}))
        self.assertEqual(self.shim.call("list_reminders", {}), "No reminders are set.")
        self.assertIn("#1 cancelled", self.shim.call("list_reminders", {"include_past": True}))

    def test_bad_input_comes_back_as_a_sentence(self):
        self.assertTrue(self.shim.call("add_reminder", {"text": "x", "due": "whenever"}).startswith("not set: cannot read a time"))
        self.assertTrue(self.shim.call("cancel_reminder", {"id": 9}).startswith("not set: there is no reminder #9"))
        self.assertTrue(self.shim.call("cancel_reminder", {"id": "three"}).startswith("not set: the reminder id must be a number"))
        self.assertTrue(self.shim.call("cancel_reminder", {"id": "#9"}).startswith("not set: there is no reminder #9"))

    def test_missing_install_is_reported(self):
        shim = load_shim(os.path.join(self.dir.name, "nope"))
        self.assertIn("not installed", shim.call("list_reminders", {}))

    def test_schemas_are_mcp_shaped(self):
        names = [tool["name"] for tool in self.shim.TOOLS]
        self.assertEqual(tuple(names), self.shim.NAMES)
        for tool in self.shim.TOOLS:
            self.assertEqual(tool["inputSchema"]["type"], "object")


class Text(unittest.TestCase):
    def test_no_dashes_anywhere_we_wrote(self):
        for path in [*ROOT.glob("diablo_reminders/*.py"), SHIM, *ROOT.glob("deploy/*.*"), *ROOT.glob("completions/*"), ROOT / "README.md"]:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path)
            self.assertNotIn("\u2013", text, path)


if __name__ == "__main__":
    unittest.main()
