"""Live voice test: who does the microphone sound like right now.

A tap on the capture feeds a rolling window. As fast as the encoder allows,
the window is scored against every enrolled voice. The raw scores are noisy,
so each voice also carries a smoothed value, a peak that holds and then
falls, and running stats for the verdict at the end of the session.
"""

import logging
import math
import queue
import threading
import time

import numpy as np

from . import state as vstate
from . import voiceid

log = logging.getLogger("diablo.tester")

WINDOW_S = 2.0
MIN_WINDOW_S = 1.0
TICK_S = 0.1
TAU_S = 0.5
PEAK_HOLD_S = 1.0
PEAK_FALL_PER_S = 0.25
RMS_FLOOR = 0.002
SPEECH_PROB = 0.5
SPEECH_TARGET_S = 60.0
POST_AIM_SILENCE_S = 15.0


def blank():
    return {
        "active": False,
        "scores": {},
        "smooth": {},
        "peak": {},
        "peak_at": {},
        "stats": {},
        "started": 0.0,
        "ticks": 0,
        "leader": "",
        "rms": 0.0,
        "speech": 0.0,
        "done": False,
        "silence": 0.0,
        "ts": 0.0,
        "heard": False,
        "playing": "",
    }


def fold(progress, scores, dt, now):
    """One scored tick: smooth toward the raw scores, hold and drop the peaks, count the lead."""
    factor = 1 - math.exp(-dt / TAU_S)
    smooth, peak, peak_at, stats = (
        progress["smooth"], progress["peak"], progress["peak_at"], progress["stats"]
    )
    for name, raw in scores.items():
        raw = float(raw)
        if name not in smooth:
            smooth[name] = raw
        else:
            smooth[name] += (raw - smooth[name]) * factor
        if name not in peak or raw >= peak[name]:
            peak[name] = raw
            peak_at[name] = now
        else:
            falling = min(dt, now - peak_at[name] - PEAK_HOLD_S)
            if falling > 0:
                peak[name] = max(smooth[name], peak[name] - PEAK_FALL_PER_S * falling)
        row = stats.setdefault(name, {"n": 0, "sum": 0.0, "max": -1.0, "lead": 0})
        row["n"] += 1
        row["sum"] += raw
        row["max"] = max(row["max"], raw)
    progress["scores"] = dict(scores)
    if smooth:
        leader = max(smooth, key=smooth.get)
        stats[leader]["lead"] += 1
        progress["leader"] = leader
    progress["ticks"] += 1


def rank(scores, minimum):
    """Rows for the terminal: name, score, whether it clears the gate, best first."""
    rows = [(name, float(score), float(score) >= minimum) for name, score in scores.items()]
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows


def state(progress, cfg):
    minimum = float(cfg.get("voiceprint_min", 0.70))
    ranked = rank(progress["smooth"], minimum)
    rows = [
        {
            "name": n,
            "score": round(s, 3),
            "raw": round(float(progress["scores"].get(n, s)), 3),
            "peak": round(float(progress["peak"].get(n, s)), 3),
            "passes": p,
        }
        for n, s, p in ranked
    ]
    margin = round(ranked[0][1] - ranked[1][1], 3) if len(ranked) > 1 else None
    ticks = int(progress["ticks"])
    summary = [
        {
            "name": n,
            "mean": round(st["sum"] / st["n"], 3),
            "max": round(st["max"], 3),
            "lead": round(st["lead"] / ticks, 3),
        }
        for n, st in progress["stats"].items()
        if st["n"]
    ] if ticks else []
    summary.sort(key=lambda row: row["mean"], reverse=True)
    started = progress.get("started") or 0.0
    return {
        "active": bool(progress["active"]),
        "rows": rows,
        "leader": progress.get("leader", ""),
        "margin": margin,
        "elapsed": round(time.time() - started, 1) if started else 0,
        "ticks": ticks,
        "summary": summary,
        "rms": round(float(progress["rms"]), 5),
        "speech": round(float(progress.get("speech", 0.0)), 1),
        "speech_target": SPEECH_TARGET_S,
        "done": bool(progress.get("done")),
        "heard": bool(progress["heard"]),
        "ts": progress["ts"],
        "playing": progress.get("playing", ""),
        "minimum": minimum,
        "profiles": voiceid.profiles(),
    }


def _run(capture, progress, score_fn, vad_path=None):
    rate = capture.sample_rate
    vad = None
    if vad_path:
        try:
            from .vad import SileroVAD

            vad = SileroVAD(vad_path)
        except Exception:
            log.warning("no speech gate for the test, any audible audio counts")
    tap = queue.SimpleQueue()
    capture.taps.append(tap)
    window = np.zeros(0, dtype=np.float32)
    keep = int(WINDOW_S * rate)
    enough = int(MIN_WINDOW_S * rate)
    last_tick = 0.0
    last_scored = 0.0
    try:
        while progress["active"]:
            try:
                chunk, _explained = tap.get(timeout=0.5)
            except queue.Empty:
                continue
            window = np.concatenate([window, chunk])[-keep:]
            now = time.monotonic()
            if now - last_tick < TICK_S or len(window) < enough:
                continue
            last_tick = now
            rms = float(np.sqrt(np.mean(np.square(window))))
            progress["rms"] = rms
            if not progress.get("playing"):
                speaking = True
                if vad is not None:
                    tail = window[-512:]
                    speaking = len(tail) == 512 and float(vad.prob(tail)) >= SPEECH_PROB
                elif rms >= RMS_FLOOR:
                    speaking = True
                else:
                    speaking = False
                if speaking:
                    progress["speech"] = progress.get("speech", 0.0) + TICK_S
                    progress["silence"] = 0.0
                    if progress["speech"] >= SPEECH_TARGET_S:
                        progress["done"] = True
                else:
                    progress["silence"] = progress.get("silence", 0.0) + TICK_S
                    if progress.get("done") and progress["silence"] >= POST_AIM_SILENCE_S:
                        progress["active"] = False
            if rms < RMS_FLOOR:
                progress["heard"] = False
                continue
            scores = score_fn(window, rate)
            if scores:
                dt = now - last_scored if last_scored else TICK_S
                last_scored = now
                fold(progress, scores, dt, now)
            progress["heard"] = True
            progress["ts"] = time.time()
    except Exception:
        log.exception("voice test died")
    finally:
        if tap in capture.taps:
            capture.taps.remove(tap)
        progress["active"] = False
        if progress.pop("was_deaf", False):
            vstate.set_deaf(True)


def start(capture, progress, score_fn=voiceid.scores, vad_path=None, was_deaf=False):
    if progress["active"]:
        return False
    progress.update(blank())
    progress["active"] = True
    progress["started"] = time.time()
    progress["was_deaf"] = was_deaf
    threading.Thread(
        target=_run, args=(capture, progress, score_fn, vad_path), daemon=True
    ).start()
    return True


def stop(progress):
    progress["active"] = False
