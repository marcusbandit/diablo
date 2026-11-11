import json
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import db, devices, server, usage
from hub.modules import usage as usage_module

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


def ogg(seconds):
    page = b"OggS" + bytes(2) + struct.pack("<q", int(seconds * 48000)) + bytes(16)
    return b"OggS" + bytes(30) + page


def wav(seconds, rate=16000):
    frames = bytes(int(seconds * rate) * 2)
    head = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return head + b"data" + struct.pack("<I", 0xFFFFFFFF) + frames


class UsageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        usage._cache.clear()
        fetchers = mock.patch.dict(usage.FETCHERS, {"openrouter": lambda: None, "fireworks": lambda: None})
        fetchers.start()
        self.addCleanup(fetchers.stop)

    def service(self, sid, **kwargs):
        return next(s for s in usage.report(**kwargs)["services"] if s["id"] == sid)


class TestCounting(UsageCase):
    def test_audio_length_comes_from_the_clip(self):
        self.assertAlmostEqual(usage.audio_seconds(ogg(7.5)), 7.5)
        self.assertAlmostEqual(usage.audio_seconds(wav(2.0)), 2.0, places=2)
        self.assertEqual(usage.audio_seconds(b"not audio"), 0.0)

    def test_a_groq_transcription_is_priced_with_the_ten_second_minimum(self):
        usage.record_transcription("https://api.groq.com/openai/v1", "whisper-large-v3-turbo", ogg(4), "v.ogg",
                                   {"x-ratelimit-limit-requests": "2000", "x-ratelimit-remaining-requests": "1990",
                                    "x-ratelimit-reset-requests": "7m", "authorization": "never kept"})
        groq = self.service("groq")
        self.assertEqual(groq["today"]["calls"], 1)
        self.assertEqual(groq["today"]["units"], 4.0)
        self.assertAlmostEqual(groq["today"]["cost"], 10 / 3600 * 0.04, places=6)
        self.assertEqual(groq["limits"][0]["used"], 10)
        self.assertEqual(groq["limits"][0]["max"], 2000)
        self.assertNotIn("never kept", json.dumps(usage.report()))

    def test_claude_rows_are_the_growth_of_a_cumulative_session(self):
        first = {"modelUsage": {"claude-sonnet-5-5": {"inputTokens": 10, "cacheCreationInputTokens": 90,
                                                      "cacheReadInputTokens": 1000, "outputTokens": 50, "costUSD": 0.5}}}
        second = {"modelUsage": {"claude-sonnet-5-5": {"inputTokens": 12, "cacheCreationInputTokens": 100,
                                                       "cacheReadInputTokens": 3000, "outputTokens": 80, "costUSD": 0.8},
                                 "claude-haiku-5-5": {"inputTokens": 5, "outputTokens": 5, "costUSD": 0.01}}}
        kept = usage.record_claude({}, first)
        usage.record_claude(kept, second)
        claude = self.service("claude")
        self.assertEqual(claude["today"]["calls"], 3)
        self.assertAlmostEqual(claude["today"]["cost"], 0.81, places=4)
        self.assertEqual(claude["today"]["tokens_out"], 85)
        by_model = {m["model"]: m for m in claude["models"]}
        self.assertEqual(by_model["claude-sonnet-5-5"]["calls"], 2)
        self.assertEqual(by_model["claude-haiku-5-5"]["calls"], 1)

    def test_a_new_process_starts_its_count_again(self):
        kept = usage.record_claude({}, {"modelUsage": {"m": {"outputTokens": 500, "costUSD": 1.0}}})
        usage.record_claude(kept, {"modelUsage": {"m": {"outputTokens": 20, "costUSD": 0.1}}})
        self.assertAlmostEqual(self.service("claude")["today"]["cost"], 1.1, places=4)

    def test_session_counts_only_calls_since_the_app_opened(self):
        usage.record("openrouter", "jev", origin="jev")
        opened = time.time() + 0.01
        time.sleep(0.02)
        usage.record("openrouter", "jev", origin="jev")
        router = self.service("openrouter", since=opened)
        self.assertEqual(router["session"]["calls"], 1)
        self.assertEqual(router["today"]["calls"], 2)
        self.assertEqual(len(router["history"]), usage.DAYS)
        self.assertEqual(router["history"][-1]["calls"], 2)

    def test_chat_tokens_come_from_the_usage_block(self):
        usage.record_chat("https://api.fireworks.ai/inference/v1", "glm", {"usage": {"prompt_tokens": 100, "completion_tokens": 20}})
        fireworks = self.service("fireworks")
        self.assertEqual(fireworks["today"]["units"], 120)
        self.assertEqual(fireworks["source"], "both")

    def test_record_never_raises(self):
        with mock.patch.object(db, "add_usage", side_effect=RuntimeError("db gone")):
            usage.record("groq")


class TestStatus(UsageCase):
    def test_claude_windows_set_the_state(self):
        later = time.time() + 3600
        db.set_setting("limits.claude", json.dumps({"windows": {
            "five_hour": {"utilization": 0.85, "resetsAt": later, "status": "allowed"},
            "seven_day": {"utilization": 0.4, "resetsAt": later, "status": "allowed"}}, "at": time.time()}))
        claude = self.service("claude")
        self.assertEqual(claude["status"], "near")
        self.assertEqual({l["label"] for l in claude["limits"]}, {"5 hour window", "This week"})

    def test_a_window_past_its_reset_reads_as_empty(self):
        db.set_setting("limits.claude", json.dumps({"windows": {
            "five_hour": {"utilization": 1.0, "resetsAt": time.time() - 60, "status": "rejected"}}}))
        claude = self.service("claude")
        self.assertEqual(claude["status"], "ok")
        self.assertEqual(claude["limits"][0]["fraction"], 0)

    def test_openrouter_out_of_credit(self):
        usage._cache["openrouter"] = {"at": time.time(), "read": time.time(), "error": "", "data": {
            "today": 0.1, "week": 0.5, "month": 2.0, "total": 10.0, "limit": None, "limit_remaining": None,
            "limit_reset": None, "free_tier": False, "free_requests": None, "credits": 10, "credits_used": 10.0}}
        db.set_setting("provider.openrouter.api_key", "sk-secret-value")
        router = self.service("openrouter")
        self.assertEqual(router["status"], "out")
        self.assertEqual(router["balance"]["value"], 0)
        self.assertEqual(router["provider"]["month"], 2.0)
        self.assertNotIn("sk-secret-value", json.dumps(usage.report()))

    def test_unconfigured_services_are_off(self):
        self.assertEqual(self.service("cartesia")["status"], "off")
        db.set_setting("tts_provider", "cartesia")
        db.set_setting("tts_api_key", "k")
        db.set_setting("usage.cartesia.monthly_credits", "1000")
        usage.record_speech("sonic-latest", "x" * 900)
        cartesia = self.service("cartesia")
        self.assertEqual(cartesia["status"], "near")
        self.assertEqual(cartesia["source"], "ours")


class TestLimits(UsageCase):
    def test_reset_headers_read_as_seconds(self):
        self.assertEqual(usage.reset_seconds("7m59.5s"), 479.5)
        self.assertEqual(usage.reset_seconds("2h"), 7200)
        self.assertEqual(usage.reset_seconds("120ms"), 0.12)
        self.assertEqual(usage.reset_seconds("30"), 30)
        self.assertIsNone(usage.reset_seconds("soon"))

    def test_a_groq_window_that_has_reset_is_dropped(self):
        db.set_setting("usage.limits.groq", json.dumps({"at": time.time() - 120, "headers": {
            "x-ratelimit-limit-tokens": "6000", "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "59s",
            "x-ratelimit-limit-requests": "2000", "x-ratelimit-remaining-requests": "1990", "x-ratelimit-reset-requests": "7m"}}))
        groq = self.service("groq")
        self.assertEqual([l["label"] for l in groq["limits"]], ["Requests"])

    def test_a_jev_call_that_never_answers_still_counts(self):
        db.set_setting("provider.openrouter.api_key", "k")
        with mock.patch("hub.jev.httpx.post", side_effect=usage.httpx.ConnectTimeout("slow")):
            from hub import jev
            self.assertIsNone(jev.decide("dim the lights"))
        router = self.service("openrouter")
        self.assertEqual((router["today"]["calls"], router["today"]["failed"]), (1, 1))

    def test_old_rows_are_pruned_at_start(self):
        usage.record("groq")
        with db._lock:
            db._conn.execute("UPDATE usage_calls SET ts = ?", (time.time() - 500 * 86400,))
            db._conn.commit()
        usage.warm()
        self.assertEqual(db.usage_totals(0), {})


class TestProviders(UsageCase):
    def test_the_first_report_waits_for_a_read_warm_already_started(self):
        def slow():
            time.sleep(0.2)
            return {"x": 2}

        with mock.patch.dict(usage.FETCHERS, {"openrouter": slow}):
            usage.provider("openrouter")
            entry = usage.provider("openrouter", wait=True)
        self.assertEqual(entry["data"], {"x": 2})

    def test_the_fireworks_account_follows_the_key(self):
        answers = iter([{"accounts": [{"name": "accounts/a"}]}, {"accounts": [{"name": "accounts/b"}]}])

        def get(*_args, **_kwargs):
            answer = mock.Mock(status_code=200)
            answer.raise_for_status = mock.Mock()
            answer.json = mock.Mock(return_value=next(answers))
            return answer

        with mock.patch.object(usage.httpx, "get", side_effect=get):
            self.assertEqual(usage._fireworks_account({"Authorization": "Bearer one"}), "accounts/a")
            self.assertEqual(usage._fireworks_account({"Authorization": "Bearer one"}), "accounts/a")
            self.assertEqual(usage._fireworks_account({"Authorization": "Bearer two"}), "accounts/b")

    def test_a_slow_provider_is_read_once_on_a_thread(self):
        calls = []

        def fetch():
            calls.append(1)
            return {"x": 1}

        with mock.patch.dict(usage.FETCHERS, {"openrouter": fetch}):
            first = usage.provider("openrouter", wait=True)
            again = usage.provider("openrouter")
        self.assertEqual(first["data"], {"x": 1})
        self.assertIs(again, first)
        self.assertEqual(len(calls), 1)

    def test_a_failing_provider_keeps_the_last_numbers(self):
        usage._cache["openrouter"] = {"at": 0, "read": 5.0, "error": "", "data": {"old": True}}

        def broken():
            raise RuntimeError("Bearer sk-should-not-show")

        with mock.patch.dict(usage.FETCHERS, {"openrouter": broken}):
            usage.provider("openrouter")
            for _ in range(50):
                if usage._cache["openrouter"]["at"]:
                    break
                time.sleep(0.02)
        entry = usage._cache["openrouter"]
        self.assertEqual(entry["data"], {"old": True})
        self.assertEqual(entry["read"], 5.0)
        self.assertNotIn("sk-should", entry["error"])

    def test_fireworks_bill_sums_line_items(self):
        answer = mock.Mock(status_code=200)
        answer.raise_for_status = mock.Mock()
        answer.json = mock.Mock(return_value={"lineItems": [
            {"category": "LLM input tokens (uncached)", "groupingValue": "GLM", "quantity": 1000,
             "totalCost": {"units": "1", "nanos": 500000000}},
            {"category": "LLM output tokens", "groupingValue": "GLM", "quantity": 200, "totalCost": {"units": "0", "nanos": 250000000}}]})
        with mock.patch.object(usage.httpx, "get", return_value=answer):
            import datetime
            bill = usage._fireworks_bill({}, "accounts/x", datetime.datetime.now().astimezone(), datetime.datetime.now().astimezone())
        self.assertAlmostEqual(bill["cost"], 1.75)
        self.assertEqual(bill["tokens"], 1200)


class TestApi(UsageCase):
    def setUp(self):
        super().setUp()
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def test_get_usage_needs_a_token(self):
        self.assertEqual(self.client.get("/v1/usage").status_code, 403)

    def test_a_client_reports_a_call_and_reads_it_back(self):
        sent = self.client.post("/v1/usage", headers=HEADERS, json={
            "service": "cartesia", "model": "sonic-latest", "unit": "characters", "units": 120, "origin": "desk"})
        self.assertEqual(sent.status_code, 200)
        body = self.client.get("/v1/usage", headers=HEADERS, params={"since": time.time() - 60}).json()
        cartesia = next(s for s in body["services"] if s["id"] == "cartesia")
        self.assertEqual(cartesia["session"]["units"], 120)
        self.assertEqual([s["id"] for s in body["services"]], list(usage.SERVICES))
        self.assertTrue(body["free"])

    def test_bad_reports_are_refused(self):
        for bad in ({"service": "nope"}, {"service": "groq", "unit": "furlongs"}, {"service": "groq", "units": -1}):
            self.assertEqual(self.client.post("/v1/usage", headers=HEADERS, json=bad).status_code, 400)

    def test_the_usage_panel_is_made_of_known_kinds(self):
        blocks = usage_module.panel({"id": "usage"})
        self.assertEqual([b["kind"] for b in blocks], ["stats", "bars", "list"])
        self.assertEqual(len(blocks[0]["items"]), len(usage.SERVICES))
        self.assertIn("Claude Code", usage_module.call("usage_summary", {}))


if __name__ == "__main__":
    unittest.main()
