"""The ears' control panel on 127.0.0.1:8758, for the Voice page of the desktop app.

    GET  /v1/ears                    everything the page draws, polled about 3 times a second
    GET  /v1/ears/live               only the meters and the state, polled about 30 times a second
    POST /v1/ears/train              {"fresh", "retrain", "places": [ids]} starts a setup session
    POST /v1/ears/train/stop         stops it; takes already saved are kept
    POST /v1/ears/train/ready        at the next place: start its takes now
    POST /v1/ears/train/skip         leave out the rest of this place
    POST /v1/ears/listen             {"on": bool} turns listening for the name on or off
    POST /v1/ears/play               {"kind": "wake", "id"} or {"kind": "talk", "clip", "end_s"}
                                     plays back what woke him, through the speakers

POSTs need the header X-Diablo-Ears: 1. A web page cannot send a custom header to another
origin without a preflight, which this server never answers. Requests whose Host is not
loopback are refused, which closes DNS rebinding. It binds to loopback only.
"""

import json
import logging
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import chime, wake

log = logging.getLogger("diablo.ears.api")

PORT = 8758
WAKES_KEPT = 12
# A short clip of the room around every wake, so he can hear what woke Diablo.
WAKE_CLIPS = Path.home() / ".local/state/diablo/wakes"
WAKE_LOG = WAKE_CLIPS / "log.json"
# A few more than the list shows, so a clip outlives its dot by a little.
CLIPS_KEPT = WAKES_KEPT * 2
_ID_RE = re.compile(r"^[0-9]{10,16}$")
# The dictation check plays this much of his talk before and after the wake.
TALK_BEFORE_S, TALK_AFTER_S = 3.2, 0.8


def keep_clip(audio, at=None):
    """Writes the audio around a wake, keeps the newest CLIPS_KEPT. Returns its id or None."""
    clip = str(int((at or time.time()) * 1000))
    try:
        wake.write_wav(WAKE_CLIPS / f"{clip}.wav", audio)
        old = sorted(WAKE_CLIPS.glob("*.wav"))[:-CLIPS_KEPT]
        for path in old:
            path.unlink(missing_ok=True)
    except (OSError, ValueError) as error:
        log.warning("wake clip not kept: %s", error)
        return None
    return clip
# From the moment the wake word fires to the end of his reply, the mic belongs to the turn.
BUSY = ("waking", "listening", "thinking", "speaking")


class Live:
    """What the service is doing right now, written by the service and the session."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = "starting"
        self.holding = False
        self._score = 0.0
        self._threshold = None
        self._peak = 0.0
        self.wakes = self._load()

    @staticmethod
    def _load():
        """The last wakes survive a restart, so a loose one at night can be heard in the morning."""
        try:
            wakes = json.loads(WAKE_LOG.read_text())
        except (OSError, ValueError):
            return []
        return [w for w in wakes if isinstance(w, dict)][-WAKES_KEPT:]

    def set_state(self, state):
        with self.lock:
            self.state = state
            self.holding = self.holding and state == "listening"

    def set_holding(self, on):
        with self.lock:
            self.holding = bool(on)

    def score(self, value, threshold=None):
        with self.lock:
            self._score = float(value)
            if threshold is not None:
                self._threshold = float(threshold)
            # Peak hold with a slow fall, so a 160 ms wake is visible on a 10 Hz poll.
            self._peak = max(self._peak * 0.9, self._score)

    def wake(self, score, voice, outcome, clip=None):
        with self.lock:
            self.wakes = (self.wakes + [{
                "at": time.time(), "score": round(float(score), 3),
                "voice": None if voice is None else round(float(voice), 3), "outcome": outcome,
                "clip": clip,
            }])[-WAKES_KEPT:]
            wakes = list(self.wakes)
        try:
            WAKE_CLIPS.mkdir(parents=True, exist_ok=True)
            tmp = WAKE_LOG.with_suffix(".tmp")
            tmp.write_text(json.dumps(wakes))
            tmp.replace(WAKE_LOG)
        except OSError:
            pass

    def snapshot(self):
        with self.lock:
            return {"state": self.state, "holding": self.holding, "score": self._score, "peak": self._peak,
                    "threshold": self._threshold, "wakes": list(self.wakes)}


class Controller:
    """Glue between the HTTP handlers and the service loop."""

    def __init__(self, live, start_session, listening, set_listening, mic_level, speaker_level):
        self.live = live
        self.start_session = start_session
        self.listening = listening
        self.set_listening = set_listening
        self.mic_level = mic_level
        self.speaker_level = speaker_level
        self.session = None
        self.lock = threading.Lock()
        self.player = None
        self.playing_until = 0.0

    def levels(self):
        """The cheap part, for the meters: no files read."""
        live = self.live.snapshot()
        return {
            "state": live["state"],
            "holding": live["holding"],
            "mic": self.mic_level(),
            "speakers": self.speaker_level(),
            "score": live["score"],
            "peak": live["peak"],
        }

    def status(self):
        from . import ears_train

        live = self.live.snapshot()
        info = wake.meta() if wake.trained() else None
        return {
            "state": live["state"],
            "holding": live["holding"],
            "listening": self.listening(),
            "trained": info is not None,
            "model": info,
            "samples": ears_train.saved(),
            # The meters come from GET /v1/ears/live; reading the mic here would reset its peak.
            "live": {
                "speakers": self.speaker_level(),
                "score": live["score"],
                "peak": live["peak"],
                "threshold": live["threshold"] if live["threshold"] is not None else (info or {}).get("threshold"),
                "voice_min": (info or {}).get("speaker_min"),
            },
            "wakes": live["wakes"],
            "session": self.session.snapshot() if self.session is not None else None,
            "places": [{k: p[k] for k in ("id", "label", "count", "intro")} for p in ears_train.PLACES],
        }

    def train(self, body):
        # One at a time, even when two requests land together.
        with self.lock:
            if self.session is not None and self.session.active:
                return 409, {"error": "A setup is already running."}
            if self.live.snapshot()["state"] in BUSY:
                return 409, {"error": "He is in the middle of a turn. Try again in a moment."}
            if body.get("retrain") and ears_samples_short():
                return 400, {"error": "Not enough saved takes to learn from yet. Record them first."}
            places = [str(p) for p in body.get("places") or []]
            self.session = self.start_session(fresh=bool(body.get("fresh")), retrain=bool(body.get("retrain")), places=places)
            return 200, {"ok": True}

    def stop(self):
        if self.session is None or not self.session.active:
            return 200, {"ok": True, "running": False}
        self.session.stop()
        return 200, {"ok": True, "running": True}

    def nudge(self, what):
        """I'm there, or skip this place, during the countdown between places."""
        if self.session is None or not self.session.active:
            return 409, {"error": "No setup is running."}
        (self.session.go if what == "ready" else self.session.skip_place)()
        return 200, {"ok": True}

    def listen(self, body):
        self.set_listening(bool(body.get("on", True)))
        return 200, {"ok": True, "listening": self.listening()}

    def playing(self):
        """True while a clip plays back, so its own "Diablo" never wakes him."""
        return time.monotonic() < self.playing_until

    def play(self, body):
        kind = str(body.get("kind") or "")
        if kind == "wake":
            clip = str(body.get("id") or "")
            path = WAKE_CLIPS / f"{clip}.wav"
            if not _ID_RE.match(clip) or not path.exists():
                return 404, {"error": "That clip is gone; only the latest wakes keep theirs."}
            audio = wake.read_wav(path)
        elif kind == "talk":
            audio, error = self._talk_piece(body)
            if error:
                return 404, {"error": error}
        else:
            return 400, {"error": "kind must be wake or talk"}
        self._play(audio)
        return 200, {"ok": True, "seconds": round(len(audio) / wake.RATE, 2)}

    @staticmethod
    def _talk_piece(body):
        """The stretch of a held out dictation he woke on, only one the model itself names."""
        from . import dictation

        clip, end_s = str(body.get("clip") or ""), body.get("end_s")
        named = (wake.meta() or {}).get("talk_fire_clips") or []
        if not any(f.get("clip") == clip and f.get("end_s") == end_s for f in named):
            return None, "He did not wake on that dictation."
        try:
            audio = wake.read_wav(dictation.CLIPS / clip)
        except (OSError, ValueError):
            return None, "gvoice no longer has that dictation."
        end = int(float(end_s) * wake.RATE)
        start = max(0, end - int(TALK_BEFORE_S * wake.RATE))
        return audio[start: end + int(TALK_AFTER_S * wake.RATE)], None

    def _play(self, audio):
        with self.lock:
            if self.player is not None and self.player.poll() is None:
                self.player.kill()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                path = Path(handle.name)
            wake.write_wav(path, audio)
            self.playing_until = time.monotonic() + len(audio) / wake.RATE + 1.0
            try:
                self.player = subprocess.Popen([*chime.PLAYER, str(path)],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as error:
                self.playing_until = 0.0
                path.unlink(missing_ok=True)
                raise RuntimeError(f"pw-play failed: {error}")
            threading.Thread(target=lambda p=self.player: (p.wait(), path.unlink(missing_ok=True)),
                             daemon=True).start()


def ears_samples_short():
    """Fewer than 10 takes training would actually use, judged the way training judges them."""
    usable = 0
    for path in (wake.SAMPLES / "pos").glob("*.wav"):
        try:
            usable += wake.judge(wake.read_wav(path))["ok"]
        except (OSError, ValueError):
            continue
        if usable >= 10:
            return False
    return True


def handler_for(controller):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _local(self):
            # A DNS rebinding page reaches 127.0.0.1 under its own host name; refuse it.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def do_GET(self):
            if not self._local():
                return self._send(403, {"error": "local only"})
            path = self.path.split("?")[0]
            if path == "/v1/ears/live":
                return self._send(200, controller.levels())
            if path != "/v1/ears":
                return self._send(404, {"error": "not found"})
            try:
                self._send(200, controller.status())
            except Exception as error:
                log.exception("status failed")
                self._send(500, {"error": str(error)})

        def do_POST(self):
            if not self._local():
                return self._send(403, {"error": "local only"})
            if self.headers.get("X-Diablo-Ears") != "1":
                return self._send(403, {"error": "missing X-Diablo-Ears header"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except ValueError:
                return self._send(400, {"error": "bad json"})
            path = self.path.split("?")[0]
            try:
                if path == "/v1/ears/train":
                    return self._send(*controller.train(body))
                if path == "/v1/ears/train/stop":
                    return self._send(*controller.stop())
                if path in ("/v1/ears/train/ready", "/v1/ears/train/skip"):
                    return self._send(*controller.nudge(path.rsplit("/", 1)[1]))
                if path == "/v1/ears/listen":
                    return self._send(*controller.listen(body))
                if path == "/v1/ears/play":
                    return self._send(*controller.play(body))
            except Exception as error:
                log.exception("request failed")
                return self._send(500, {"error": str(error)})
            return self._send(404, {"error": "not found"})

    return Handler


def serve(controller, port=PORT, host="127.0.0.1"):
    server = ThreadingHTTPServer((host, port), handler_for(controller))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="ears-api", daemon=True).start()
    log.info("control panel on http://%s:%d/v1/ears", host, port)
    return server
