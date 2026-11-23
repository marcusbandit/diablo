import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import saying

SAY_AS = {"DIABLO_SAY_AS": "yamabox=ヤマボックス, kumo=クモ"}


class TestForSpeech(unittest.TestCase):
    def test_listed_words_go_to_their_reading(self):
        with mock.patch.dict(os.environ, SAY_AS):
            self.assertEqual(saying.for_speech("Running on yamabox."), "Running on ヤマボックス.")
            self.assertEqual(saying.for_speech("Kumo is offline"), "クモ is offline")

    def test_other_words_stay(self):
        with mock.patch.dict(os.environ, SAY_AS):
            for said in ("Running on workbox.", "yamaboxes is not a host", "", None):
                self.assertEqual(saying.for_speech(said), said)

    def test_nothing_listed_changes_nothing(self):
        with mock.patch.dict(os.environ, {"DIABLO_SAY_AS": ""}), \
                mock.patch.object(saying.localenv, "load", return_value={}):
            self.assertEqual(saying.for_speech("Running on yamabox."), "Running on yamabox.")


if __name__ == "__main__":
    unittest.main()
