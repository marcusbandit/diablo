"""Train his voice model from the dashboard: three rounds, eight seconds of
actual speech each, silence never counts toward the round.

Every accepted round is blended into the voiceprint profile through
voiceid.enroll, which averages embeddings instead of overwriting, so a
session only adds evidence and never replaces what is already there.
Deleting the profile is a separate, destructive act that demands explicit
confirmation and is refused while a session is running.
"""

import logging
import queue
import threading
import time

import numpy as np

from . import activity, phrases, voiceid
from . import state as vstate

log = logging.getLogger("diablo.trainer")

ROUNDS = 3
SECONDS = 8
RMS_FLOOR = 0.002
SPEECH_PROB = 0.5
ROUND_TIMEOUT_S = 90


def blank():
    return {
        "active": False,
        "stage": "idle",
        "enrolled": 0,
        "last": "",
        "left": None,
        "speech": 0.0,
        "speech_target": SECONDS,
        "name": "default",
    }


def record(capture, seconds, timeout=15):
    tap = queue.SimpleQueue()
    capture.taps.append(tap)
    try:
        return collect(tap, seconds, capture.sample_rate, timeout=timeout)
    finally:
        if tap in capture.taps:
            capture.taps.remove(tap)


def _speech_vad(cfg):
    try:
        from .vad import SileroVAD

        return SileroVAD(cfg["silero_path"])
    except Exception:
        log.warning("no speech gate for training, any audible audio counts")
        return None


def _speech_record(capture, seconds, progress, rate, vad, timeout=ROUND_TIMEOUT_S, pause_s=2.0):
    """Collect audio until `seconds` of real speech arrived, silence excluded.

    Only chunks the speech gate accepts are kept, so the enrollment audio is
    speech alone. The aim does not cut him off: once it is met the round ends
    on his first real pause, and until then he may talk as long as he likes.
    Gives up after `timeout` seconds of wall time and returns whatever speech
    it got, possibly None.
    """
    tap = queue.SimpleQueue()
    capture.taps.append(tap)
    progress["speech_target"] = seconds
    progress["speech"] = 0.0
    progress["left"] = float(seconds)
    deadline = time.monotonic() + timeout
    chunks = []
    got = 0.0
    quiet = 0.0
    try:
        while True:
            if got >= seconds and quiet >= pause_s:
                break
            if time.monotonic() >= deadline:
                break
            try:
                chunk, _explained = tap.get(timeout=1.0)
            except queue.Empty:
                continue
            speaking = True
            if vad is not None and len(chunk) >= 512:
                speaking = float(vad.prob(chunk[-512:])) >= SPEECH_PROB
            if not speaking:
                quiet += len(chunk) / rate
                continue
            chunks.append(chunk)
            got += len(chunk) / rate
            quiet = 0.0
            progress["speech"] = round(got, 1)
            progress["left"] = max(0.0, round(seconds - got, 1))
        if not chunks:
            return None
        return np.concatenate(chunks)
    finally:
        if tap in capture.taps:
            capture.taps.remove(tap)
        progress["left"] = None


def collect(tap, seconds, rate, timeout=15):
    need = int(seconds * rate)
    chunks = []
    got = 0
    while got < need:
        chunk, _explained = tap.get(timeout=timeout)
        chunks.append(chunk)
        got += len(chunk)
    return np.concatenate(chunks)[:need]


AGENT_LINES = [
    "I am Diablo, primordial demon butler in the service of Marcus, sir.",
    "This voice is mine alone, sir, and sir alone may command it.",
    "By these words I know myself, and I know my master's voice.",
]


def run_agent_rounds(cfg, speaker, capture, progress):
    """He records himself: the mic hears the speakers while he speaks. No one else needed."""
    rate = capture.sample_rate
    vad = _speech_vad(cfg)
    progress.update({"active": True, "stage": "briefing", "enrolled": 0, "last": "", "left": None})
    speak(speaker, cfg, "As you wish, sir. I will learn my own voice. Three rounds, eight seconds each.")
    for rnd, line in enumerate(AGENT_LINES, start=1):
        tap = queue.SimpleQueue()
        capture.taps.append(tap)
        try:
            progress["stage"] = f"round {rnd} of {ROUNDS}: speaking into the microphone"
            speak(speaker, cfg, line)
            progress["stage"] = f"round {rnd} of {ROUNDS}: recording"
            try:
                audio = _speech_record(capture, SECONDS, progress, rate, vad)
            except queue.Empty:
                progress["last"] = f"round {rnd} failed"
                continue
            if audio is None:
                progress["last"] = f"round {rnd}: too quiet, once more"
                continue
            try:
                voiceid.enroll(audio, rate, name="agent")
            except Exception as error:
                log.warning("agent enroll failed: %s", error)
                progress["last"] = f"round {rnd} enroll failed"
                continue
            progress["enrolled"] += 1
            progress["last"] = f"round {rnd} added"
        finally:
            if tap in capture.taps:
                capture.taps.remove(tap)
    progress["stage"] = "done"
    if progress["enrolled"]:
        voiceid.record_session("agent", progress["enrolled"], SECONDS)
    samples = profile_samples("agent")
    speak(speaker, cfg, f"That completes my self-training, sir. My voice model holds {samples} samples.")
    progress["active"] = False
    return progress["enrolled"]


def cue(cfg, speaker, progress, sleep=time.sleep):
    """The countdown before a round: a cached clip whose length is known, never a fresh line.

    play_wav returns when the clip has finished, so recording starts the
    instant the countdown ends. Without a cached clip he stays silent and the
    terminal carries the count alone.
    """
    clip = phrases.get(cfg, "countdown")
    if clip:
        progress["stage"] = progress["stage"] + ": three, two, one"
        speaker.play_wav(clip[1], interruptible=False)
    else:
        for left in (3, 2, 1):
            progress["left"] = float(left)
            sleep(0.5)
    progress["left"] = None


def run_rounds(cfg, speaker, capture, progress, enroll_fn=None, record_fn=None, sleep=time.sleep):
    """Marcus trains his voice. He never speaks here except the cached countdown."""
    name = progress.get("name") or "default"
    enroll_fn = enroll_fn or (lambda audio, rate: voiceid.enroll(audio, rate, name=name))
    rate = capture.sample_rate
    vad = _speech_vad(cfg)
    progress.update({"active": True, "stage": "starting", "enrolled": 0, "last": "", "left": None})
    for rnd in range(1, ROUNDS + 1):
        done = False
        for _attempt in range(2):
            progress["stage"] = f"round {rnd} of {ROUNDS}"
            progress["left"] = None
            cue(cfg, speaker, progress, sleep=sleep)
            progress["stage"] = f"round {rnd} of {ROUNDS}: recording"
            try:
                if record_fn is not None:
                    audio = record_fn(capture, SECONDS)
                else:
                    audio = _speech_record(capture, SECONDS, progress, rate, vad)
            except queue.Empty:
                progress["last"] = f"round {rnd}: the microphone gave nothing, once more"
                continue
            if audio is None:
                progress["last"] = f"round {rnd}: too quiet, once more a little louder"
                continue
            rms = float(np.sqrt(np.mean(np.square(audio))))
            if rms < RMS_FLOOR:
                progress["last"] = f"round {rnd}: too quiet, once more a little louder"
                continue
            try:
                enroll_fn(audio, rate)
            except Exception as error:
                log.warning("enroll failed: %s", error)
                progress["last"] = f"round {rnd}: the encoder slipped, once more"
                continue
            progress["enrolled"] += 1
            progress["last"] = f"round {rnd} added"
            done = True
            break
        if not done:
            progress["last"] = f"round {rnd} skipped"
    progress["stage"] = "done"
    if progress["enrolled"]:
        voiceid.record_session(name, progress["enrolled"], SECONDS)
    progress["last"] = f"{progress['enrolled']} of {ROUNDS} rounds added to {name}"
    activity.log_event("tool", f"voice training: {progress['last']}")
    progress["active"] = False
    return progress["enrolled"]


def speak(speaker, cfg, text):
    activity.log_event("say", text)
    speaker.say(text, cfg, interruptible=False)


def profile_samples(name=None):
    return voiceid.stats(name)["samples"]


def state(progress):
    voices = voiceid.all_stats()
    return {
        "active": bool(progress["active"]),
        "stage": progress["stage"],
        "enrolled": progress["enrolled"],
        "last": progress["last"],
        "left": progress.get("left"),
        "speech": round(float(progress.get("speech", 0.0)), 1),
        "speech_target": progress.get("speech_target", SECONDS),
        "name": (progress.get("name") if progress["active"] else "") or voiceid.active_name(),
        "profiles": voiceid.profiles(),
        "profile": {"exists": voiceid.profile_exists(), "samples": profile_samples()},
        "voices": voices,
        "totals": {
            "voices": len(voices),
            "recordings": sum(v["recordings"] for v in voices.values()),
            "sessions": sum(v["sessions"] for v in voices.values()),
        },
    }


def start(cfg, speaker, capture, progress, name=None, mode=None, was_deaf=False):
    if progress["active"]:
        return False
    voiceid.migrate()
    progress.update({
        "active": True, "stage": "starting", "enrolled": 0, "last": "",
        "name": name or voiceid.active_name(), "left": None,
        "speech": 0.0, "speech_target": SECONDS,
    })
    target = run_agent_rounds if mode == "agent" else run_rounds
    threading.Thread(
        target=_run_guarded, args=(cfg, speaker, capture, progress, target, was_deaf), daemon=True
    ).start()
    return True


def _run_guarded(cfg, speaker, capture, progress, target, was_deaf=False):
    try:
        target(cfg, speaker, capture, progress)
    except Exception:
        log.exception("training session failed")
        progress.update({"active": False, "stage": "failed"})
    finally:
        progress["active"] = False
        if was_deaf:
            vstate.set_deaf(True)
        else:
            vstate.write_state("listening")


def delete_profile(confirm, progress, name=None):
    voiceid.migrate()
    target = name or voiceid.active_name()
    if not confirm:
        return {
            "ok": False,
            "needs_confirm": True,
            "active": bool(progress["active"]),
            "target": target,
            "profile": {"exists": voiceid.profile_exists(target), "samples": profile_samples(target)},
        }
    if progress["active"]:
        return {"ok": False, "error": "training in progress", "active": True}
    result = voiceid.delete(target)
    if result.get("deleted"):
        log.info("voice profile %s deleted on his lord's confirmed order", target)
        activity.log_event("tool", f"voice model {target} deleted")
    return result
