import io
import struct
import sys
import tempfile
import unittest
import wave
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

    def test_an_unknown_provider_is_503(self):
        db.set_setting("tts_provider", "elsewhere")
        self.assertEqual(self.client.post("/v1/speech", json={"text": "Hello"}).status_code, 503)

    def test_an_active_standby_still_speaks(self):
        from hub import standby

        self.assertIn(("POST", "/v1/speech"), standby.WRITABLE)

    def test_a_streamed_wav_reaches_the_phone_whole(self):
        db.set_setting("tts_provider", "cartesia")
        with mock.patch.object(self.server.tts, "speak", return_value=streamed_wav(b"\x01\x02" * 50)):
            answer = self.client.post("/v1/speech", json={"text": "Hello"})
        with wave.open(io.BytesIO(answer.content)) as w:
            self.assertEqual(w.getnframes(), 50)

    def test_a_failing_voice_is_502(self):
        db.set_setting("tts_provider", "cartesia")
        with mock.patch.object(self.server.tts, "speak", side_effect=RuntimeError("down")):
            answer = self.client.post("/v1/speech", json={"text": "Hello"})
        self.assertEqual(answer.status_code, 502, answer.text)

    def test_empty_and_huge_text_are_refused(self):
        db.set_setting("tts_provider", "cartesia")
        self.assertEqual(self.client.post("/v1/speech", json={"text": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/v1/speech", json={"text": "x" * 5000}).status_code, 413)


def streamed_wav(pcm):
    """The way Cartesia streams it: placeholder sizes and a LIST chunk before the data."""
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"LIST" + struct.pack("<I", 4) + b"INFO" + b"data" + struct.pack("<I", 0xFFFFFFFF) + pcm)


class WholeWavTest(unittest.TestCase):
    def test_a_streamed_wav_gets_its_real_sizes(self):
        from hub import tts

        pcm = bytes(range(256)) * 10
        fixed = tts.whole_wav(streamed_wav(pcm))
        with wave.open(io.BytesIO(fixed)) as w:
            self.assertEqual(w.getframerate(), 24000)
            self.assertEqual(w.readframes(w.getnframes()), pcm)
        self.assertEqual(struct.unpack("<I", fixed[4:8])[0], len(fixed) - 8)

    def test_an_odd_tail_and_other_bytes_are_left_alone(self):
        from hub import tts

        with wave.open(io.BytesIO(tts.whole_wav(streamed_wav(b"abc")))) as w:
            self.assertEqual(w.getnframes(), 1)
        self.assertEqual(tts.whole_wav(b"not a wav"), b"not a wav")


if __name__ == "__main__":
    unittest.main()
