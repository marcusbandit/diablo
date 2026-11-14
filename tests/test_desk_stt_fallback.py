import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import stt


def answer(code, url, body=None):
    return httpx.Response(code, json=body or {}, request=httpx.Request("GET", url))


class DeskFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.wav = root / "clip.wav"
        self.wav.write_bytes(b"RIFF0000WAVE")
        token = root / "token"
        token.write_text("g")
        self.cfg = {"hub_url": "http://hub.example.com:8755", "hub_token": "t", "stt_via": "gvoice",
                    "gvoice_token_path": str(token), "gvoice_base": "http://127.0.0.1:8756"}
        stt._local_served()
        self.addCleanup(stt._local_served)
        self.gets, self.posts = [], []
        self.local = lambda url: answer(200, url, {"text": " from the gpu "})
        self.hub = lambda url: answer(200, url, {"text": " from groq ", "backend": "cloud"})

    def get(self, url, headers=None, timeout=None):
        self.gets.append({"url": url, "timeout": timeout})
        return self.local(url)

    def post(self, url, headers=None, content=None, timeout=None):
        self.posts.append({"url": url, "headers": headers, "content": content})
        return self.hub(url)

    def run_it(self, fallback=True):
        with mock.patch.object(httpx, "get", self.get), mock.patch.object(httpx, "post", self.post), \
                mock.patch.object(stt.time, "sleep"):
            return stt.transcribe(str(self.wav), self.cfg, fallback)

    def down(self, url):
        raise httpx.ConnectError("refused")

    def test_local_serves_and_the_hub_is_never_asked(self):
        with self.assertLogs("diablo.stt", "INFO") as logs:
            self.assertEqual(self.run_it(), ("from the gpu", "local"))
        self.assertEqual(self.posts, [])
        self.assertIn("stt served by local", logs.output[0])

    def test_local_has_a_short_read_timeout(self):
        self.run_it()
        self.assertEqual(self.gets[0]["timeout"].read, stt.LOCAL_TIMEOUT_S)
        self.assertLessEqual(stt.LOCAL_TIMEOUT_S, 10)

    def test_a_stopped_gvoice_hands_the_clip_to_the_hub_without_local(self):
        self.local = self.down
        with self.assertLogs("diablo.stt", "WARNING") as logs:
            self.assertEqual(self.run_it(), ("from groq", "hub cloud"))
        self.assertEqual(self.posts[0]["url"], "http://hub.example.com:8755/v1/stt")
        self.assertEqual(self.posts[0]["headers"]["X-Stt-Skip"], "local")
        self.assertEqual(self.posts[0]["content"], b"RIFF0000WAVE")
        self.assertIn("stt served by hub cloud", logs.output[0])
        self.assertIn("ConnectError", logs.output[0])

    def test_a_hung_gvoice_falls_back(self):
        def hung(url):
            raise httpx.ReadTimeout("hung")

        self.local = hung
        self.assertEqual(self.run_it()[1], "hub cloud")
        self.assertEqual(len(self.gets), 1)

    def test_a_gvoice_error_falls_back(self):
        self.local = lambda url: answer(500, url)
        self.assertEqual(self.run_it()[1], "hub cloud")

    def test_a_missing_token_falls_back(self):
        self.cfg["gvoice_token_path"] = str(Path(self.tmp.name) / "none")
        self.assertEqual(self.run_it()[1], "hub cloud")

    def test_after_a_failure_local_is_skipped_for_a_while(self):
        self.local = self.down
        self.run_it()
        self.run_it()
        self.assertEqual(len(self.gets), 1)
        self.assertEqual(len(self.posts), 2)

    def test_local_is_tried_again_after_the_backoff(self):
        self.local = self.down
        self.run_it()
        stt._down["until"] = time.monotonic() - 1
        self.local = lambda url: answer(200, url, {"text": "back"})
        self.assertEqual(self.run_it(), ("back", "local"))
        self.assertEqual(stt._down["fails"], 0)

    def test_busy_is_retried_on_local_and_does_not_back_off(self):
        codes = iter([409, 200])
        self.local = lambda url: answer(next(codes), url, {"text": "after dictation"})
        self.assertEqual(self.run_it(), ("after dictation", "local"))
        self.assertEqual(len(self.gets), 2)
        self.assertFalse(stt._local_down())

    def test_still_busy_falls_back_without_backing_off(self):
        self.local = lambda url: answer(409, url)
        self.assertEqual(self.run_it()[1], "hub cloud")
        self.assertEqual(len(self.gets), 3)
        self.assertFalse(stt._local_down())

    def test_without_fallback_a_failure_raises_and_the_hub_is_not_asked(self):
        self.local = self.down
        with self.assertRaises(httpx.ConnectError):
            self.run_it(fallback=False)
        with self.assertRaises(httpx.HTTPError):
            self.run_it(fallback=False)
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.gets), 1)

    def test_the_hub_failing_too_raises(self):
        self.local = self.down
        self.hub = lambda url: answer(503, url)
        with self.assertRaises(httpx.HTTPStatusError):
            self.run_it()

    def test_transcribe_file_keeps_returning_words(self):
        with mock.patch.object(httpx, "get", self.get):
            self.assertEqual(stt.transcribe_file(str(self.wav), self.cfg), "from the gpu")


if __name__ == "__main__":
    unittest.main()
