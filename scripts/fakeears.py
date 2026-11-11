#!/usr/bin/env python3
"""A stand-in for the ears' control panel (voice/ears_api.py), for screenshots of the Voice page.

    fakeears.py PORT SCENE

SCENE is one of untrained, names, speech, room, learning, trying, done, trained, refuses (every action fails), failed,
or cycle: a turn every 18.5 s for the chat's presence mark, asleep, the name, listening,
thinking and speaking, then a call that never becomes a request, with the mic and speaker
levels of a voice.
The meters move, so a shot looks like a live page. Nothing touches a microphone.
"""

import json
import math
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, SCENE = int(sys.argv[1]), sys.argv[2]
PLACES = [
    {"id": "desk", "label": "At your desk", "count": 6, "intro": "Let's start at your desk."},
    {"id": "bed", "label": "From your bed", "count": 5, "intro": "Now go to your bed and call me from there."},
    {"id": "far", "label": "Across the room", "count": 5, "intro": "Now go as far from the microphone as the room allows."},
    {"id": "anywhere", "label": "Anywhere, any way", "count": 4, "intro": "Anywhere you like, any way you'd say it."},
]
SENTENCE = "Diego drew a diagram of the bubble, then said the able one should have known better than that."


def env(start, end, loud=0.7, n=64, take_s=2.6):
    return [round(loud * (0.6 + 0.4 * math.sin(i)) if start <= (i + 0.5) / n * take_s <= end else 0.08 + 0.03 * (i % 3), 3) for i in range(n)]


MODEL = {
    "trained_at": "2026-11-09T19:42:10", "threshold": 0.77, "patience": 2, "recordings": 20, "held_out": 4,
    "held_out_recall_clean": 1.0, "held_out_recall_noisy": 0.92, "validation_hours": 10.7, "validation_fires": 0,
    "seconds": 139.0, "ready": True, "speaker_min": 0.86, "speaker_self_low": 0.91, "speaker_self_mean": 0.95,
    "talk_hours": 2.1, "talk_fires": 0, "talk_min": 0.62, "talk_pieces": 20000,
    "dictation": {"clips": 902, "used": 798, "used_hours": 8.33, "says_name": 43, "little_speech": 46, "other_voice": 15},
}


def takes(n, misses=0):
    out = []
    order = ["desk"] * 6 + ["bed"] * 5 + ["far"] * 5 + ["anywhere"] * 4
    for i in range(n):
        sec = 0.52 + 0.03 * (i % 5)
        out.append({"ok": True, "code": "ok", "why": "", "seconds": sec, "start": 0.4, "end": 0.4 + sec,
                    "place": order[i], "file": f"{i:03d}.wav", "take_s": 2.6, "envelope": env(0.4, 0.4 + sec),
                    "detail": f"The name took {sec:.2f} s, at -14 dB over a room at -48 dB."})
        if i == 7 and misses:
            out.append({"ok": False, "code": "early", "why": "You started before I was listening.", "seconds": 0.4,
                        "start": 0.0, "end": 0.4, "place": "bed", "take_s": 2.6, "envelope": env(0.0, 0.4),
                        "detail": "The name was already going when the take began, so its start is missing. Say it just after the tick."})
    return out


def session(stage, **kw):
    base = {"active": True, "stage": stage, "said": "", "passage": "", "recording": False, "take_s": 2.6,
            "take_left": 0.0, "group": "", "round": 0, "rounds": 20, "takes": [], "tries": [], "progress": 0.0,
            "log": [], "metrics": None, "error": "", "paused": [], "retrain": False, "started": time.time() - 60,
            "places": [{k: p[k] for k in ("id", "label", "count")} for p in PLACES], "moving": False, "move_left": 0.0,
            "last": None, "prompt": "", "word": "", "notname": [], "heard": "", "marks": []}
    base.update(kw)
    return base


def status():
    t = time.time()
    wobble = 0.5 + 0.5 * math.sin(t * 3.1)
    mic = 0.004 + 0.03 * wobble
    out = {"state": "asleep", "listening": True, "trained": False, "model": None,
           "samples": {"pos": 0, "speech": 0, "room": 0},
           "live": {"mic": mic, "speakers": 0.0, "score": 0.0, "peak": 0.0, "threshold": None, "voice_min": None},
           "wakes": [], "session": None, "places": PLACES}
    if SCENE == "untrained":
        out["state"] = "untrained"
    elif SCENE == "names":
        out["state"] = "training"
        t = takes(8, misses=1)
        out["session"] = session("names", said="Now go to your bed and call me from there.", group="bed", round=9,
                                 recording=True, take_left=1.1, takes=t, last=t[-2], paused=["spotify"])
        out["live"]["mic"] = 0.09
    elif SCENE == "moving":
        out["state"] = "training"
        out["session"] = session("names", said="Now go to your bed and call me from there. I'll tick in 12 seconds, or press I'm there.",
                                 group="bed", moving=True, move_left=8.3, takes=takes(6))
    elif SCENE == "notname":
        out["state"] = "training"
        out["session"] = session("notname", said="Now some words that are not my name. Say each word on screen after the tick.",
                                 word="Diego", recording=True, take_s=2.2, take_left=1.4, takes=takes(20),
                                 notname=[{"word": "Diplo", "seconds": 0.5, "envelope": []}, {"word": "Tableau", "seconds": 0.6, "envelope": []}])
    elif SCENE == "speech":
        out["state"] = "training"
        words = SENTENCE.split()
        marks = ["hit"] * 7 + ["miss"] + ["hit"] * 4 + [""] * (len(words) - 12)
        out["session"] = session("speech", said="Now read each sentence on screen out loud. The words light up as I hear them.",
                                 passage=SENTENCE, prompt="sentence", round=1, recording=True, take_s=9.0, take_left=5.2,
                                 takes=takes(20), marks=marks, heard="diego drew a diagram of the bubble said the able one")
    elif SCENE == "room":
        out["state"] = "training"
        out["session"] = session("room", said="Last one. Stay quiet for ten seconds while I listen to the room.",
                                 recording=True, take_s=10.0, take_left=6.4, takes=takes(20))
        out["live"]["mic"] = 0.002
    elif SCENE == "learning":
        out["state"] = "training"
        out["session"] = session("learning", said="Give me a minute while I learn it.", progress=0.61, takes=takes(20),
                                 log=["20 recordings of the name, 2 of speech, 1 of the room", "checking on recordings held out of training",
                                      "  step 2000/4000, loss 0.0017"])
    elif SCENE == "notready":
        bad = dict(MODEL, ready=False, validation_fires=138, threshold=0.97, held_out_recall_clean=0.75, left_out={"early": 8, "late": 1})
        out.update(state="asleep", trained=True, model=bad, samples={"pos": 42, "speech": 2, "room": 1})
        out["live"].update(threshold=0.97, voice_min=0.73)
    elif SCENE == "trying":
        out.update(state="training", trained=True, model=MODEL)
        out["live"].update(threshold=0.77, voice_min=0.86, score=0.93 * wobble, peak=0.95)
        out["session"] = session("trying", said="Say my name whenever you like, and I'll chime when I hear it. Three tries.",
                                 takes=takes(20), metrics=MODEL, progress=1.0,
                                 tries=[{"score": 0.98, "voice": 0.94, "ok": True}, {"score": 0.91, "voice": 0.96, "ok": True}])
    elif SCENE == "done":
        out.update(state="asleep", trained=True, model=MODEL, samples={"pos": 20, "speech": 2, "room": 1})
        out["live"].update(threshold=0.77, voice_min=0.86)
        out["session"] = session("done", active=False, said="That works. From now on, say my name, wait for the chime, and talk.",
                                 takes=takes(20), metrics=MODEL, progress=1.0)
    elif SCENE in ("trained", "refuses"):
        model = dict(MODEL, talk_fires=2, talk_fire_clips=[
            {"clip": "1794100000.1.wav", "end_s": 3.84}, {"clip": "1794100100.2.wav", "end_s": 11.2}])
        out.update(state="asleep", trained=True, model=model, samples={"pos": 20, "speech": 2, "room": 1})
        out["live"].update(threshold=0.77, voice_min=0.86, score=0.12 * wobble, peak=0.31, speakers=0.05)
        out["wakes"] = [
            {"at": t - 3600, "score": 0.97, "voice": 0.95, "outcome": "answered", "clip": "1794200000000"},
            {"at": t - 1500, "score": 0.88, "voice": None, "outcome": "echo", "clip": "1794200001000"},
            {"at": t - 600, "score": 0.91, "voice": 0.62, "outcome": "voice", "clip": "1794200002000"},
            {"at": t - 90, "score": 0.99, "voice": 0.96, "outcome": "answered", "clip": None},
        ]
    elif SCENE == "failed":
        out["state"] = "untrained"
        out["samples"] = {"pos": 6, "speech": 0, "room": 0}
        out["session"] = session("failed", active=False, takes=takes(6),
                                 error="The computer kept playing sound during every take. Stop it, a game, a call or a tab, and start again. The takes so far are kept.")
    return out


CYCLE = [("asleep", 3.0), ("waking", 0.5), ("listening", 3.5), ("thinking", 3.0), ("speaking", 4.0),
         ("asleep", 2.0), ("waking", 0.5), ("listening", 2.0)]


def cycle():
    t = time.time() % sum(d for _, d in CYCLE)
    for state, length in CYCLE:
        if t < length:
            break
        t -= length
    voice = 0.02 + 0.18 * abs(math.sin(t * 9.0) * math.sin(t * 2.3 + 1))
    return {"state": state, "mic": voice if state in ("waking", "listening") else 0.003,
            "speakers": voice if state == "speaking" else 0.0, "score": 0.0, "peak": 0.0,
            "listening": True, "trained": True, "wakes": [], "places": [], "samples": {"pos": 20, "speech": 2, "room": 1}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if SCENE == "cycle":
            return self._send(cycle())
        s = status()
        if self.path.startswith("/v1/ears/live"):
            return self._send({**s["live"], "state": s["state"]})
        self._send(s)

    def do_POST(self):
        if SCENE == "refuses":
            data = json.dumps({"error": "OSError: [Errno 98] address already in use"}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._send({"ok": True})


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
