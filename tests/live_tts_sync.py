"""Live check on banditbox: does his face move with the sound you hear?

Speaks a few real Cartesia replies through the real Speaker and ears.say_answer, so the running
orb animates them, while it records the monitor of the default sink: exactly what goes to the
speakers or headphones, stamped by the capture clock. Every clip is found in that recording by
cross-correlation, music or not. The monitor sees a sample one device buffer before the jack
does (8 to 18 ms on banditbox; through the Scarlett's own mic the room heard it 3 to 5 ms after
the playback clock said, 2026-11-13). Against that it logs, from the state files the orb reads,
when the talking face came on and went off and where the mouth track was anchored.

Run it from the repo root with the voice venv:

    .venv-voice/bin/python tests/live_tts_sync.py

It keeps the raw recording in captures/tts-sync/<time>.npz (pass that path to work the numbers
out again without speaking) and the numbers in <time>.json and exits non-zero when any offset is over LIMIT_MS.
The test never touches other sound (speech_hold off) and puts no card on the screen.
"""

import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice import ears, mouth, presence, tts  # noqa: E402
from voice import state as vstate  # noqa: E402

RATE = 48000
LIMIT_MS = 50.0
GAP_S = 2.5
LINES = {
    "short": "Yes, sir.",
    "first": "One moment, sir.",
    "final": "Done. The lamps are low and the music is quieter.",
    "cut": "Very well, sir. I shall read you the whole list, every item of it, slowly and with feeling.",
}
CUT_AFTER_S = 1.4


class Recorder:
    """The default sink's monitor, each block stamped on the wall clock at its capture time."""

    def __init__(self):
        import os
        import subprocess

        sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True).stdout.strip()
        self.sink = sink
        self.blocks = []
        self.lock = threading.Lock()
        os.environ["PIPEWIRE_PROPS"] = '{ target.object = "%s" stream.capture.sink = true node.name = "tts-sync-check" }' % sink
        try:
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=480,
                                         latency=0.02, callback=self._cb)
        finally:
            os.environ.pop("PIPEWIRE_PROPS", None)

    def _cb(self, indata, frames, ti, status):
        wall = time.time() + (ti.inputBufferAdcTime - ti.currentTime)
        with self.lock:
            self.blocks.append((wall, indata[:, 0].copy()))

    def audio(self):
        with self.lock:
            blocks = list(self.blocks)
        stamps = np.array([b[0] for b in blocks])
        index = np.cumsum([0] + [len(b[1]) for b in blocks[:-1]])
        fit = np.polyfit(index, stamps, 1)
        return np.concatenate([b[1] for b in blocks]), fit


class Watcher:
    """Every write to the two files the orb reads, stamped when it landed."""

    def __init__(self, paths):
        self.paths = paths
        self.events = []
        self.running = True
        self.seen = {p: None for p in paths}
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.running:
            for name, path in self.paths.items():
                try:
                    stamp = path.stat().st_mtime_ns
                except OSError:
                    continue
                if stamp != self.seen[name]:
                    self.seen[name] = stamp
                    try:
                        body = json.loads(path.read_text())
                    except (OSError, ValueError):
                        continue
                    self.events.append((time.time(), name, body))
            time.sleep(0.002)


def samples_of(wav_bytes):
    pcm, rate = mouth._pcm(wav_bytes)
    at48 = np.interp(np.arange(0, len(pcm), rate / RATE), np.arange(len(pcm)), pcm).astype(np.float32)
    return at48


def find(mic, fit, clip, around):
    """Wall time the clip's first sample was heard, searching near `around`."""
    first = int(np.clip((around - 1.0 - fit[1]) / fit[0], 0, len(mic)))
    last = int(np.clip((around + 3.0 - fit[1]) / fit[0] + len(clip), 0, len(mic)))
    window = mic[first:last]
    ref = clip[: min(len(clip), RATE)]
    n = 1 << int(np.ceil(np.log2(len(window) + len(ref))))
    corr = np.fft.irfft(np.fft.rfft(window, n) * np.conj(np.fft.rfft(ref, n)), n)[: len(window) - len(ref)]
    lag = int(np.argmax(np.abs(corr)))
    strength = float(np.abs(corr[lag]) / (np.median(np.abs(corr)) + 1e-12))
    return float(np.polyval(fit, first + lag)), strength


def heard_until(mic, fit, clip, start):
    """Wall time a cut clip stopped being heard: the end E for which the clip silenced from E
    on matches the recording best. Short windows are too noisy with music in the room."""
    begin = int(round((start - fit[1]) / fit[0]))
    span = min(len(clip) + RATE // 2, len(mic) - begin)
    heard = mic[begin:begin + span]
    played = np.zeros(span, np.float32)
    n = min(len(clip), span)
    played[:n] = clip[:n]
    best, end = -2.0, 0.0
    for e in np.arange(0.1, n / RATE, 0.005):
        trial = played.copy()
        trial[int(e * RATE):] = 0
        score = float(np.corrcoef(heard, trial)[0, 1])
        if score > best:
            best, end = score, e
    return start + end


def run():
    cfg = dict(ears.Settings(ears.load_config()).get(), speech_hold="off")
    mouth._aligner.measure = lambda *a, **k: None
    presence.show_reply = lambda *a, **k: None
    rec = Recorder()
    print("listening on", rec.sink)
    rec.stream.start()
    watch = Watcher({"state": vstate.STATE_DIR / "ears.json", "mouth": mouth.TRACK_FILE})
    speaker = tts.Speaker(cfg, ears._Quiet())
    fetched = []
    real_fetch = speaker._fetch

    def fetch(text, c):
        wav = real_fetch(text, c)
        fetched.append({"text": text, "wav": wav, "at": time.time()})
        return wav

    speaker._fetch = fetch
    marks = {}
    time.sleep(1.0)

    ears.set_state("thinking")
    marks["short_asked"] = time.time()
    ears.say_answer(None, speaker, LINES["short"], cfg, None, None)
    ears.set_state("asleep")
    time.sleep(2.5)

    ears.set_state("thinking")
    ears.say_answer(None, speaker, LINES["first"], cfg, None, None, first=True)
    marks["gap_from"] = time.time()
    time.sleep(GAP_S)
    marks["gap_to"] = time.time()
    ears.say_answer(None, speaker, LINES["final"], cfg, None, None)
    ears.set_state("asleep")
    time.sleep(2.5)

    ears.set_state("thinking")
    clip_seen = threading.Event()

    def cutter():
        while not fetched or fetched[-1]["text"] != LINES["cut"]:
            time.sleep(0.005)
        clip_seen.set()
        while True:
            hit = [e for e in watch.events if e[1] == "state" and e[2].get("state") == "speaking"
                   and e[0] > fetched[-1]["at"] - 5]
            if hit:
                break
            time.sleep(0.005)
        time.sleep(CUT_AFTER_S)
        marks["cut_called"] = time.time()
        speaker.stop_now()

    threading.Thread(target=cutter, daemon=True).start()
    ears.say_answer(None, speaker, LINES["cut"], cfg, None, None)
    ears.set_state("asleep")
    time.sleep(1.5)
    watch.running = False
    rec.stream.stop()
    mic, fit = rec.audio()
    return mic, fit, watch.events, fetched, marks


def save(path, mic, fit, events, fetched, marks):
    """Everything a run recorded, so the numbers can be worked out again without speaking."""
    clips = {f"clip{i}": np.frombuffer(item["wav"], dtype=np.uint8) for i, item in enumerate(fetched)}
    meta = {"fit": list(fit), "events": events, "marks": marks,
            "fetched": [{"text": i["text"], "at": i["at"]} for i in fetched]}
    np.savez_compressed(path, mic=mic, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8), **clips)


def load(path):
    data = np.load(path)
    meta = json.loads(bytes(data["meta"]).decode())
    fetched = [dict(item, wav=bytes(data[f"clip{i}"])) for i, item in enumerate(meta["fetched"])]
    return data["mic"], np.array(meta["fit"]), [tuple(e) for e in meta["events"]], fetched, meta["marks"]


def track_open(events, start, end, frame=0.01):
    """The orb's mouth, open or not, every 10 ms from start to end, replayed from the files the
    way Orb.qml plays them."""
    times = np.arange(start, end, frame)
    out = np.zeros(len(times))
    state, track = "asleep", {"start": 0, "duration": 0, "cues": []}
    queue = sorted(events, key=lambda e: e[0])
    j = 0
    for i, t in enumerate(times):
        while j < len(queue) and queue[j][0] <= t:
            if queue[j][1] == "state":
                state = queue[j][2].get("state")
            else:
                track = queue[j][2]
            j += 1
        if state != "speaking":
            continue
        cues = track.get("cues") or []
        if not cues:
            out[i] = 0.5
            continue
        rel = t - track["start"]
        shape = "rest"
        for at, s in cues:
            if at > rel:
                break
            shape = s
        if 0 <= rel <= track["duration"] and shape != "rest":
            out[i] = 1.0
    return times, out


def level(mic, fit, t, n=480):
    i = int((t - fit[1]) / fit[0])
    piece = mic[max(0, i):max(0, i) + n]
    return float(np.sqrt(np.mean(piece ** 2))) if len(piece) else 0.0


def best_lag(scores):
    scores = np.nan_to_num(np.array(scores, dtype=float), nan=-2.0)
    return None if scores.max() <= -2.0 else 10 * (int(np.argmax(scores)) - len(scores) // 2)


def analyse(mic, fit, events, fetched, marks):
    states = [(t, b.get("state")) for t, n, b in events if n == "state"]
    tracks = [(t, b) for t, n, b in events if n == "mouth"]
    rows = []
    for item in fetched:
        clip = samples_of(item["wav"])
        heard, strength = find(mic, fit, clip, item["at"])
        length = len(clip) / RATE
        cut = item["text"] == LINES["cut"]
        heard_end = heard_until(mic, fit, clip, heard) if cut else heard + length
        on = next((t for t, s in states if s == "speaking" and heard - 3.0 <= t <= heard + 3.0), None)
        off = next((t for t, s in states if s != "speaking" and on is not None and t > on), None)
        track = next((b for t, b in tracks if b.get("cues") and abs(b["start"] - heard) < 3.0), None)
        cleared = next((t for t, b in tracks if not b.get("cues") and t > heard), None)
        times, opened = track_open(events, heard - 0.5, heard_end + 0.5)
        env = np.array([level(mic, fit, t) for t in times])
        ref = np.array([np.sqrt(np.mean(clip[max(0, int((t - heard) * RATE)):max(0, int((t - heard) * RATE)) + 480] ** 2))
                        if 0 <= t - heard < length else 0.0 for t in times])
        lags = range(-30, 31)
        score = [np.corrcoef(np.roll(opened, k), ref)[0, 1] if np.std(opened) > 0 else 0 for k in lags]
        env_score = [np.corrcoef(np.roll(ref, k), env)[0, 1] for k in lags]
        rows.append({
            "line": next(k for k, v in LINES.items() if v == item["text"]),
            "match": round(strength, 1),
            "clip_ms": round(length * 1000),
            "fetched_to_heard_ms": round((heard - item["at"]) * 1000),
            "face_on_ms": None if on is None else round((on - heard) * 1000, 1),
            "mouth_anchor_ms": None if track is None else round((track["start"] - heard) * 1000, 1),
            "face_off_ms": None if off is None else round((off - heard_end) * 1000, 1),
            "mouth_cleared_ms": None if cleared is None else round((cleared - heard_end) * 1000, 1),
            "mouth_vs_sound_lag_ms": best_lag(score),
            "sound_vs_mic_lag_ms": best_lag(env_score),
        })
    gap = [s for t, s in states if marks["gap_from"] <= t <= marks["gap_to"]]
    last_before = [s for t, s in states if t < marks["gap_from"]]
    talking_in_gap = (last_before[-1:] == ["speaking"] and not gap) or "speaking" in gap
    cut = next((r for r in rows if r["line"] == "cut"), None)
    if cut is not None and marks.get("cut_called"):
        heard = next(i for i in fetched if i["text"] == LINES["cut"])
        start, _ = find(mic, fit, samples_of(heard["wav"]), heard["at"])
        end = heard_until(mic, fit, samples_of(heard["wav"]), start)
        cut["cut_to_silence_ms"] = round((end - marks["cut_called"]) * 1000, 1)
    return {"rows": rows, "talking_face_in_gap": talking_in_gap, "limit_ms": LIMIT_MS}


def verdict(report):
    bad = []
    for row in report["rows"]:
        for key in ("face_on_ms", "mouth_anchor_ms", "face_off_ms", "mouth_vs_sound_lag_ms"):
            value = row.get(key)
            if value is None or abs(value) > LIMIT_MS:
                bad.append(f"{row['line']}.{key}={value}")
    if report["talking_face_in_gap"]:
        bad.append("talking face during the gap")
    return bad


if __name__ == "__main__":
    out = ROOT / "captures" / "tts-sync"
    out.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1:
        raw = Path(sys.argv[1])
        recorded = load(raw)
    else:
        raw = out / time.strftime("%Y%m%d-%H%M%S.npz")
        recorded = run()
        save(raw, *recorded)
    report = analyse(*recorded)
    report["bad"] = verdict(report)
    path = raw.with_suffix(".json")
    path.write_text(json.dumps(report, indent=1))
    for row in report["rows"]:
        print(json.dumps(row))
    print("talking face in the gap:", report["talking_face_in_gap"])
    print("over the limit:", report["bad"] or "none", "->", path)
    sys.exit(1 if report["bad"] else 0)
