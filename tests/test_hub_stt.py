import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import db, settings, stt


class FakeWhisper:
    def __init__(self, text, health_delay=0.0, health_status=200, status=200):
        self.text = text
        self.health_delay = health_delay
        self.health_status = health_status
        self.status = status
        self.health_calls = 0
        self.posts = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                fake.health_calls += 1
                time.sleep(fake.health_delay)
                self._send(fake.health_status, {"ok": fake.health_status == 200})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                fake.posts.append({"auth": self.headers.get("Authorization"), "body": self.rfile.read(length)})
                if fake.status != 200:
                    self._send(fake.status, {"error": "no"})
                else:
                    self._send(200, {"text": fake.text, "language": "da"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class SttCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        stt._health.clear()
        stt._last.update(backend="", at=0.0)
        stt._refreshing = False
        stt._fails.clear()
        self.local = self.fake("from the gpu")
        self.cloud = self.fake("from groq")
        settings.set_setting("transcription.local.base_url", self.local.url)
        settings.set_setting("transcription.local.api_key", "local-token")
        settings.set_setting("transcription_base_url", self.cloud.url)
        settings.set_setting("transcription_api_key", "groq-key")

    def fake(self, text, **kwargs):
        server = FakeWhisper(text, **kwargs)
        self.addCleanup(server.close)
        return server


class TestOrdering(SttCase):
    def test_defaults_are_local_then_cloud(self):
        self.assertEqual(stt.order(), ["local", "cloud"])
        self.assertEqual([b["name"] for b in stt.configured()], ["local", "cloud"])

    def test_local_serves_when_healthy(self):
        with self.assertLogs("diablo.stt", "INFO") as logs:
            self.assertEqual(stt.transcribe(b"ogg", "voice.ogg"), ("from the gpu", "da"))
        self.assertEqual(len(self.cloud.posts), 0)
        self.assertEqual(self.local.posts[0]["auth"], "Bearer local-token")
        self.assertIn("stt served by local (large-v3-turbo)", logs.output[0])
        self.assertEqual(stt.status()["last"]["backend"], "local")

    def test_every_call_is_logged_as_usage_with_its_backend(self):
        with mock.patch.object(stt.usage, "record_transcription") as record:
            stt.transcribe(b"ogg", "voice.ogg")
        self.assertEqual(record.call_args.args[:2], (self.local.url, "large-v3-turbo"))

    def test_reads_settings_once_per_list(self):
        with mock.patch.object(stt.settings, "get_all", wraps=stt.settings.get_all) as read:
            stt.configured()
        self.assertEqual(read.call_count, 1)

    def test_order_is_a_setting(self):
        settings.set_setting("transcription.backends", "cloud,local")
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
        self.assertEqual(len(self.local.posts), 0)

    def test_cloud_only(self):
        settings.set_setting("transcription.backends", "cloud")
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
        self.assertEqual(self.cloud.posts[0]["auth"], "Bearer groq-key")
        self.assertEqual(self.local.health_calls, 0)

    def test_a_backend_without_a_key_is_skipped(self):
        settings.set_setting("transcription.local.api_key", "")
        self.assertEqual([b["name"] for b in stt.configured()], ["cloud"])
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")

    def test_a_third_tier_needs_only_settings(self):
        middle = self.fake("from nemurenai")
        settings.set_setting("transcription.backends", "local,nemurenai,cloud")
        settings.set_setting("transcription.nemurenai.base_url", middle.url)
        settings.set_setting("transcription.nemurenai.model", "small")
        settings.set_setting("transcription.nemurenai.api_key", "k")
        settings.set_setting("transcription.nemurenai.health", "on")
        self.local.health_status = 503
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from nemurenai")

    def test_nothing_set_up(self):
        settings.set_setting("transcription.local.api_key", "")
        settings.set_setting("transcription_api_key", "")
        self.assertFalse(stt.available())
        with self.assertRaises(stt.TranscriptionError):
            stt.transcribe(b"ogg", "voice.ogg")


class TestHealth(SttCase):
    def test_slow_health_check_times_out_and_falls_back_fast(self):
        self.local.health_delay = 1.5
        start = time.monotonic()
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
        self.assertLess(time.monotonic() - start, stt.HEALTH_TIMEOUT + 0.7)
        self.assertEqual(len(self.local.posts), 0)

    def test_health_is_cached(self):
        stt.transcribe(b"ogg", "voice.ogg")
        stt.status(wait=True)
        self.assertEqual(self.local.health_calls, 1)

    def test_cache_expires(self):
        stt.transcribe(b"ogg", "voice.ogg")
        for key, (ok, at, ttl) in list(stt._health.items()):
            stt._health[key] = (ok, at - ttl - 1, ttl)
        stt.transcribe(b"ogg", "voice.ogg")
        self.assertEqual(self.local.health_calls, 2)

    def test_unhealthy_local_goes_to_cloud(self):
        self.local.health_status = 503
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
        self.assertEqual(len(self.local.posts), 0)

    def test_status_names_the_active_backend(self):
        self.assertEqual(stt.status(wait=True)["active"], "local")
        stt._health.clear()
        self.local.health_status = 503
        state = stt.status(wait=True)
        self.assertEqual(state["active"], "cloud")
        self.assertEqual([(b["name"], b["healthy"]) for b in state["backends"]], [("local", False), ("cloud", True)])
        self.assertNotIn("groq-key", json.dumps(state))
        self.assertNotIn("local-token", json.dumps(state))


    def test_status_without_wait_never_blocks_on_a_sleeping_host(self):
        self.local.health_delay = 1.5
        start = time.monotonic()
        state = stt.status()
        self.assertLess(time.monotonic() - start, 0.3)
        self.assertIsNone(state["backends"][0]["healthy"])
        self.assertEqual(state["active"], "cloud")


    def test_active_follows_the_cache(self):
        stt.status(wait=True)
        self.assertEqual(stt.active(), "local")
        self.local.health_status = 503
        stt._health.clear()
        stt.status(wait=True)
        self.assertEqual(stt.active(), "cloud")

    def test_active_refreshes_a_stale_answer_in_the_background(self):
        stt.status(wait=True)
        self.local.health_status = 503
        for key, (ok, at, ttl) in list(stt._health.items()):
            stt._health[key] = (ok, at - ttl - 1, ttl)
        self.assertEqual(stt.active(), "local")
        deadline = time.monotonic() + 3
        while stt._refreshing and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(stt.active(), "cloud")

    def test_active_never_raises(self):
        with mock.patch.object(stt, "configured", side_effect=RuntimeError("db gone")), \
                self.assertLogs("diablo.stt", "ERROR"):
            self.assertEqual(stt.active(), "")

    def test_fresh_cache_starts_no_probe_thread(self):
        stt.status(wait=True)
        before = self.local.health_calls
        for _ in range(5):
            stt.status()
        self.assertFalse(stt._refreshing)
        self.assertEqual(self.local.health_calls, before)


class TestFallback(SttCase):
    def test_failed_request_retries_once_on_cloud(self):
        self.local.status = 500
        with self.assertLogs("diablo.stt", "INFO") as logs:
            self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
        self.assertEqual(len(self.local.posts), 1)
        self.assertEqual(len(self.cloud.posts), 1)
        self.assertIn("served by cloud", logs.output[0])
        self.assertIn("local failed (HTTPStatusError)", logs.output[0])

    def test_failed_request_marks_local_down_for_the_next_one(self):
        self.local.status = 502
        stt.transcribe(b"ogg", "voice.ogg")
        stt.transcribe(b"ogg", "voice.ogg")
        self.assertEqual(len(self.local.posts), 1)
        self.assertEqual(len(self.cloud.posts), 2)

    def test_host_down_falls_back(self):
        settings.set_setting("transcription.local.base_url", f"http://127.0.0.1:{closed_port()}/v1")
        start = time.monotonic()
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
        self.assertLess(time.monotonic() - start, 1.5)

    def test_a_malformed_url_falls_back(self):
        settings.set_setting("transcription.local.base_url", "http://banditbox:875x/v1")
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
        settings.set_setting("transcription.local.health", "off")
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")

    def test_a_non_object_answer_falls_back(self):
        original = self.local.server.RequestHandlerClass.do_POST

        def answer_a_list(handler):
            handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
            handler._send(200, ["not", "an", "object"])

        self.local.server.RequestHandlerClass.do_POST = answer_a_list
        self.addCleanup(setattr, self.local.server.RequestHandlerClass, "do_POST", original)
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")

    def test_a_bad_clip_does_not_mark_local_down(self):
        self.local.status = 400
        stt.transcribe(b"ogg", "voice.ogg")
        self.local.status = 200
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from the gpu")

    def test_repeated_500s_mark_local_down_with_a_growing_wait(self):
        self.local.status = 500
        stt.transcribe(b"ogg", "voice.ogg")
        stt.transcribe(b"ogg", "voice.ogg")
        self.assertEqual(len(self.local.posts), 2)
        stt.transcribe(b"ogg", "voice.ogg")
        self.assertEqual(len(self.local.posts), 2)
        first = max(ttl for _, _, ttl in stt._health.values())
        stt._health.clear()
        stt.transcribe(b"ogg", "voice.ogg")
        self.assertGreater(max(ttl for _, _, ttl in stt._health.values()), first)

    def test_success_resets_the_wait(self):
        self.local.status = 502
        stt.transcribe(b"ogg", "voice.ogg")
        stt._health.clear()
        self.local.status = 200
        stt.transcribe(b"ogg", "voice.ogg")
        self.assertEqual(stt._fails, {})

    def test_language_is_two_letters_or_english(self):
        self.assertEqual(stt._language("da"), "da")
        self.assertEqual(stt._language("yue"), "en")
        self.assertEqual(stt._language(None), "en")
        self.assertEqual(stt._language("EN"), "en")

    def test_a_busy_server_marks_local_down(self):
        self.local.status = 503
        stt.transcribe(b"ogg", "voice.ogg")
        self.local.status = 200
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")

    def test_a_refused_key_warns_once_and_falls_back(self):
        stt._warned.clear()
        self.local.health_status = 401
        with self.assertLogs("diablo.stt", "WARNING") as logs:
            self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
            stt._health.clear()
            stt.transcribe(b"ogg", "voice.ogg")
        refused = [line for line in logs.output if "refused the key" in line]
        self.assertEqual(len(refused), 1)
        self.assertIn("transcription.local.api_key", refused[0])
        self.assertNotIn("local-token", " ".join(logs.output))

    def test_empty_local_transcript_tries_the_cloud(self):
        self.local.text = ""
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg")[0], "from groq")
        self.assertEqual(stt._fails, {})

    def test_empty_everywhere_returns_empty(self):
        self.local.text = ""
        self.cloud.text = ""
        self.assertEqual(stt.transcribe(b"ogg", "voice.ogg"), ("", "da"))

    def test_local_usage_is_recorded_under_its_backend_name(self):
        with mock.patch.object(stt.usage, "record_transcription") as record:
            stt.transcribe(b"ogg", "voice.ogg")
        self.assertEqual(record.call_args.kwargs["service"], "local")

    def test_a_new_url_starts_a_fresh_streak(self):
        self.local.status = 502
        stt.transcribe(b"ogg", "voice.ogg")
        stt.transcribe(b"ogg", "voice.ogg")
        moved = self.fake("from the new gpu")
        settings.set_setting("transcription.local.base_url", moved.url)
        moved.status = 502
        stt.transcribe(b"ogg", "voice.ogg")
        ttl = stt._health[("local", moved.url)][2]
        self.assertEqual(ttl, stt.HEALTH_TTL)

    def test_every_backend_down_raises(self):
        self.local.status = 500
        self.cloud.status = 401
        with self.assertRaises(stt.TranscriptionError) as caught:
            stt.transcribe(b"ogg", "voice.ogg")
        self.assertIn("local failed", str(caught.exception))
        self.assertIn("cloud failed", str(caught.exception))
        self.assertNotIn("groq-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
