import io
import json
import threading
import wave

import numpy as np

from voice import lipsync


def _wav(seconds=0.2, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.sin(np.arange(int(rate * seconds)) / 9) * 8000).astype("<i2").tobytes())
    return buf.getvalue()


def test_delay_defaults_to_zero_without_a_file(tmp_path):
    assert lipsync.delay_ms(tmp_path / "missing.json") == 0


def test_delay_round_trips_and_is_clamped(tmp_path):
    path = tmp_path / "diablo" / "lipsync.json"
    assert lipsync.save(42.4, path) == 42
    assert json.loads(path.read_text()) == {"delay_ms": 42}
    assert lipsync.delay_ms(path) == 42
    assert lipsync.save(-9000, path) == -lipsync.LIMIT_MS
    assert lipsync.delay_ms(path) == -lipsync.LIMIT_MS


def test_unreadable_delay_reads_as_zero(tmp_path):
    path = tmp_path / "lipsync.json"
    path.write_text("not json")
    assert lipsync.delay_ms(path) == 0
    path.write_text('{"delay_ms": "soon"}')
    assert lipsync.delay_ms(path) == 0


def test_config_path_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert lipsync.config_path() == tmp_path / "diablo" / "lipsync.json"


def test_sample_is_fetched_once_then_cached(tmp_path):
    calls = []
    wav = _wav()

    def fetch(text):
        calls.append(text)
        return wav

    def face(text, data):
        return {"mouth": [[0.0, "open"], [0.1, "rest"]], "eyes": []}

    first = lipsync.sample("Yes, sir.", fetch=fetch, face=face, folder=tmp_path)
    second = lipsync.sample("Yes, sir.", fetch=fetch, face=face, folder=tmp_path)
    assert calls == ["Yes, sir."]
    assert first[0] == second[0] == wav
    assert second[1]["mouth"] == [[0.0, "open"], [0.1, "rest"]]


class _Clip:
    def __init__(self, seconds):
        self.seconds = seconds
        self.heard_at = 0.0
        self.heard_wall = 1000.0
        self.ended_at = 0.0
        self.started = threading.Event()
        self.finished = threading.Event()
        self.started.set()
        self.finished.set()


class _Output:
    def __init__(self):
        self.played = 0
        self.closed = False
        self.primed = False

    def prime(self, rate):
        self.primed = True

    def play(self, samples, rate):
        self.played += 1
        return _Clip(len(samples) / rate)

    def cut(self, clip):
        pass

    def close(self):
        self.closed = True


def test_serve_says_when_each_pass_is_heard_and_done(monkeypatch):
    monkeypatch.setattr(lipsync, "GAP_S", 0.0)
    out, output = io.StringIO(), _Output()
    track = {"mouth": [[0.0, "open"]], "eyes": [[0.0, "neutral"]]}
    assert lipsync.serve(out=out, rounds=2, source=lambda: (_wav(), track), output=output) == 0
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["event"] for line in lines] == ["status", "heard", "done", "heard", "done"]
    heard = lines[1]
    assert heard["start"] == 1000.0
    assert heard["cues"] == [[0.0, "open"]]
    assert abs(heard["duration"] - 0.2) < 0.01
    assert output.primed and output.closed and output.played == 2


def test_serve_reports_a_failed_sample():
    out = io.StringIO()

    def broken():
        raise RuntimeError("no key")

    assert lipsync.serve(out=out, rounds=1, source=broken, output=_Output()) == 1
    assert json.loads(out.getvalue().splitlines()[-1])["event"] == "error"


def test_tuner_command_points_at_the_tuner():
    command = lipsync.tuner_command("qs")
    assert command[:3] == ["qs", "-n", "-p"]
    assert command[3].endswith("presence/tuner.qml")
