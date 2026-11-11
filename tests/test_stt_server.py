import sys
import unittest
import unittest.mock
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import stt_server


class FakeInfo:
    language = "en"
    duration = 4.5


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio.read(), kwargs))
        return iter([FakeSegment(" dim the "), FakeSegment("lights ")]), FakeInfo()


def engine(free=20000, idle=600):
    loads = []

    def loader():
        model = FakeModel()
        loads.append(model)
        return model

    made = stt_server.Engine(loader=loader, free=lambda: free, idle=idle, min_free=2500)
    return made, loads


def client(made, token="secret"):
    return TestClient(stt_server.create_app(engine=made, token=token))


AUTH = {"Authorization": "Bearer secret"}


class TestServer(unittest.TestCase):
    def test_transcribes_an_upload(self):
        made, loads = engine()
        response = client(made).post("/v1/audio/transcriptions", headers=AUTH,
                                     files={"file": ("voice.ogg", b"OggS bytes", "audio/ogg")},
                                     data={"model": "large-v3-turbo"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"text": "dim the lights", "language": "en", "duration": 4.5,
                                           "model": stt_server.MODEL})
        self.assertEqual(loads[0].calls[0][0], b"OggS bytes")
        self.assertIsNone(loads[0].calls[0][1]["language"])

    def test_language_field_is_passed_on(self):
        made, loads = engine()
        client(made).post("/v1/audio/transcriptions", headers=AUTH,
                          files={"file": ("a.wav", b"RIFF", "audio/wav")}, data={"language": "da"})
        self.assertEqual(loads[0].calls[0][1]["language"], "da")

    def test_needs_the_token(self):
        made, _ = engine()
        app = client(made)
        self.assertEqual(app.get("/v1/health").status_code, 401)
        self.assertEqual(app.get("/v1/health", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(app.post("/v1/audio/transcriptions", files={"file": ("a", b"x")}).status_code, 401)

    def test_no_token_file_refuses_everyone(self):
        made, _ = engine()
        self.assertEqual(client(made, token="").get("/v1/health", headers={"Authorization": "Bearer "}).status_code, 401)

    def test_missing_file_is_a_400(self):
        made, _ = engine()
        response = client(made).post("/v1/audio/transcriptions", headers=AUTH, data={"model": "x"})
        self.assertEqual(response.status_code, 400)

    def test_health_without_loading(self):
        made, loads = engine()
        response = client(made).get("/v1/health", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["loaded"], False)
        self.assertEqual(loads, [])

    def test_busy_gpu_is_unhealthy_and_refuses_to_load(self):
        made, loads = engine(free=1000)
        app = client(made)
        self.assertEqual(app.get("/v1/health", headers=AUTH).status_code, 503)
        response = app.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("a.ogg", b"x")})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(loads, [])

    def test_loaded_model_stays_healthy_when_vram_fills(self):
        made, _ = engine()
        made.transcribe(b"x")
        made.free = lambda: 100
        self.assertTrue(made.status()["ok"])

    def test_failed_load_is_unhealthy_for_a_while(self):
        def broken():
            raise OSError("libcublas.so.12 not found")

        made = stt_server.Engine(loader=broken, free=lambda: 20000)
        app = client(made)
        response = app.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("a.ogg", b"x")})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(app.get("/v1/health", headers=AUTH).status_code, 503)
        made.load_failed -= stt_server.LOAD_COOLDOWN + 1
        self.assertEqual(app.get("/v1/health", headers=AUTH).status_code, 200)

    def test_non_ascii_token_is_a_401(self):
        made, _ = engine()
        response = client(made).get("/v1/health", headers={"Authorization": "Bearer caf\xe9".encode("latin-1")})
        self.assertEqual(response.status_code, 401)

    def test_oversized_upload_is_refused_before_reading(self):
        made, loads = engine()
        response = client(made).post("/v1/audio/transcriptions",
                                     headers={**AUTH, "Content-Length": str(stt_server.MAX_BYTES + 1)},
                                     content=b"x")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(loads, [])


    def test_chunked_upload_over_the_cap_is_refused(self):
        made, loads = engine()

        def body():
            for _ in range(3):
                yield b"x" * (stt_server.MAX_BYTES // 2)

        response = client(made).post("/v1/audio/transcriptions", headers=AUTH, content=body())
        self.assertEqual(response.status_code, 413)
        self.assertEqual(loads, [])

    def test_free_vram_is_read_at_most_every_few_seconds(self):
        calls = []
        made = stt_server.Engine(loader=FakeModel, free=lambda: calls.append(1) or 20000)
        for _ in range(5):
            made.status()
        self.assertEqual(len(calls), 1)


    def test_a_failed_vram_read_is_no_room(self):
        with unittest.mock.patch.object(stt_server.subprocess, "run", side_effect=OSError("no nvidia-smi")):
            self.assertEqual(stt_server.free_vram_mb(), 0 if stt_server.DEVICE == "cuda" else None)


class TestIdle(unittest.TestCase):
    def test_no_unload_while_a_request_is_in_flight(self):
        made, _ = engine(idle=0)
        made.transcribe(b"x")
        made.enter()
        self.assertFalse(made.reap(now=made.used + 10))
        made.leave()
        self.assertTrue(made.reap(now=made.used + 10))

    def test_reaper_exits_after_unloading(self):
        made, _ = engine(idle=0)
        made.transcribe(b"x")
        exits = []

        def leave(code):
            self.assertTrue(made.lock.locked())
            exits.append(code)
            raise SystemExit

        with self.assertRaises(SystemExit):
            stt_server.reaper(made, every=0, leave=leave)
        self.assertEqual(exits, [0])
        self.assertIsNone(made.model)

    def test_unloads_after_idle_and_reloads(self):
        made, loads = engine(idle=60)
        made.transcribe(b"x")
        self.assertFalse(made.reap(now=made.used + 30))
        self.assertTrue(made.reap(now=made.used + 61))
        self.assertIsNone(made.model)
        made.transcribe(b"y")
        self.assertEqual(len(loads), 2)


if __name__ == "__main__":
    unittest.main()
