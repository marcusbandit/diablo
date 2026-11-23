import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from hub import brains, db, devices, server, voicetimes
from voice import timeline

TOKEN = "test-hub-token"
HEADERS = {"X-Diablo-Token": TOKEN}

POINTS = {"name": 0, "speech_start": 900, "speech_end": 4000, "turn_end": 5100, "text": 5200, "hub_sent": 5210,
          "brain_back": 5600, "beat_audio": 5700, "first_sentence": 9000, "ack_ready": 9500, "first_audio": 9560,
          "hub_back": 9800, "end": 14000}
HUB = {"backend": "claude", "model": "model-a", "tools": 0, "hub_start_ms": 5, "picked_ms": 1650, "sent_ms": 1900,
       "first_token_ms": 2700, "first_text_token_ms": 3600, "said_ms": 3750, "brain_ms": 4500}


class StateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(voicetimes, "STATE", Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)


class TestStages(unittest.TestCase):
    def test_every_stage_counts_from_the_end_of_his_speech(self):
        found = voicetimes.stages(POINTS, HUB)
        self.assertEqual(found, {"speech_end": 0, "transcript": 1200, "hub_in": 1210, "first_token": 3910,
                                 "first_text": 4810, "first_sentence": 5000, "tts_audio": 5500, "playback": 5560,
                                 "beat": 1700})
        self.assertEqual(list(found), [s for s in voicetimes.STAGES if s in found])

    def test_the_reply_spoken_after_the_turn_counts_its_own_voice_fetch(self):
        points = {k: v for k, v in POINTS.items() if k not in ("ack_ready", "beat_audio")}
        points.update(tts_ready=10400, first_audio=10450)
        found = voicetimes.stages(points, HUB)
        self.assertEqual((found["tts_audio"], found["playback"]), (6400, 6450))
        self.assertNotIn("beat", found)

    def test_the_hub_sentence_stands_in_when_the_ears_missed_it(self):
        points = {k: v for k, v in POINTS.items() if k != "first_sentence"}
        self.assertEqual(voicetimes.stages(points, HUB)["first_sentence"], 1210 + 3750)

    def test_a_hub_sentence_before_the_end_of_speech_is_left_out(self):
        points = {"speech_end": 4000, "text": 3000, "hub_sent": 3010, "first_audio": 6000}
        self.assertNotIn("first_sentence", voicetimes.stages(points, {"said_ms": 500}))

    def test_no_end_of_speech_means_nothing_to_count(self):
        self.assertEqual(voicetimes.stages({"hub_sent": 10, "first_audio": 500}, HUB), {})

    def test_points_before_the_end_of_speech_and_junk_are_left_out(self):
        found = voicetimes.stages({"speech_end": 4000, "text": 3000, "first_audio": "soon", "hub_sent": 4100},
                                  {"first_token_ms": None})
        self.assertEqual(found, {"speech_end": 0, "hub_in": 100})


class TestRecord(StateCase):
    def test_a_turn_is_one_json_line_in_the_log_and_the_file(self):
        with self.assertLogs("hub.voicetimes", level="INFO") as logged:
            row = voicetimes.record({"points": POINTS, "hub": HUB, "kind": "wake", "outcome": "answered by claude"},
                                    now=1000.0)
        self.assertEqual(len(logged.records), 1)
        message = logged.records[0].getMessage()
        self.assertTrue(message.startswith("voice turn {"))
        self.assertEqual(json.loads(message[len("voice turn "):]), row)
        self.assertEqual(row["hub"]["model"], "model-a")
        self.assertEqual(row["stages"]["playback"], 5560)
        kept = (Path(self.tmp.name) / voicetimes.LOG_NAME).read_text().splitlines()
        self.assertEqual([json.loads(line) for line in kept], [row])

    def test_nothing_is_kept_without_an_end_of_speech(self):
        self.assertIsNone(voicetimes.record({"points": {"hub_sent": 5}}))
        self.assertEqual(voicetimes.load(), [])

    def test_the_file_keeps_its_newest_turns(self):
        with mock.patch.object(voicetimes, "KEEP", 5), self.assertLogs("hub.voicetimes", level="INFO"):
            for n in range(120):
                voicetimes.record({"points": {"speech_end": 0, "first_audio": n}, "hub": HUB})
        rows = voicetimes.load(last=500)
        self.assertLessEqual(len(rows), 105)
        self.assertEqual(rows[-1]["stages"]["playback"], 119)


class TestSummary(StateCase):
    def test_median_and_p90_over_the_last_twenty_turns(self):
        with self.assertLogs("hub.voicetimes", level="INFO"):
            for n in range(1, 31):
                tools = 0 if n % 2 else 3
                voicetimes.record({"points": {"speech_end": 0, "hub_sent": 10, "first_audio": n * 100},
                                   "hub": {**HUB, "tools": tools}})
        found = voicetimes.report()
        self.assertEqual(len(found["turns"]), 20)
        playback = found["summary"]["playback"]
        self.assertEqual(playback["n"], 20)
        self.assertEqual(playback["median_ms"], 2050)
        self.assertEqual(playback["p90_ms"], 2800)
        self.assertEqual(found["counted"], {"all": 20, "conversational": 10})
        self.assertEqual(found["conversational"]["playback"]["median_ms"], 2000)
        self.assertIn("p90", voicetimes.text(found))

    def test_a_direct_change_is_not_a_conversational_turn(self):
        self.assertTrue(voicetimes.conversational({"hub": {"backend": "claude", "tools": 0}}))
        self.assertFalse(voicetimes.conversational({"hub": {"backend": "direct", "tools": 0}}))
        self.assertFalse(voicetimes.conversational({"hub": {"backend": "claude", "tools": 2}}))

    def test_percentile_is_the_nearest_rank(self):
        self.assertEqual(voicetimes.percentile([5, 1, 3], 0.9), 5)
        self.assertEqual(voicetimes.percentile(list(range(1, 11)), 0.9), 9)
        self.assertIsNone(voicetimes.percentile([], 0.9))


class TestEndpoint(StateCase):
    def setUp(self):
        super().setUp()
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def test_the_ears_report_a_turn_and_read_the_medians_back(self):
        with self.assertLogs("hub.voicetimes", level="INFO"):
            sent = self.client.post("/v1/voice/timing", headers=HEADERS,
                                    json={"points": POINTS, "hub": HUB, "kind": "wake", "outcome": "answered"})
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.json()["stages"]["playback"], 5560)
        read = self.client.get("/v1/voice/timing", headers=HEADERS, params={"last": 5})
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["summary"]["first_token"], {"n": 1, "median_ms": 3910, "p90_ms": 3910})

    def test_a_turn_without_its_end_of_speech_is_refused_quietly(self):
        sent = self.client.post("/v1/voice/timing", headers=HEADERS, json={"points": {"hub_sent": 4}})
        self.assertEqual(sent.json(), {"ok": False, "stages": {}})

    def test_a_token_is_needed(self):
        self.assertEqual(self.client.get("/v1/voice/timing").status_code, 403)


class FakeStream:
    def __init__(self, lines):
        self.lines = [json.dumps(line).encode() + b"\n" for line in lines]

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""


def delta(kind, **fields):
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": kind, **fields}}}


class TestHubMarks(unittest.TestCase):
    def test_thinking_is_the_first_token_and_the_text_its_first_word(self):
        session = brains.ClaudeSession("bench")
        session.proc = mock.MagicMock()
        session.proc.stdout = FakeStream([
            {"type": "stream_event", "event": {"type": "message_start"}},
            delta("thinking_delta", thinking="Hmm."),
            delta("text_delta", text="Hello there. "),
            {"type": "result", "subtype": "success", "result": "Hello there."},
        ])
        clock = iter(range(100, 200))
        result = brains.Turn()
        with mock.patch.object(brains.time, "time", side_effect=lambda: float(next(clock))):
            asyncio.run(session._read(result, None))
        self.assertLess(result.marks["first_token"], result.marks["first_text_token"])
        self.assertEqual(result.said, ["Hello there."])

    def test_stage_times_count_from_the_request_arriving(self):
        turn = brains.Turn(marks={"picked": 11.0, "sent": 11.5, "first_token": 12.0, "first_text_token": 13.0},
                           first_said_at=13.25)
        found = brains.stage_times(turn, started=10.5, arrived=10.0)
        self.assertEqual(found, {"hub_start_ms": 500, "picked_ms": 1000, "sent_ms": 1500, "first_token_ms": 2000,
                                 "first_text_token_ms": 3000, "said_ms": 3250})


class ControlProc:
    """A claude process that answers every control request at once and keeps what it was sent."""

    def __init__(self):
        self.returncode = None
        self.sent = []
        self.stdin = self
        self.stdout = self

    def write(self, data):
        self.sent.append(json.loads(data))

    async def drain(self):
        return None

    async def readline(self):
        asked = self.sent[-1]
        return (json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": asked["request_id"], "response": {}}}) + "\n").encode()


class TestPrime(unittest.TestCase):
    def session(self, model="model-big"):
        session = brains.ClaudeSession("bench")
        session.proc = ControlProc()
        session.model = model
        return session

    def test_a_waiting_session_switches_to_the_spoken_model_and_back(self):
        session = self.session()
        self.assertTrue(asyncio.run(session.prime("model-small")))
        asked = [m["request"] for m in session.proc.sent]
        self.assertEqual(asked, [{"subtype": "set_model", "model": "model-small"},
                                 {"subtype": "set_model", "model": "model-big"}])
        self.assertEqual(session.model, "model-big")

    def test_nothing_to_prime_on_the_same_model_a_busy_or_a_dead_session(self):
        session = self.session("model-small")
        self.assertFalse(asyncio.run(session.prime("model-small")))
        busy = self.session()
        busy.busy = True
        self.assertFalse(asyncio.run(busy.prime("model-small")))
        dead = self.session()
        dead.proc.returncode = 0
        self.assertFalse(asyncio.run(dead.prime("model-small")))
        self.assertEqual(session.proc.sent + busy.proc.sent + dead.proc.sent, [])

    def test_a_prime_that_fails_closes_the_process(self):
        session = self.session()
        session.proc.kill = mock.MagicMock()

        async def broken(request):
            raise TimeoutError()

        with mock.patch.object(session, "control", side_effect=broken):
            self.assertFalse(asyncio.run(session.prime("model-small")))
        session.proc.kill.assert_called_once()

    def test_the_spoken_model_comes_from_the_route(self):
        with mock.patch.object(brains.route, "spoken_brain", return_value=("model-small", "high")):
            self.assertEqual(brains.spoken_model(), "model-small")
        with mock.patch.object(brains.route, "spoken_brain", return_value=None):
            self.assertEqual(brains.spoken_model(), "")


class TestEarsReport(unittest.TestCase):
    def test_an_answered_hub_turn_is_reported_without_its_words(self):
        row = {"kind": "wake", "text": "secret words", "points": POINTS, "hub": HUB, "outcome": "answered by claude",
               "spoken": ["more words"]}
        body = timeline.hub_report(row)
        self.assertEqual(body["points"], POINTS)
        self.assertNotIn("text", body)
        self.assertNotIn("secret words", json.dumps(body))

    def test_turns_the_hub_did_not_answer_aloud_are_not_reported(self):
        self.assertIsNone(timeline.hub_report({"points": {"speech_end": 1, "hub_sent": 2}, "hub": HUB}))
        self.assertIsNone(timeline.hub_report({"points": POINTS, "hub": {"backend": "control hub"}}))
        self.assertIsNone(timeline.hub_report({"points": POINTS}))

    def test_saving_a_turn_hands_its_row_to_the_hooks(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(timeline, "ON_SAVE", [seen.append]):
            tl = timeline.Timeline("wake", start=0.0, clock=lambda: 1.0)
            tl.mark("speech_end")
            tl.save(Path(tmp) / "turns.jsonl")
        self.assertEqual(seen[0]["points"], {"speech_end": 1000})


if __name__ == "__main__":
    unittest.main()
