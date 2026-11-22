import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PUSHUP = Path(__file__).resolve().parent.parent / "pushup"


def load_pushup(home):
    with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                                      "XDG_STATE_HOME": str(home / ".local/state")}):
        if str(PUSHUP) not in sys.path:
            sys.path.insert(0, str(PUSHUP))
        loader = importlib.machinery.SourceFileLoader("pushup_under_test", str(PUSHUP / "pushup"))
        spec = importlib.util.spec_from_loader("pushup_under_test", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    return module


class TestOldRowNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.m = load_pushup(self.home)

    def write_log(self, *entries):
        self.m.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.m.LOG.write_text("".join(json.dumps(e) + "\n" for e in entries))

    def test_an_old_body_rows_set_reads_and_counts_as_inverted_rows(self):
        now = time.time()
        self.write_log({"ts": now - 600, "exercise": "body rows", "reps": 8, "did": 8, "result": "done"})
        [entry] = self.m.read_local()
        self.assertEqual(entry["exercise"], "inverted rows")
        overview = self.m.overview(None, now)
        self.assertIn("inverted rows", overview["today"]["line"])
        self.assertEqual(overview["timeline"]["sets"]["pull"][-1], 1)
        self.assertIn('"body rows"', self.m.LOG.read_text())

    def test_old_inbox_lines_and_config_picks_take_the_new_name_once(self):
        now = time.time()
        line = self.m.inbox_entry({"id": "x-1", "ts": now - 60, "result": "manual", "exercise": "body rows", "did": 6}, now)
        self.assertEqual(line["exercise"], "inverted rows")
        self.m.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.m.CONFIG_FILE.write_text('exercises = ["body rows", "inverted rows", "push-ups"]\n')
        self.assertEqual(self.m.load_config()["exercises"], ["inverted rows", "push-ups"])

    def test_every_spelling_of_the_row_finds_inverted_rows(self):
        import catalog
        for text in ("rows", "body rows", "inverted row", "supine row", "australian pull-up", "table rows"):
            self.assertEqual(catalog.find(text), "inverted rows", text)


if __name__ == "__main__":
    unittest.main()
