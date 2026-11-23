import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import db


class SpeechEndpointTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from hub import server

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        self.server = server
        self.client = TestClient(server.app)
        server.app.dependency_overrides[server.require_token] = lambda: {"device": "phone"}
        self.addCleanup(server.app.dependency_overrides.clear)
        alone = mock.patch.object(server.standby, "is_standby", return_value=False)
        alone.start()
        self.addCleanup(alone.stop)

    def test_speaks_in_his_configured_voice(self):
        db.set_setting("tts_provider", "cartesia")
        db.set_setting("tts_voice_id", "voice-one")
        with mock.patch.object(self.server.tts, "speak", return_value=b"RIFFwav") as spoke:
            answer = self.client.post("/v1/speech", json={"text": " Lights on, sir. "})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.headers["content-type"], "audio/wav")
        self.assertEqual(answer.content, b"RIFFwav")
        self.assertEqual(spoke.call_args.args, ("Lights on, sir.", False, "en", "phone"))

    def test_the_cartesia_call_carries_the_chosen_voice(self):
        db.set_setting("tts_provider", "cartesia")
        db.set_setting("tts_api_key", "k")
        db.set_setting("tts_voice_id", "voice-one")
        sent = {}

        def post(url, headers=None, json=None, timeout=None):
            import httpx

            sent.update(json)
            return httpx.Response(200, content=b"RIFFwav", request=httpx.Request("POST", url))

        with mock.patch.object(self.server.tts.httpx, "post", post):
            answer = self.client.post("/v1/speech", json={"text": "Done, sir."})
        self.assertEqual(answer.content, b"RIFFwav")
        self.assertEqual(sent["voice"], "voice-one")
        self.assertEqual(sent["transcript"], "Done, sir.")

    def test_no_voice_set_up_is_503(self):
        db.set_setting("tts_provider", "none")
        self.assertEqual(self.client.post("/v1/speech", json={"text": "Hello"}).status_code, 503)

    def test_a_failing_voice_is_502(self):
        db.set_setting("tts_provider", "cartesia")
        with mock.patch.object(self.server.tts, "speak", side_effect=RuntimeError("down")):
            answer = self.client.post("/v1/speech", json={"text": "Hello"})
        self.assertEqual(answer.status_code, 502, answer.text)

    def test_empty_and_huge_text_are_refused(self):
        db.set_setting("tts_provider", "cartesia")
        self.assertEqual(self.client.post("/v1/speech", json={"text": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/v1/speech", json={"text": "x" * 5000}).status_code, 413)


if __name__ == "__main__":
    unittest.main()
