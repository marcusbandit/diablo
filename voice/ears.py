"""Diablo's ears: say his name, hear a chime, talk, hear a second chime, he answers.

One wake gives one turn. He sleeps on the wake word model (voice/wake.py), and when it
fires and the voice is Marcus's, he plays the listening chime and records until Marcus
has been quiet long enough (voice/endturn.py). The done chime says he stopped listening. The words
go to the hub's POST /v1/chat, the same brain and the same conversation as the desktop
app, and the reply is spoken. Then he sleeps until the next "Diablo".

    python -m voice.ears            the service (diablo-ears.service runs this at login)

The setup and the live meter are on the Voice page of the desktop app, served from here
by voice/ears_api.py on 127.0.0.1:8758. State for faces: ~/.local/state/diablo/ears.json,
one of asleep, waking, listening, thinking, speaking, untrained, training, off.
"""

import atexit
import json
import logging
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx
import numpy as np

from . import chime, commands, ears_api, endturn, feedback, hubclient, jevfast, mouth, panel, presence, quick, recordings, speakers, stt, surroundings, timeline, voiceid, wake
from . import state as vstate
from .config import load_config

log = logging.getLogger("diablo.ears")

STATE_DIR = Path.home() / ".local/state/diablo"
STATE_FILE = STATE_DIR / "ears.json"
OFF_FILE = STATE_DIR / "ears.off"
NOTIFIED = STATE_DIR / "ears-untrained-notified"
TMP = Path("/tmp/opencode")

NO_SPEECH_S = 6.0
# The voice loop: each listen waits this long for talk before it starts over, a line is cut
# at LOOP_TURN_MAX_S so music that slips past the voice check cannot hold the mic, and the
# loop ends by itself after ears.loop_idle_s with nothing said.
LOOP_LISTEN_S = 30.0
LOOP_TURN_MAX_S = 90
LOOP_IDLE_S = 300.0
LOOP, STOP = "loop", "stop"
# An answer not back within this long is left thinking in the background, and the ears go
# back to listening for his name: he can ask the next thing while Diablo still works.
INLINE_WAIT_S = 1.5
# Speech control waits up to LOOP_LISTEN_S for his next line. An answer that lands meanwhile
# used to wait for that line and then come out as if it answered it: the centering replies
# kept playing after each new order (2026-11-13). The wait now ends once he has been quiet
# this long and an answer is ready.
INTERRUPT_QUIET_S = 0.5
# A piece this likely to be speech already holds a ready answer back: the first syllable of his
# next line often scores under SPEECH_PROB.
ONSET_PROB = 0.3
# A streamed answer can be quiet between its first line and its end for as long as a hub turn
# may run (hub/brains.py TURN_SECONDS), plus a margin.
TURN_READ_S = 960
# Jev ends the turn: after EARLY_S of quiet on a short line, the words so far go to Jev on a
# thread while the ears keep listening. A plain command Jev is EARLY_SURE of ends the turn
# there and then, instead of after the full quiet; talk that resumes throws the read away.
EARLY_S = 0.35
# How far past the longest turn the wall clock may run when the mic stops sending audio.
STALL_GRACE_S = 5.0
EARLY_TALK_S = 3.0
EARLY_SURE = 0.9
# Dictation: each pause of ears.dictation_pause_s closes a piece, a piece is cut at 60 s, and
# dictation gives up after DICTATION_IDLE_S with nothing said.
DICTATION_PAUSE_S = 1.2
DICTATION_PIECE_MAX_S = 60
DICTATION_IDLE_S = 60.0
SPEECH_PROB = 0.5
# Spoken replies stop at a sentence end near this length; the full reply is in the chat.
SPOKEN_CHARS = 700


LIVE = None
REPORT = None
_last_state = None


class HubReport:
    """Tells the hub the ears' state on every change and every few seconds, so the hub can tell when it is safe to restart.
    A state the hub has not heard of lately counts as unknown there, which blocks a restart."""

    EVERY_S = 15.0

    def __init__(self, cfg):
        self.cfg = cfg
        self.changed = threading.Event()
        self.stopped = False
        self.thread = threading.Thread(target=self._run, name="hub-report", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped:
            self.changed.wait(self.EVERY_S)
            self.changed.clear()
            if not self.stopped:
                hubclient.report_ears(self.cfg, _last_state or "asleep")

    def poke(self):
        self.changed.set()

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.changed.set()
        self.thread.join(timeout=6)
        hubclient.report_ears(self.cfg, "off")


# Modes that outlast one state: speech control, dictation, answers thinking in the background.
# Every write carries them, so the on-screen orb (presence/) never loses the ring mid-reply.
STICKY = {"loop": False, "dictation": False, "thinking": 0, "moment": None}


_state_lock = threading.Lock()


def set_state(state, **extra):
    """Writes the state file when it changes and tells the control panel. Background answers
    and the early read call this from their own threads, so one writer at a time."""
    with _state_lock:
        _set_state(state, **extra)


def _set_state(state, **extra):
    global _last_state
    if LIVE is not None:
        LIVE.set_state(state)
    changed = False
    for key in STICKY:
        if key in extra:
            value = extra.pop(key)
            changed = changed or STICKY[key] != value
            STICKY[key] = value
    if state == _last_state and not extra and not changed:
        return
    _last_state = state
    if REPORT is not None:
        REPORT.poke()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"state": state, "at": time.time(), **STICKY, **extra}))
    tmp.replace(STATE_FILE)


def thinking_changed():
    set_state(_last_state or "asleep", thinking=PENDING.thinking())


def show_moment(mood):
    """A short feeling on the on-screen face: delighted, sorry, relieved."""
    set_state(_last_state or "asleep", moment={"mood": mood, "at": time.time()})


feedback.on_moment = show_moment


def set_holding(on):
    """The quiet cue: the ears heard him pause and are still holding the turn open."""
    if LIVE is not None:
        LIVE.set_holding(on)
    set_state("listening", holding=bool(on))


def listening():
    return not OFF_FILE.exists()


def set_listening(on):
    if on:
        OFF_FILE.unlink(missing_ok=True)
    else:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        OFF_FILE.touch()


def pick_device(wanted):
    import sounddevice as sd

    if wanted:
        for index, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0 and wanted.lower() in dev["name"].lower():
                return index
    return None


class Mic:
    """80 ms blocks of 16 kHz mono into a queue, each stamped with when it arrived.

    The sound card hands over 20 ms at a time, so the meter's level is never more than
    20 ms old; four of those make one 80 ms block for the wake word model.
    """

    STEP = wake.CHUNK // 4

    def __init__(self, cfg):
        import sounddevice as sd

        self.queue = queue.Queue(maxsize=400)
        self.level = 0.0
        self.overflows = 0
        self._parts = []
        # Noise removal before anything listens; off only when the config says so.
        self.denoiser = None
        if str(cfg.get("ears_denoise", "1")).lower() not in ("0", "false", "no", "off"):
            try:
                from .denoise import Denoiser

                self.denoiser = Denoiser(wake.RATE)
                from .denoise import mark_start

                mark_start()
            except Exception as error:
                log.warning("noise filter unavailable, hearing the raw mic: %s", error)
        self.stream = sd.InputStream(
            samplerate=wake.RATE, channels=1, dtype="float32", blocksize=self.STEP,
            latency="low", device=pick_device(cfg.get("mic_device", "")), callback=self._callback,
        )

    def _callback(self, data, frames, when, status):
        if status and status.input_overflow:
            self.overflows += 1
        piece = data[:, 0].copy()
        if self.denoiser is not None:
            piece = self.denoiser.feed(piece)
            if len(piece) == 0:
                return
        # Loudest 20 ms since the meter last read it, so no syllable slips between polls.
        self.level = max(self.level, float(np.sqrt(np.mean(piece ** 2))))
        self._parts.append(piece)
        if sum(len(p) for p in self._parts) < wake.CHUNK:
            return
        block = np.concatenate(self._parts)
        self._parts = [block[wake.CHUNK:]] if len(block) > wake.CHUNK else []
        try:
            self.queue.put_nowait((time.monotonic(), block[: wake.CHUNK]))
        except queue.Full:
            pass

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()

    def read(self, timeout=1.0):
        return self.queue.get(timeout=timeout)

    def take_level(self):
        """The loudest 20 ms since the last call, then start over."""
        level, self.level = self.level, 0.0
        return level

    def backlog(self):
        return self.queue.qsize()

    def drain(self):
        # The part block in the callback goes too, so nothing from before the drain leaks in.
        self._parts = []
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return


class DictationWatch:
    """Knows whether Marcus is dictating, or was in the last couple of seconds.

    The dictation daemon (gvoice) shares the mic. Its name in a dictated sentence must
    never wake him, including the name said just as dictation stops, so a wake counts
    only when dictation has been idle for QUIET_S.
    """

    QUIET_S = 2.0

    def __init__(self, cfg, poll=0.25):
        import threading

        self.cfg, self.poll = cfg, poll
        self.last_busy = 0.0
        threading.Thread(target=self._watch, name="dictation-watch", daemon=True).start()

    def _watch(self):
        while True:
            try:
                status = stt.dictation_status(self.cfg)
                if status.get("listening") or status.get("phase") not in (None, "idle"):
                    self.last_busy = time.monotonic()
            except FileNotFoundError:
                log.info("no dictation daemon set up; not watching for it")
                return
            except Exception:
                pass
            time.sleep(self.poll)

    def busy(self):
        return time.monotonic() - self.last_busy < self.QUIET_S


class Ambient:
    """Keeps short clips of everyday sound from this mic, as things that are not his name.

    A model trained on takes of the name from this mic and negatives from everywhere else
    learns "this mic" instead of "Diablo": rubbing the mic or a near word woke it. So while
    he is asleep, about every EVERY_S a 2 s window is kept when something happened in it
    (speech, typing, a chair, the mic being touched) and nothing in it scored like the name.
    They stay in samples/ambient on this machine, the newest KEEP of them.
    """

    EVERY_S = 20.0
    KEEP = 4000
    # Only clips the model was sure about, and never one that looks like him saying a
    # single name-length word: a far "Diablo" it missed must not become a negative.
    QUIET_SCORE = 0.05

    def __init__(self):
        self.next_at = time.monotonic() + self.EVERY_S
        self.scores = []

    def feed(self, history, score):
        self.scores = (self.scores + [score])[-25:]
        if time.monotonic() < self.next_at or len(history) < 25:
            return None
        if max(self.scores) >= self.QUIET_SCORE:
            return None
        clip = np.concatenate(history[-25:])
        db = wake.envelope_db(clip)
        if not wake.segments(db, float(np.percentile(db, 20))):
            return None
        if self.maybe_his_call(clip):
            self.next_at = time.monotonic() + self.EVERY_S
            return None
        self.next_at = time.monotonic() + self.EVERY_S
        folder = wake.SAMPLES / "ambient"
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}.wav"
        try:
            wake.write_wav(path, clip)
            old = sorted(folder.glob("*.wav"))
            for stale in old[: max(len(old) - self.KEEP, 0)]:
                stale.unlink(missing_ok=True)
        except OSError as error:
            log.warning("ambient clip not kept: %s", error)
            return None
        return path


def _ambient_maybe_his_call(clip):
    verdict = wake.judge(clip)
    if not verdict["ok"] or not 0.3 <= verdict["seconds"] <= 1.3:
        return False
    minimum = wake.meta().get("speaker_min")
    score = wake.speaker_score(verdict["word"]) if minimum is not None else None
    return score is None or score >= minimum - 0.1


Ambient.maybe_his_call = staticmethod(_ambient_maybe_his_call)


class _Quiet:
    """What tts.Speaker expects of a capture: here the turn already ignores the mic."""

    def __init__(self):
        import threading

        self.interrupted = threading.Event()

    def begin_playback(self):
        pass

    def end_playback(self):
        pass


class Heard(tuple):
    """(audio, spoke), plus the words when the end-of-turn check already transcribed them, and
    .early, Jev's answer when Jev ended the turn on a plain command."""

    def __new__(cls, audio, spoke, text=None, early=None):
        heard = super().__new__(cls, (audio, spoke))
        heard.text = text
        heard.early = early
        return heard


def early_sure(found):
    """A Jev answer good enough to end the turn on: an act, Jev sure of the command, and not
    one that sends or closes something: "close Firefox... once the download is done" must not
    close it at the first pause."""
    act = (found or {}).get("act")
    return bool(isinstance(act, dict) and act.get("action") not in commands.SURE_ONLY
                and float(found.get("confidence") or 0) >= EARLY_SURE)


def record(mic, cfg, after, vad, start_audio=None, probe=None, hold=None, no_speech_s=NO_SPEECH_S, tl=None,
           early=None, interrupt=None):
    """Audio from `after` (a monotonic time) until he has finished.

    A quiet of ears.silence_s ends the turn when the words so far read as finished, and
    ears.silence_open_s when they trail off (voice/endturn.py). probe(audio) returns the
    words so far; without it, or with ears.adaptive off, the plain base quiet ends the
    turn. hold(True) says the turn is being held open, hold(False) that it no longer is.
    start_audio, when given, goes in front, as if it had been read from the mic.
    Returns Heard: (audio, spoke), where spoke is False when nothing was said within
    no_speech_s, and .text is the words when the check already has them. tl, a Timeline,
    gets speech_start, speech_end, turn_end and the time spent in end-of-turn checks.
    early(text), when given with probe, is Jev on the words so far: it runs on a thread at
    EARLY_S of quiet on a short line, and an answer early_sure() of ends the turn at once.
    interrupt(), when given, ends the wait with nothing heard while he is quiet: speech control
    passes "an answer is ready", so a finished answer is said now, not after his next line.
    """
    policy = endturn.Policy.from_cfg(cfg)
    vad.reset()
    pieces, pending = [], np.zeros(0, dtype=np.float32)
    if start_audio is not None and len(start_audio):
        # Already heard before `after`: it counts like any other audio of the turn.
        pieces.append(np.asarray(start_audio, dtype=np.float32))
        pending = pieces[0].copy()
    spoke, quiet_s, heard_s, began = False, 0.0, 0.0, time.monotonic()
    # The cap on a turn counts from his first word: in speech control the ears may wait long
    # before he talks, and a cap from the start cut "Create a thread in T3 Code that makes it
    # fa-" 2.8 s into the sentence (2026-11-11).
    talk_began, talk_began_at = None, None
    text, checked, held, samples = None, False, False, len(pending)
    backlog = getattr(mic, "backlog", lambda: 0)
    last_speech = None
    sound_at = began
    job, found = None, None
    while True:
        elapsed = time.monotonic() - began
        if (interrupt is not None and not spoke and time.monotonic() - sound_at >= INTERRUPT_QUIET_S
                and interrupt()):
            break
        # Audio time, not the wall clock, so a replay in tests caps the same way.
        talked = (samples - talk_began) / wake.RATE if talk_began is not None else 0.0
        # A stalled mic sends no blocks, so audio time stops; the wall clock still ends it.
        stalled = talk_began_at is not None and time.monotonic() - talk_began_at >= policy.max_s + STALL_GRACE_S
        if talked >= policy.max_s or stalled or (not spoke and elapsed >= no_speech_s):
            break
        try:
            stamp, block = mic.read(timeout=1.0)
        except queue.Empty:
            continue
        if stamp < after:
            continue
        pieces.append(block)
        samples += len(block)
        pending = np.concatenate([pending, block])
        while len(pending) >= 512:
            prob = vad.prob(pending[:512])
            pending = pending[512:]
            step = 512 / wake.RATE
            if prob >= ONSET_PROB:
                sound_at = time.monotonic()
            if prob >= SPEECH_PROB:
                heard_s += step
                quiet_s = 0.0
                last_speech = stamp + len(block) / wake.RATE
                # He went on talking: the words checked so far are stale, and so is the hold.
                text, checked = None, False
                if job is not None:
                    job["stale"] = True
                    job = None
                if held:
                    held = False
                    if hold is not None:
                        hold(False)
                if heard_s >= 0.2:
                    if not spoke:
                        talk_began = max(samples - int(heard_s * wake.RATE), 0)
                        talk_began_at = time.monotonic()
                        if tl is not None:
                            tl.mark("speech_start", at=max(stamp - heard_s, after))
                    spoke = True
            else:
                quiet_s += step
        if (early is not None and probe is not None and spoke and job is None and not checked
                and quiet_s >= EARLY_S and heard_s <= EARLY_TALK_S):
            job = _start_early(np.concatenate(pieces), probe, early)
        if job is not None and job["done"].is_set() and not job["stale"]:
            if job["text"] is not None and not checked:
                # The read doubles as the end-of-turn check: no second speech to text.
                text, checked = job["text"], True
                if tl is not None:
                    tl.add_ms("probe_ms", job["ms"])
            if early_sure(job["found"]):
                found = job["found"]
                if tl is not None:
                    tl.mark("early_end")
                    tl.note(early_ms=job["ms"])
                break
        if not spoke or quiet_s < endturn.first_check_s(policy, heard_s) or backlog() > 2:
            continue
        if job is not None and not job["done"].is_set():
            # Its words are on the way; a second speech to text would only race it.
            continue
        if policy.adaptive and probe is not None and not checked:
            checked = True
            probed = time.monotonic()
            try:
                text = probe(np.concatenate(pieces))
            except Exception as error:
                log.warning("end of turn check failed, the base quiet decides: %s", error)
                text = None
            if tl is not None:
                tl.add_ms("probe_ms", (time.monotonic() - probed) * 1000)
            continue
        if endturn.decide(quiet_s, text, policy) == "end":
            break
        if not held and text is not None and hold is not None:
            held = True
            hold(True)
    if held and hold is not None:
        hold(False)
    if tl is not None:
        if spoke and last_speech is not None:
            tl.mark("speech_end", at=last_speech)
        tl.mark("turn_end")
    audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    return Heard(audio, spoke, text if spoke else None, found)


def _start_early(clip, probe, early):
    """The words so far and Jev's read of them, on a thread."""
    job = {"done": threading.Event(), "stale": False, "text": None, "found": None, "ms": 0}

    def run():
        started = time.monotonic()
        try:
            job["text"] = probe(clip)
            if job["text"] and not stt.is_noise(job["text"]) and not job["stale"]:
                job["found"] = early(job["text"])
        except Exception as error:
            log.debug("early read failed: %s", error)
        finally:
            job["ms"] = round((time.monotonic() - started) * 1000)
            job["done"].set()

    threading.Thread(target=run, daemon=True, name="early").start()
    return job


def transcribe(audio, cfg):
    TMP.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", dir=TMP, delete=False) as handle:
        path = handle.name
    try:
        stt.write_wav(audio, wake.RATE, path)
        return stt.transcribe_file(path, cfg)
    finally:
        Path(path).unlink(missing_ok=True)


def ask(cfg, text, context=None, shot=None, desktop=None, on_say=None):
    """The hub's answer to the words, as it sent it: reply, say, backend and maybe act.

    With on_say the answer streams: his first line comes to on_say the moment the hub has it,
    before the work it announces is done. A hub that does not stream answers in one piece.
    What was on screen at the wake goes along. A picture goes only when the words point
    at the screen, so a plain question stays fast. desktop (the open windows and media
    players) lets Jev pick a plain desktop command, which comes back as "act"."""
    import base64

    body = {"text": text, "section": "", "source": "voice", "client_id": f"ears-{uuid.uuid4()}",
            "host": socket.gethostname()}
    recent = commands.recent_lines()
    if recent:
        # What he just had Diablo do by voice: "down to 40" after three quieters is the volume.
        context = list(context or []) + ["Just done by voice, oldest first: " + "; ".join(recent)]
    if context:
        body["context"] = list(context)
    if shot and surroundings.points_at_screen(text):
        body["images"] = [{"data": base64.b64encode(shot).decode(), "media_type": "image/jpeg"}]
    if desktop:
        body["desktop"] = with_recent(desktop)
    if on_say is not None:
        body["stream"] = True
    with httpx.stream(
        "POST",
        f"{cfg['hub_url'].rstrip('/')}/v1/chat",
        headers={"X-Diablo-Token": cfg.get("hub_token", "")},
        json=body,
        timeout=httpx.Timeout(600, read=TURN_READ_S),
    ) as response:
        response.raise_for_status()
        if "ndjson" not in response.headers.get("content-type", ""):
            response.read()
            found = response.json()
            return found if isinstance(found, dict) else {}
        return read_lines(response.iter_lines(), on_say)


def read_lines(lines, on_say):
    """The streamed answer: each {"say"} line goes to on_say, {"done"} is the answer."""
    for line in lines:
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            continue
        if "say" in item:
            if on_say is not None and str(item["say"] or "").strip():
                on_say(str(item["say"]))
        elif "done" in item:
            found = item["done"]
            return found if isinstance(found, dict) else {}
        elif "error" in item:
            raise ValueError(f"hub chat failed: {item['error']}")
    raise ValueError("the hub's answer ended early")


class Pending:
    """Answers still thinking on the hub while the ears listen again. Each job runs its hub
    call on a thread; finished ones are spoken by the main loop between his turns."""

    def __init__(self):
        self.jobs = []
        self.lock = threading.Lock()

    def start(self, work, tl, said):
        """work(say) runs the hub call; each line it hands to say is spoken as soon as the
        main loop is free, ahead of the answer and in the order they came."""
        job = {"done": threading.Event(), "words": "", "tl": tl, "said": said, "dropped": False, "early": []}

        def say(words):
            with self.lock:
                if not job["dropped"]:
                    job["early"].append(words)

        def run():
            try:
                job["words"] = work(say)
            except Exception:
                log.exception("background answer failed")
                job["words"] = "That one failed on my side."
            finally:
                job["done"].set()

        with self.lock:
            self.jobs.append(job)
        threading.Thread(target=run, daemon=True, name="answer").start()
        return job

    def show(self, job):
        feedback.thinking()
        thinking_changed()

    def forget(self, job):
        with self.lock:
            if job in self.jobs:
                self.jobs.remove(job)

    def take_early(self, job):
        with self.lock:
            early, job["early"] = job["early"], []
        return early

    def keep(self, unsaid):
        """Puts back what say_ready took but never said, ahead of anything newer: the first
        lines left and the finished answers, so a cut off line never loses the answer."""
        with self.lock:
            for job, early, finished in reversed(unsaid):
                if job["dropped"]:
                    continue
                job["early"] = list(early) + job["early"]
                if job not in self.jobs:
                    self.jobs.insert(0, job)

    def has_speech(self):
        with self.lock:
            self.jobs = [job for job in self.jobs if not (job["dropped"] and job["done"].is_set())]
            return any(not job["dropped"] and (job["early"] or job["done"].is_set()) for job in self.jobs)

    def speakable(self):
        """[(job, first lines, finished)], oldest job first: the lines each job has to say now,
        and whether its answer is in. Finished jobs come off the list; dropped ones say nothing."""
        out = []
        with self.lock:
            for job in list(self.jobs):
                finished = job["done"].is_set()
                early, job["early"] = job["early"], []
                if finished:
                    self.jobs.remove(job)
                if not job["dropped"] and (early or finished):
                    out.append((job, early, finished))
        return out

    def thinking(self):
        with self.lock:
            return len(self.jobs)

    def drop_all(self):
        with self.lock:
            for job in self.jobs:
                job["dropped"] = True
                job["early"] = []
            return len(self.jobs)


PENDING = Pending()

# "Never mind", "cancel that", "stop thinking": drops what Diablo is still thinking on.
CANCEL = re.compile(r"^\W*(?:diablo\W+)?(?:never\s*mind|cancel(?:\s+(?:that|it|this))?|forget\s+(?:it|that)|stop\s+thinking)\W*$", re.I)


def answer_or_background(work, tl, said):
    """Runs work(say) (the hub call and any act) and returns its words when it is quick. When it
    is not back within INLINE_WAIT_S, returns None and leaves it thinking: the main loop says
    his first line as soon as it comes, then the answer once it lands and he is not mid-turn."""
    job = PENDING.start(work, tl, said)
    if job["done"].wait(INLINE_WAIT_S):
        PENDING.forget(job)
        return " ".join(w for w in PENDING.take_early(job) + [job["words"]] if w).strip()
    tl.note(background=True)
    tl.mark("backgrounded")
    PENDING.show(job)
    log.info("still thinking on %r, listening again", said[:80])
    return None


def say_ready(mic, speaker, cfg, detector, tap):
    """Speaks the first lines of answers still thinking and every answer that finished in the
    background, each job's lines in order. True when he cut one off with the name, so a turn
    follows; whatever was not said yet waits for after that turn."""
    ready = PENDING.speakable()
    if any(finished for _, _, finished in ready):
        thinking_changed()
    for at, (job, early, finished) in enumerate(ready):
        for n, words in enumerate(early):
            if say_answer(mic, speaker, words, cfg, detector, tap, job["tl"], first=True):
                PENDING.keep([(job, early[n + 1:], finished)] + ready[at + 1:])
                return True
        if finished:
            cut = say_answer(mic, speaker, job["words"], cfg, detector, tap, job["tl"])
            job["tl"].mark("end")
            job["tl"].save()
            if cut:
                PENDING.keep(ready[at + 1:])
                return True
    return False


def cancel_thinking(cfg):
    """Stops the hub's running turns for the main conversation and drops their answers."""
    dropped = PENDING.drop_all()
    try:
        httpx.post(f"{cfg['hub_url'].rstrip('/')}/v1/turns/stop", headers={"X-Diablo-Token": cfg.get("hub_token", "")},
                   json={"section": ""}, timeout=5)
    except httpx.HTTPError as error:
        log.warning("could not stop the hub's turn: %s", error)
    return dropped


def control(cfg, text, desktop):
    """Speech control: Jev alone reads the line. {"act", "jev_ms", ...}; act None means it is
    no plain command. Straight from banditbox when it can (voice/jevfast.py), through the hub
    for a thread line, which needs the hub's thread list. Nothing goes into the Diablo chat."""
    try:
        direct = jevfast.control(text, desktop)
    except Exception as error:
        log.warning("direct Jev failed, asking the hub: %s", error)
        direct = None
    if direct is not None:
        return direct
    response = httpx.post(
        f"{cfg['hub_url'].rstrip('/')}/v1/voice/control",
        headers={"X-Diablo-Token": cfg.get("hub_token", "")},
        json={"text": text, "desktop": desktop, "host": socket.gethostname()},
        timeout=10,
    )
    response.raise_for_status()
    found = response.json()
    return found if isinstance(found, dict) else {}


def with_recent(desktop):
    """The desktop plus what Diablo just did by voice, which is what "it", "more" and
    "down to 40" are about."""
    return {**(desktop or {}), "recent": commands.recent_lines()}


def early_read(cfg, words, desktop):
    """Jev on the words so far, for record()'s early end. None on any failure."""
    try:
        return control(cfg, words, with_recent(desktop))
    except Exception as error:
        log.debug("early Jev read failed: %s", error)
        return None


def note_control(tl, found):
    tl.note(hub={"jev_ms": found.get("jev_ms"), "brain_ms": found.get("hub_ms"),
                 "backend": f"control {found.get('path') or 'hub'}",
                 "model": f"jev {found.get('choice')} {found.get('confidence')}"})


def locked():
    """Whether this box's screen is locked, by the hub's own probe run here, locally.
    A voice in the room must not drive a locked desktop; unknown counts as locked, as the hub does."""
    try:
        from hub import lockguard

        return lockguard.enabled() and lockguard.decide("voice", lockguard.screen(target=socket.gethostname()))
    except Exception:
        return False


def run_act(act, tl, cfg):
    """Runs a desktop act from the hub. The words to say: empty when it worked."""
    if locked():
        tl.note(outcome="refused, the screen is locked")
        feedback.failed()
        return "The screen is locked, so I left it alone."
    started = time.monotonic()
    ok, why = commands.run(act)
    tl.mark("act_done")
    tl.note(act=act.get("action"), act_ms=round((time.monotonic() - started) * 1000),
            outcome=f"did: {act.get('did', act.get('action'))}" if ok else f"act failed: {why}")
    log.info("act %s %s in %.0f ms%s", act.get("action"), "done" if ok else "failed",
             (time.monotonic() - started) * 1000, "" if ok else f": {why}")
    if ok:
        if act.get("action") in ("undo", "go_back", "reopen_window"):
            feedback.back()
        else:
            feedback.done()
        return ""
    feedback.failed(why)
    return f"That didn't work: {why}."


def local_line(mic, settings, vad, text, tl, speaker=None):
    """Lines the ears handle on their own: "never mind" while he thinks, "type ...", and
    "dictate". The words to say, or None when the line is not one of them."""
    if PENDING.thinking() and CANCEL.match(text):
        dropped = cancel_thinking(settings.get())
        thinking_changed()
        tl.note(outcome=f"cancelled {dropped} still thinking")
        feedback.back()
        return ""
    typed = commands.typed_words(text)
    if typed is not None:
        started = time.monotonic()
        ok, why = commands.type_text(typed)
        tl.mark("act_done")
        tl.note(act="type", act_ms=round((time.monotonic() - started) * 1000),
                outcome=f"typed {len(typed)} characters" if ok else f"typing failed: {why}")
        if ok:
            commands.remember("type", f"typed {typed[:40]}")
            feedback.done(f"Typed: {typed[:60]}")
            return ""
        feedback.failed(why)
        return f"That didn't work: {why}."
    if commands.wants_dictation(text):
        return dictate(mic, settings, vad, tl, speaker)
    if commands.wants_animation(text):
        return animate(mic, settings, vad, tl, speaker)
    panel_kind = panel.intent(text)
    if panel_kind:
        words = panel.apply(panel_kind)
        tl.mark("act_done")
        tl.note(act=f"panel {panel_kind}", outcome=words[:80])
        feedback.done()
        return words
    quick_answer = quick.answer(text, settings.get())
    if quick_answer is not None:
        words, card = quick_answer
        tl.mark("act_done")
        tl.note(act=f"quick {card.get('kind') if card else 'none'}", outcome=words[:80])
        if card:
            presence.show(card.get("title"), card.get("big"), card.get("lines") or [], kind=card.get("kind", "quick"))
        return words
    level = commands.volume_target(text)
    if level is not None:
        ok, why = commands.set_volume(level)
        tl.mark("act_done")
        tl.note(act="volume_set", outcome=why)
        if ok:
            feedback.done()
            return ""
        feedback.failed()
        return f"That didn't work: {why}."
    combo = commands.pressed_combo(text)
    if combo is not None:
        out = commands.desktop.press_key(combo)
        tl.mark("act_done")
        tl.note(act="press", outcome=f"pressed {combo}" if out == "ok" else f"press failed: {out}")
        if out == "ok":
            commands.remember("press", f"pressed {combo}")
            feedback.done(f"Pressed {combo}")
            return ""
        feedback.failed(out)
        return f"That didn't work: {out}."
    tab = commands.tab_name(text)
    if tab is not None:
        ok, why = commands.browser_tab(tab)
        tl.mark("act_done")
        tl.note(act="browser_tab", outcome=why)
        if ok:
            feedback.done(why)
            return ""
        feedback.failed(why)
        return f"That didn't work: {why}."
    return None


ANIMATE_MAX_S = 300.0


def _cut_before(audio, text, keep_words):
    """The piece's audio up to where word number `keep_words` of `text` starts, found by the
    aligner; by the share of the words when the aligner cannot read it."""
    from . import mouth

    with tempfile.TemporaryDirectory(prefix="animate-") as tmp:
        clip = Path(tmp) / "piece.wav"
        stt.write_wav(audio, wake.RATE, clip)
        measured = mouth._aligner.measure(text, clip.read_bytes())
    at = None
    if measured:
        at = next((s for s in measured[1][keep_words:] if s is not None), None)
    if at is None:
        at = len(audio) / wake.RATE * keep_words / max(1, len(text.split()))
    return audio[: max(0, int((at - 0.08) * wake.RATE))]


TAKE_GAP_S = 0.25


def _render_take(pieces):
    """diablo-act on a take, then a card that plays it beside the cursor. Runs on a thread.

    The take goes through the same pipeline as diablo-act: Whisper finds the language of
    each stretch (English, Danish, Spanish or Japanese), each stretch is aligned on its own
    in that language, and the cues are shifted into place."""
    from . import act

    try:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="animate-") as tmp:
            tmp = Path(tmp)
            gap = np.zeros(int(TAKE_GAP_S * wake.RATE), dtype=np.float32)
            joined = np.concatenate([part for audio, _ in pieces for part in (audio, gap)][:-1])
            stt.write_wav(joined, wake.RATE, tmp / "take.wav")
            wav = tmp / "take-24k.wav"
            act.to_wav(tmp / "take.wav", wav)
            try:
                segments = act.transcribe(wav, "auto")
            except RuntimeError as error:
                # no Whisper: the words the ears heard, as English
                log.warning("animate: %s, using the ears' words", error)
                segments = act.transcribe(wav, "en", " ".join(words for _, words in pieces))
            face, looks, text = act.measure(wav, segments)
            out = act.make(wav, text, act.out_path(), face, looks, size=720)
        log.info("animate: %s in %.1f s", out, time.monotonic() - started)
        presence.show("Your take", "", [text[:160]], kind="video", ttl=180, video=str(out))
        feedback.done()
    except Exception:
        log.exception("animate: rendering the take failed")
        feedback.failed()


def animate(mic, settings, vad, tl, speaker=None):
    """"Animate me": a beep, Marcus talks until "I'm done", a beep, he hears the last word,
    and the take comes back as Diablo acting it out, in a card beside the cursor."""
    cfg = dict(settings.get())
    if speaker is not None:
        speaker.say("Sir, wait for the beep.", cfg, interruptible=False)
    cfg.update({"ears.adaptive": "0", "ears.turn_max_s": DICTATION_PIECE_MAX_S,
                "ears.silence_s": cfg.get("ears.dictation_pause_s") or DICTATION_PAUSE_S})
    log.info("animate: recording")
    set_state("listening", holding=False, dictation=True)
    feedback.dictation(True)
    tl.mark("animate_start")
    pieces, ended = [], False
    began = after = last = time.monotonic()
    while (listening() and time.monotonic() - last < DICTATION_IDLE_S
           and time.monotonic() - began < ANIMATE_MAX_S):
        audio, spoke = record(mic, cfg, after, vad, no_speech_s=10)
        after = time.monotonic()
        if not spoke:
            continue
        last = time.monotonic()
        ok, voice = talk_ok(audio, cfg)
        if not ok:
            log.info("animate: piece refused, not his voice (%.3f)", voice)
            continue
        try:
            text = transcribe(audio, cfg)
        except Exception as error:
            log.warning("animate: speech to text failed: %s", error)
            continue
        if stt.is_noise(text):
            continue
        ended, words = commands.animation_end(text)
        if ended:
            if words:
                pieces.append((_cut_before(audio, text, len(words.split())), words))
            break
        pieces.append((audio, text))
        feedback.piece()
    tl.mark("animate_end")
    feedback.dictation(False)
    set_state("thinking", dictation=False)
    text = " ".join(words for _, words in pieces).strip()
    tl.note(animate_pieces=len(pieces), animate_words=len(text.split()), animate_ended=ended)
    if not text:
        tl.note(outcome="animate: nothing recorded")
        feedback.failed()
        return "I didn't catch anything, sir."
    threading.Thread(target=_render_take, args=(pieces,), daemon=True, name="animate").start()
    tl.mark("act_done")
    tl.note(act="animate", outcome=f"take of {len(text.split())} words, rendering")
    last_word = text.split()[-1].strip(".,!?;:\"'")
    return f"Got it. Your last word was {last_word}."


def dictate(mic, settings, vad, tl, speaker=None):
    """Talk until "dictation done", then the whole of it is typed where the focus was.

    Each pause closes a piece, which is checked against his voice and turned to text at
    once, so the end phrase is seen as soon as it is said. Nothing goes to the hub."""
    cfg = dict(settings.get())
    cfg.update({"ears.adaptive": "0", "ears.turn_max_s": DICTATION_PIECE_MAX_S,
                "ears.silence_s": cfg.get("ears.dictation_pause_s") or DICTATION_PAUSE_S})
    windows = commands.desktop_state()["windows"]
    focused = next((w for w in windows if w["focused"]), None)
    focus = focused["address"] if focused else None
    into = (focused or {}).get("class") or "the window you are in"
    log.info("dictation on")
    set_state("listening", holding=False, dictation=True)
    feedback.dictation(True)
    tl.mark("dictation_start")
    pieces, ended = [], False
    after = last = time.monotonic()
    while listening() and time.monotonic() - last < DICTATION_IDLE_S:
        heard = record(mic, cfg, after, vad, no_speech_s=10)
        after = time.monotonic()
        audio, spoke = heard
        if not spoke:
            continue
        last = time.monotonic()
        ok, voice = talk_ok(audio, cfg)
        if not ok:
            log.info("dictation: piece refused, voice %.3f", voice)
            continue
        try:
            text = transcribe(audio, cfg)
        except Exception as error:
            log.warning("dictation: speech to text failed: %s", error)
            continue
        if stt.is_noise(text):
            continue
        ended, words = commands.dictation_end(text)
        if words:
            pieces.append(words)
            log.info("dictation piece: %s", words)
            if not ended:
                feedback.piece()
        if ended:
            break
    tl.mark("dictation_end")
    set_state("thinking", dictation=False)
    text = " ".join(pieces).strip()
    tl.note(dictation_pieces=len(pieces), dictation_chars=len(text), dictation_ended=ended)
    if not text:
        tl.note(outcome="dictation: nothing to type")
        feedback.failed()
        return ""
    now = next((w["address"] for w in commands.desktop_state()["windows"] if w["focused"]), None)
    if focus and now != focus:
        commands._focus(focus)
    started = time.monotonic()
    ok, why = commands.type_text(text)
    tl.mark("act_done")
    tl.note(act="dictation", act_ms=round((time.monotonic() - started) * 1000),
            outcome=f"dictated {len(text)} characters" if ok else f"typing failed: {why}")
    if ok:
        commands.remember("dictation", f"dictated {len(text.split())} words")
        feedback.dictation(False)
        return ""
    feedback.failed()
    return f"That didn't work: {why}."


def speaks_direct(cfg):
    """A direct change (a light, a window) is silent unless ears.speak_direct is on."""
    return str(cfg.get("ears.speak_direct") or "0").strip().lower() in ("1", "on", "true", "yes")


def answer_words(cfg, text, context, shot, desktop, tl=None, on_say=None):
    """The words to say for one heard line, after running any desktop act. Empty: say nothing.

    With on_say, his first line goes there as soon as the hub has it, and the words returned
    are only what comes after it."""
    tl = tl or timeline.Timeline()
    tl.mark("hub_sent")
    first = []

    def heard_first(line):
        tl.mark("ack_back")
        words = spoken(line.strip())
        if words:
            first.append(words)
            on_say(words)

    try:
        found = ask(cfg, text, context, shot, desktop, on_say=heard_first if on_say is not None else None)
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hub chat failed: %s", error)
        tl.mark("hub_back")
        tl.note(outcome="hub unreachable")
        return "I can't reach my hub right now."
    tl.mark("hub_back")
    tl.note(hub=found.get("timing"), backend=found.get("backend"))
    act = found.get("act")
    if isinstance(act, dict):
        failed = run_act(act, tl, cfg)
        if failed:
            return failed
        return found.get("reply", "") if speaks_direct(cfg) else ""
    if found.get("backend") == "direct" and not speaks_direct(cfg):
        log.info("direct: %s", str(found.get("reply") or "")[:200])
        feedback.done(str(found.get("reply") or "Done").replace(", sir.", "").rstrip("."))
        tl.note(outcome=f"direct: {str(found.get('reply') or '')[:80]}")
        return ""
    reply = str(found.get("reply") or "")
    if first and found.get("spoken_first"):
        # The first line is said already; only the rest is left, never the first line again.
        reply = str(found.get("rest") or "")
        tl.note(ack=first[0][:200])
    if reply.startswith("That failed: stopped"):
        # Cancelled on purpose ("never mind", a new request): nothing to say about it.
        tl.note(outcome="stopped")
        return ""
    tl.note(outcome=f"answered by {found.get('backend') or 'the hub'}")
    return said_aloud(reply, found.get("say"))


def said_aloud(reply, say=None):
    """The words to speak: the reply cut to a few sentences, then a question Diablo asked with its
    numbered options in full, so the options are never cut off."""
    words = spoken(str(reply or "").strip())
    say = " ".join(str(say or "").split())
    return f"{words} {say}".strip() if say else words


def spoken(reply):
    """The part of a reply worth saying aloud: no code, no links, a few sentences at most."""
    import re

    text = re.sub(r"```.*?```", " ", reply, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#>|]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= SPOKEN_CHARS:
        return text
    cut = text[:SPOKEN_CHARS]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[: end + 1] if end > 100 else cut) + " The rest is in the chat."


class Settings:
    """Config plus the hub's live settings (the voice, the TTS key), refreshed each minute."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.live = dict(cfg)
        self.at = None
        self._fetching = threading.Lock()

    def _refresh(self):
        try:
            fetched = hubclient.get_settings(self.cfg)
            if fetched:
                self.live = {**self.cfg, **fetched}
        except Exception:
            log.exception("hub settings refresh failed")
        finally:
            self.at = time.monotonic()
            self._fetching.release()

    def get(self):
        """The first call waits for the hub; later ones never do, a stale copy refreshes
        in the background, since a wake check reads these and the hub can take seconds."""
        stale = self.at is None or time.monotonic() - self.at > 60
        if stale and self._fetching.acquire(blocking=False):
            if self.at is None:
                self._refresh()
            else:
                threading.Thread(target=self._refresh, name="settings", daemon=True).start()
        return self.live


def speaker_ok(audio, cfg, slack=0.0):
    """(passed, score). Passes when the check is off or cannot run."""
    if str(cfg.get("ears_speaker_check", "1")).lower() in ("0", "false", "no", "off"):
        return True, None
    minimum = wake.meta().get("speaker_min")
    if minimum is None:
        return True, None
    score = wake.speaker_score(audio)
    if score is None:
        return True, None
    return score >= minimum - slack, score


def talk_ok(audio, cfg, slack=0.0):
    """(passed, score) for the request, against the print from his dictations."""
    if str(cfg.get("ears_speaker_check", "1")).lower() in ("0", "false", "no", "off"):
        return True, None
    minimum = wake.meta().get("talk_min")
    if minimum is None:
        return True, None
    score = wake.talk_score(audio)
    if score is None:
        return True, None
    return score >= minimum - slack, score


# Music in the mic drags his voiceprint down: wakes with Spotify on scored 0.753 and 0.738
# against a 0.754 minimum (2026-11-11). When the speakers carried sound in the 2 s before the
# name, the wake check and that turn's request check get the barge-in slack, since the clip is
# him mixed with that sound (a gvoice rule may only dim it, and a pause lands after his first
# words); the speaker tap still refuses a name that came out of the speakers. The window ends
# at the name, so Diablo's own chimes never count. MUSIC_RMS is well under music at any
# listening level (0.16 with Spotify on) and over a silent sink.
MUSIC_RMS = 0.01


def sound_slack(tap, since=None, until=None, seconds=2.0):
    """BARGE_SLACK when the speakers played sound in the window, else 0."""
    try:
        loud = tap is not None and tap.level(seconds=seconds, since=since, until=until) >= MUSIC_RMS
    except Exception:
        loud = False
    return BARGE_SLACK if loud else 0.0


def notify_untrained():
    """One quiet pointer per boot that he can learn the name, never a nag."""
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    try:
        if NOTIFIED.read_text().strip() == boot:
            return
    except OSError:
        pass
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    NOTIFIED.write_text(boot)
    subprocess.run(
        ["notify-send", "-a", "Diablo", "-i", "diablo", "Diablo can learn his name",
         "Open Diablo, then Settings, then Voice. About three minutes, and he answers when you say Diablo."],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def turn(mic, settings, speaker, vad, detector=None, tap=None, woke_at=None, lead=None, tl=None):
    """One turn. True when Marcus cut the reply off with the name, so another turn follows,
    LOOP when he asked for the voice loop.

    The request is recorded from woke_at, the moment the name was heard, with lead, the
    talk between the end of the name and the fire, in front: "Diablo, turn the lights off"
    in one breath is kept whole. The chime only says he is listening; nobody has to wait
    for it. tl, a Timeline, collects where the time went."""
    tl = tl or timeline.Timeline("follow-up")
    tl.mark("name")
    set_state("listening", holding=False)
    # The chime first, before anything slow, so he hears at once that the name landed.
    chime.play("listen")
    tl.mark("chime")
    if "fired" in tl.points:
        log.info("chime %d ms after the name, %d ms after the fire",
                 tl.points["chime"], tl.points["chime"] - tl.points["fired"])
    cfg = settings.get()
    # The screen as it was when he said the name, before anything here changes it.
    seen = surroundings.Snapshot()
    # Sound is held only while he listens, from just after the chime to the done chime, by
    # his gvoice rules (pause, dim, mute), and given back at once: a request like "pause
    # the music" runs after this, so a late give back can never undo it. The reply takes
    # a hold of its own, and the gap only bridges replies.
    with speakers.Hold(gap=0):
        if woke_at is not None:
            # Just after the block the name fired on; lead carries what came before it.
            after = woke_at + 0.001
        else:
            # A follow-up turn: he waits for the chime here, so skip most of it.
            after = time.monotonic() + chime.seconds("listen") * 0.6
        heard = record(mic, cfg, after, vad, start_audio=lead,
                       probe=lambda clip: transcribe(clip, cfg), hold=set_holding, tl=tl,
                       early=lambda words: early_read(cfg, words, seen.desktop or commands.desktop_state()))
        audio, spoke = heard
    if not spoke:
        log.info("woke, heard nothing")
        tl.note(outcome="heard nothing")
        chime.play("never")
        return False
    checked = time.monotonic()
    # Measured before the name, so the listen chime never counts as music; a follow-up turn
    # has no name to measure at and gets none.
    slack = sound_slack(tap, since=woke_at - 2.0, until=woke_at) if woke_at is not None else 0.0
    ok, voice = talk_ok(audio, cfg, slack=slack)
    tl.mark("voice_ok")
    tl.note(voice_check_ms=round((time.monotonic() - checked) * 1000))
    if not ok:
        # Seconds of talk is where his voice is clear: a video or someone else that got
        # past the wake check is caught here, before anything reaches the hub.
        log.info("request refused, voice %.3f against his talk", voice)
        tl.note(outcome=f"refused, voice {voice:.2f}")
        chime.play("never")
        return False
    if heard.early is None:
        # An early act gets only its own ok sound, not the heard chime as well.
        chime.play("done")
    set_state("thinking", **({} if heard.text is not None else {"step": "transcribing"}))
    try:
        # The end-of-turn check already has the words when nothing was said after it.
        started = time.monotonic()
        text = heard.text if heard.text is not None else transcribe(audio, cfg)
        tl.note(stt_ms=round((time.monotonic() - started) * 1000), stt_reused=heard.text is not None)
    except Exception as error:
        log.warning("speech to text failed: %s", error)
        tl.note(outcome="speech to text failed")
        chime.play("never")
        return False
    tl.mark("text")
    set_state("thinking", step="")
    tl.note(text=text, recording=recordings.keep(audio, text, tl.info.get("kind", "wake"), heard.early is not None))
    if stt.is_noise(text):
        log.info("woke, transcript empty or noise: %r", text)
        tl.note(outcome="noise")
        chime.play("never")
        return False
    log.info("heard: %s", text)
    if commands.loop_intent(text) == "start":
        tl.note(outcome="speech control on")
        return LOOP
    local = local_line(mic, settings, vad, text, tl, speaker)
    if local is not None:
        return say_answer(mic, speaker, local, cfg, detector, tap, tl)
    if heard.early is not None:
        note_control(tl, heard.early)
        return say_answer(mic, speaker, run_act(heard.early["act"], tl, cfg), cfg, detector, tap, tl)
    context, shot = seen.get()
    words = answer_or_background(lambda say: answer_words(cfg, text, context, shot, seen.desktop, tl, say), tl, text)
    if words is None:
        return False
    return say_answer(mic, speaker, words, cfg, detector, tap, tl)


def say_answer(mic, speaker, words, cfg, detector, tap, tl=None, first=False):
    """Speaks the answer, or with first his line ahead of the work. True when Marcus cut it
    off with the name."""
    log.info("%s: %s", "first" if first else "reply", words[:200])
    if not words:
        return False
    tl = tl or timeline.Timeline()
    marks = ("ack_ready", "ack_spoken") if first else ("tts_ready", "spoken")
    if not first:
        tl.note(reply=words[:300])
    if not tl.info.get("act", "").startswith("quick"):
        presence.show_reply(words)
    set_state("speaking")
    if detector is None:
        speaker.say(words, cfg, interruptible=False)
        tl.mark(marks[1])
        return False
    return speak_until_named(mic, speaker, words, cfg, detector, tap, tl, marks)


def loop_turn(mic, settings, speaker, vad, detector=None, tap=None, dictation=None, yields=True):
    """One line inside the voice loop, no name needed. None when nothing was said or the
    line was not for him, STOP when he ended the loop, True when a line was handled.
    yields: a ready answer may end the wait for his line. False right after he cut an answer
    off with the name: the line he cut in to say comes before the answers put back."""
    cfg = dict(settings.get())
    cfg["ears.turn_max_s"] = cfg.get("ears.loop_turn_max_s") or cfg.get("ears.turn_max_s") or LOOP_TURN_MAX_S
    set_state("listening", holding=False, loop=True)
    tl = timeline.Timeline("loop")
    heard = record(mic, cfg, time.monotonic(), vad, probe=lambda clip: transcribe(clip, cfg),
                   no_speech_s=LOOP_LISTEN_S, tl=tl,
                   early=lambda words: early_read(cfg, words, commands.desktop_state()),
                   interrupt=PENDING.has_speech if yields else None)
    audio, spoke = heard
    if not spoke:
        return None
    # A loop line has no name: its clock starts when he started talking.
    tl.rebase("speech_start")
    try:
        return _loop_line(mic, settings, speaker, vad, detector, tap, dictation, cfg, heard, tl)
    finally:
        if not tl.info.get("background"):
            tl.mark("end")
            tl.save()


def _loop_line(mic, settings, speaker, vad, detector, tap, dictation, cfg, heard, tl):
    audio = heard[0]
    if dictation is not None and dictation.busy():
        # He was dictating into another app, not talking to Diablo.
        log.info("loop: dictation is on, line ignored")
        tl.note(outcome="ignored, dictating")
        return None
    checked = time.monotonic()
    ok, voice = talk_ok(audio, cfg)
    tl.mark("voice_ok")
    tl.note(voice_check_ms=round((time.monotonic() - checked) * 1000))
    if not ok:
        log.info("loop: line refused, voice %.3f against his talk", voice)
        tl.note(outcome=f"refused, voice {voice:.2f}")
        return None
    if heard.early is None:
        chime.play("done")
    set_state("thinking", loop=True, **({} if heard.text is not None else {"step": "transcribing"}))
    try:
        started = time.monotonic()
        text = heard.text if heard.text is not None else transcribe(audio, cfg)
        tl.note(stt_ms=round((time.monotonic() - started) * 1000), stt_reused=heard.text is not None)
    except Exception as error:
        log.warning("loop: speech to text failed: %s", error)
        tl.note(outcome="speech to text failed")
        chime.play("never")
        return True
    tl.mark("text")
    set_state("thinking", step="")
    tl.note(text=text, recording=recordings.keep(audio, text, tl.info.get("kind", "wake"), heard.early is not None))
    if stt.is_noise(text):
        log.info("loop: transcript empty or noise: %r", text)
        tl.note(outcome="noise")
        return None
    log.info("loop heard: %s", text)
    if commands.loop_intent(text, active=True) == "stop":
        tl.note(outcome="speech control off")
        return STOP
    local = local_line(mic, settings, vad, text, tl, speaker)
    if local is not None:
        say_answer(mic, speaker, local, cfg, detector, tap, tl)
        return True
    desktop = commands.desktop_state()
    if heard.early is not None:
        found = heard.early
        note_control(tl, found)
    else:
        tl.mark("hub_sent")
        try:
            found = control(cfg, text, with_recent(desktop))
        except (httpx.HTTPError, ValueError) as error:
            log.warning("control call failed, the line goes to the full turn: %s", error)
            found = {}
        tl.mark("hub_back")
        note_control(tl, found)
    if isinstance(found.get("act"), dict):
        say_answer(mic, speaker, run_act(found["act"], tl, cfg), cfg, detector, tap, tl)
        if found["act"].get("action") in REDIM_AFTER:
            redim()
        return True
    # Not a plain command: the whole turn, Claude included, as if he had said the name.
    tl.points.pop("hub_sent", None)
    tl.points.pop("hub_back", None)
    context = surroundings.lines()
    shot = surroundings.screenshot() if surroundings.points_at_screen(text) else None
    words = answer_or_background(lambda say: answer_words(cfg, text, context, shot, desktop, tl, say), tl, text)
    if words is not None:
        say_answer(mic, speaker, words, cfg, detector, tap, tl)
    return True


# Speech control dims what plays for as long as it is on, so his voice is not lost under the
# music: a line was refused and another misheard with Spotify at full volume (2026-11-11).
LOOP_HOLD = {"hold": None}
LOOP_DIM_TO = 25
# Acts that can start sound playing: the dim is taken again after them.
REDIM_AFTER = ("play", "next", "previous", "unmute", "volume_set", "volume_up", "open_thread", "browser_tab")


def loop_dim_to(cfg):
    try:
        return max(0, min(100, int(float(cfg.get("ears.loop_dim_to") or LOOP_DIM_TO))))
    except (TypeError, ValueError):
        return LOOP_DIM_TO


def redim():
    """Gives the dim back and takes it again, so a song he just started is dimmed too."""
    hold = LOOP_HOLD["hold"]
    if hold is None:
        return
    hold.__exit__(None, None, None)
    hold.__enter__()


def voice_loop(mic, settings, speaker, vad, detector=None, tap=None, dictation=None):
    """"Diablo, start voice loop": every line is a request, no name, until he says stop voice
    loop or nothing is said for ears.loop_idle_s. Sound is not held in the loop, so music
    keeps playing; the voice check on each line keeps a song from counting."""
    cfg = settings.get()
    try:
        idle_s = min(max(float(cfg.get("ears.loop_idle_s") or LOOP_IDLE_S), 10.0), 1800.0)
    except (TypeError, ValueError):
        idle_s = LOOP_IDLE_S
    log.info("speech control on, ends after %.0f s of nothing", idle_s)
    set_state("listening", loop=True)
    feedback.mode(True)
    LOOP_HOLD["hold"] = speakers.Hold(mode="dim", dim_to=loop_dim_to(cfg), gap=0)
    LOOP_HOLD["hold"].__enter__()
    mic.drain()
    last = time.monotonic()
    while listening() and time.monotonic() - last < idle_s:
        cut = say_ready(mic, speaker, settings.get(), detector, tap)
        if cut:
            mic.drain()
        try:
            result = loop_turn(mic, settings, speaker, vad, detector, tap, dictation, yields=not cut)
        except Exception:
            log.exception("loop turn failed")
            result = None
        if result == STOP:
            break
        if result:
            last = time.monotonic()
            mic.drain()
            if detector is not None:
                detector.reset()
    log.info("speech control off")
    hold, LOOP_HOLD["hold"] = LOOP_HOLD["hold"], None
    if hold is not None:
        hold.__exit__(None, None, None)
    set_state("asleep", loop=False)
    feedback.mode(False)
    return False


# While he talks, his own voice fills the mic. The voice check gets this much slack, since
# the clip it judges is Marcus mixed with him; the speaker tap is what keeps his own
# "Diablo" from counting.
BARGE_SLACK = 0.1
MIN_BARGE_BLOCKS = 12


def speak_until_named(mic, speaker, words, cfg, detector, tap, tl=None, marks=("tts_ready", "spoken")):
    """Speaks the reply while still listening for the name. True when Marcus said it."""
    import threading

    done, cancel = threading.Event(), threading.Event()

    def talk():
        try:
            if vstate.is_deaf():
                return
            # Fetch, then play, so a cut during the fetch means nothing plays at all.
            wav = speaker._fetch(words, cfg)
            if tl is not None:
                tl.mark(marks[0])
            if wav and not cancel.is_set():
                speaker._speak([wav], False, cfg)
                if tl is not None:
                    tl.mark(marks[1])
        except Exception:
            log.exception("turn failed while speaking")
        finally:
            done.set()

    mic.drain()
    detector.reset()
    threading.Thread(target=talk, daemon=True).start()
    history = []
    while not done.is_set():
        try:
            stamp, block = mic.read(timeout=0.5)
        except queue.Empty:
            continue
        history = (history + [block])[-20:]
        if not detector.feed(wake.to_int16(block)):
            continue
        if tap is not None and tap.said_name(at=stamp):
            log.info("his own name in his reply, not an interruption")
            detector.reset()
            continue
        # Under a second in, the clip is mostly his own voice and a voiceprint of it means
        # little; the speaker tap above is the guard there.
        if len(history) >= MIN_BARGE_BLOCKS:
            ok, voice = speaker_ok(np.concatenate(history), cfg, slack=BARGE_SLACK)
        else:
            ok, voice = True, None
        if not ok:
            log.info("name over the reply refused, voice %.3f", voice)
            detector.reset()
            continue
        log.info("interrupted at %.3f", detector.last)
        if tl is not None:
            tl.mark(marks[1])
            tl.note(cut_off=True)
        cancel.set()
        # Repeat the stop until playback is gone: one stop can land between the clip
        # starting and the speaker marking itself as speaking.
        until = time.monotonic() + 3.0
        while not done.is_set() and time.monotonic() < until:
            speaker.stop_now()
            done.wait(0.05)
        if not done.is_set():
            log.warning("reply still fetching after the cut; it will not play")
        return True
    return False


def run():
    from . import ears_api
    from .ears_train import Session
    from .tts import Speaker
    from .vad import SileroVAD

    global LIVE, REPORT
    cfg = load_config()
    REPORT = HubReport(cfg)
    atexit.register(REPORT.stop)
    settings = Settings(cfg)
    # Jev straight from here for speech control and early ends, on the hub's own rules.
    try:
        jevfast.bind(settings)
    except Exception:
        log.exception("direct Jev unavailable, every Jev call goes through the hub")
    vad = SileroVAD(cfg["silero_path"])
    speaker = Speaker(cfg, _Quiet())
    # Every model a wake or a reply uses is loaded and run once before the mic opens, so
    # nothing loads on the wake path, the first wake after a start included.
    warming = time.monotonic()
    aligner = mouth.warm()
    voice_s = voiceid.warm()
    settings.get()
    aligner.join(timeout=WARM_WAIT_S)
    log.info("warm in %.1f s (voiceprint %s), listening", time.monotonic() - warming,
             f"{voice_s:.1f} s" if voice_s is not None else "off")
    # No gap before a reply. With one, a cut that lands in the gap is cleared when the
    # gap ends and the old reply plays over the next turn.
    speaker._gap = lambda: 0.0
    LIVE = live = ears_api.Live()
    speakers.Hold.recover()
    atexit.register(speakers.shutdown)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    with Mic(cfg) as mic:
        ears = {"detector": None, "tap": speakers.Speakers()}

        def reload():
            if ears["tap"] is not None:
                ears["tap"].close()
            ears["tap"] = speakers.Speakers()
            ears["detector"] = wake.Detector() if wake.trained() else None
            if ears["detector"] is not None:
                log.info("wake model loaded, threshold %.3f", ears["detector"].threshold)

        def start_session(fresh, retrain, places=None):
            set_state("training")
            return Session(cfg, mic, settings, live=live, tap=ears["tap"], fresh=fresh, retrain=retrain,
                           places=places).start()

        def speaker_level():
            tap = ears["tap"]
            return tap.level(0.2) if tap is not None else 0.0

        dictation = DictationWatch(cfg)
        controller = ears_api.Controller(live, start_session, listening, set_listening,
                                         mic.take_level, speaker_level)
        try:
            ears_api.serve(controller)
        except OSError as error:
            log.warning("control panel unavailable: %s", error)
        reload()
        loop(mic, settings, speaker, vad, controller, ears, reload, live, cfg, dictation)


WARM_WAIT_S = 20


def timed_turn(tl, *args):
    """turn(), with its timeline saved however it ends."""
    try:
        return turn(*args, tl=tl)
    except Exception:
        tl.note(outcome="failed")
        raise
    finally:
        if not tl.info.get("background"):
            tl.mark("end")
            tl.save()


def loop(mic, settings, speaker, vad, controller, ears, reload, live, cfg, dictation=None):
    history, highs, training, untrained_at = [], [], False, 0.0
    ambient = Ambient()
    while True:
        session = controller.session
        if session is not None and session.active:
            # The session reads the mic now; the loop only waits for it to finish.
            training = True
            set_state("training")
            time.sleep(0.1)
            continue
        if training:
            training = False
            mic.drain()
            reload()
            history.clear()
        try:
            stamp, block = mic.read(timeout=2.0)
        except queue.Empty:
            continue
        read_at = time.monotonic()
        if mic.overflows:
            # Lost audio can hide a wake; say so instead of failing quietly.
            log.warning("mic input overflowed %d times", mic.overflows)
            mic.overflows = 0
        if not listening():
            set_state("off")
            live.score(0.0)
            continue
        detector = ears["detector"]
        if detector is None:
            # Untrained: look for a model and point at the setup every 20 s, not every block.
            if time.monotonic() - untrained_at >= 20:
                untrained_at = time.monotonic()
                if wake.trained():
                    reload()
                    continue
                notify_untrained()
            set_state("untrained")
            continue
        if detector.stale():
            reload()
            continue
        set_state("asleep")
        tap = ears["tap"]
        if PENDING.has_speech():
            try:
                cut = say_ready(mic, speaker, settings.get(), detector, tap)
                while cut:
                    mic.drain()
                    detector.reset()
                    cut = timed_turn(timeline.Timeline("follow-up"), mic, settings, speaker, vad, detector, tap)
                    if cut == LOOP:
                        cut = voice_loop(mic, settings, speaker, vad, detector, tap, dictation)
            except Exception:
                log.exception("speaking a finished answer failed")
            mic.drain()
            detector.reset()
            history.clear()
            highs.clear()
            continue
        history = (history + [block])[-25:]
        woke = detector.feed(wake.to_int16(block))
        highs = (highs + [detector.last >= detector.threshold])[-25:]
        live.score(detector.last, detector.threshold)
        if woke and controller.playing():
            # A false wake being played back from the Voice page says the name on purpose.
            log.info("wake %.3f ignored, a wake clip is playing", detector.last)
            detector.reset()
            continue
        if not woke:
            ambient.feed(history, detector.last)
            continue
        score = detector.last
        backlog = mic.backlog()
        clip = ears_api.keep_clip(np.concatenate(history))
        checks = {"read": read_at, "clip": time.monotonic()}
        # The fire comes a few blocks after the word ends (the patience). What he said in
        # those blocks is the start of the request: from the first block of the run over
        # the line, which still holds the end of the name, onwards.
        run = 0
        while run < len(highs) and highs[-1 - run]:
            run += 1
        lead = np.concatenate(history[len(history) - run + 1:]) if run > 1 else None
        # Claim the mic before the checks, so a setup cannot start in the gap before the turn.
        set_state("waking")
        if dictation is not None and dictation.busy():
            # First, before anything else: his name in a dictated sentence is not a call.
            log.info("wake %.3f ignored, dictation is on", score)
            live.wake(score, None, "dictation", clip)
            detector.reset()
            continue
        checks["dictation"] = time.monotonic()
        if tap.said_name(at=stamp):
            # The name came out of the speakers: a video, a voice note, his own voice.
            log.info("wake %.3f refused, the speakers said it", score)
            live.wake(score, None, "echo", clip)
            detector.reset()
            continue
        checks["echo"] = time.monotonic()
        ok, voice = speaker_ok(np.concatenate(history[-20:]), settings.get(),
                               slack=sound_slack(tap, since=stamp - 2.0, until=stamp))
        checks["voiceprint"] = time.monotonic()
        if not ok:
            log.info("wake %.3f refused, voice %.3f", score, voice)
            live.wake(score, voice, "voice", clip)
            detector.reset()
            continue
        if controller.session is not None and controller.session.active:
            detector.reset()
            continue
        log.info("wake %.3f, voice %s", score, f"{voice:.3f}" if voice is not None else "unchecked")
        # The fire lands `run` blocks after the name ended; the clock starts at the name.
        tl = timeline.Timeline("wake", start=stamp - max(run - 1, 0) * len(block) / wake.RATE)
        tl.mark("name", at=tl.t0)
        tl.mark("fired", at=stamp)
        for point, at in checks.items():
            tl.mark(point, at=at)
        tl.note(wake=round(score, 3), voice=round(voice, 3) if voice is not None else None,
                backlog=backlog or None)
        live.wake(score, voice, "answered", clip)
        try:
            # A reply cut off with the name goes straight into the next turn.
            woke_at = stamp
            result = timed_turn(tl, mic, settings, speaker, vad, detector, tap, woke_at, lead)
            while result:
                mic.drain()
                detector.reset()
                if result == LOOP:
                    result = voice_loop(mic, settings, speaker, vad, detector, tap, dictation)
                else:
                    result = timed_turn(timeline.Timeline("follow-up"), mic, settings, speaker, vad, detector, tap)
        except Exception:
            log.exception("turn failed")
        mic.drain()
        detector.reset()
        history.clear()
        highs.clear()
        set_state("asleep")


def _api(path, body=None):
    """The control panel. Raises OSError with the server's own words on an error status."""
    import urllib.error
    import urllib.request

    from .ears_api import PORT

    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-Diablo-Ears": "1", "Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get("error") or str(error)
        except ValueError:
            message = str(error)
        raise OSError(message) from None


def status():
    try:
        info = _api("/v1/ears")
    except OSError:
        print("the ears service is not running: systemctl --user start diablo-ears")
        return 1
    print("service   running")
    print(f"state     {info['state']}{'' if info['listening'] else ' (listening off)'}")
    model = info.get("model")
    if not model:
        print("model     not trained yet: Diablo desktop, Settings, Voice")
        return 0
    print(f"model     trained {model.get('trained_at')} on {model.get('recordings')} recordings")
    print(f"threshold {model.get('threshold'):.3f}")
    print(f"recall    {model.get('held_out_recall_clean', 0):.0%} clean, "
          f"{model.get('held_out_recall_noisy', 0):.0%} with room noise ({model.get('held_out')} held out)")
    print(f"false     {model.get('validation_fires')} wakes in {model.get('validation_hours')} h of other audio")
    if model.get("speaker_min") is not None:
        print(f"voice     needs {model['speaker_min']:.2f}, his own recordings score {model.get('speaker_self_mean', 0):.2f} on average")
    return 0


def train_cli(fresh=False, retrain=False):
    """The setup from a terminal: the same session the desktop app shows, printed."""
    try:
        _api("/v1/ears/train", {"fresh": fresh, "retrain": retrain})
    except OSError as error:
        print(f"could not start: {error}")
        return 1
    shown = {"said": "", "passage": "", "takes": 0, "stage": ""}
    try:
        while True:
            session = _api("/v1/ears").get("session") or {}
            if session.get("said") and session["said"] != shown["said"]:
                shown["said"] = session["said"]
                print(f"Diablo: {session['said']}", flush=True)
            if session.get("passage") and session["passage"] != shown["passage"]:
                shown["passage"] = session["passage"]
                print(f"\n  {session['passage']}\n", flush=True)
            for take in session.get("takes", [])[shown["takes"]:]:
                print(f"  {'ok' if take['ok'] else 'again'} {take['seconds']:.2f} s {take['why']}", flush=True)
            shown["takes"] = len(session.get("takes", []))
            if not session.get("active"):
                if session.get("error"):
                    print(session["error"])
                    return 1
                return 0
            time.sleep(0.3)
    except KeyboardInterrupt:
        try:
            _api("/v1/ears/train/stop", {})
        except OSError:
            pass
        print("\nstopped. Every take so far is kept.")
        return 130
    except OSError as error:
        print(f"lost the ears: {error}")
        return 1


USAGE = """diablo-ears: say Diablo, hear a chime, talk, he answers.

The setup and the meter live in the desktop app: Settings, then Voice.

  diablo-ears status      model, threshold, measured false wakes, service state
  diablo-ears train       the setup from a terminal (--fresh drops saved takes of the name)
  diablo-ears retrain     learn again from the takes already saved
  diablo-ears chimes      play every sound he makes, one after another
  diablo-ears timings     where each recent turn's time went (--last N, --html FILE)
  diablo-ears recordings  the last turns' recordings and what was heard (a number shows more)
  diablo-ears recheck     run a recording (an id, or the last) through gvoice again
  diablo-ears run         the service itself (diablo-ears.service runs this)
"""


def recheck_cli(clip):
    """Runs a kept recording through gvoice again and prints what it says beside what the
    turn heard."""
    found = recordings.path(clip)
    if found is None:
        print("no such recording; diablo-ears recordings lists them")
        return 1
    meta = {}
    try:
        meta = json.loads(found.with_suffix(".json").read_text())
    except (OSError, ValueError):
        pass
    print(f"recording  {found}")
    print(f"the turn heard  {meta.get('text', '')!r}" + ("  (Jev ended it early)" if meta.get("early") else ""))
    try:
        print(f"gvoice now      {stt.transcribe_file(str(found), load_config())!r}")
    except Exception as error:
        print(f"gvoice failed: {error}")
        return 1
    return 0


def timings_cli(args):
    """The last turns as a waterfall: in the terminal, or as a page with --html FILE."""
    last, page = 10, None
    rest = list(args)
    while rest:
        flag = rest.pop(0)
        if flag == "--last" and rest and rest[0].isdigit():
            last = int(rest.pop(0))
        elif flag == "--html" and rest:
            page = rest.pop(0)
        else:
            print("usage: diablo-ears timings [--last N] [--html FILE]")
            return 1
    rows = timeline.load(last=last)
    if page:
        Path(page).write_text(timeline.html_report(rows))
        print(f"{len(rows)} turns written to {page}")
        return 0
    print(timeline.text_report(rows))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # The dictation watcher asks four times a second; one log line each is noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    command = argv[0] if argv else "run"
    if command == "run":
        run()
        return 0
    if command == "learn":
        from .ears_train import learn_main

        return learn_main()
    if command in ("train", "retrain"):
        return train_cli(fresh="--fresh" in argv, retrain=command == "retrain")
    if command == "status":
        return status()
    if command == "timings":
        return timings_cli(argv[1:])
    if command == "recordings":
        for row in recordings.listed(int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 10):
            stamp = time.strftime("%H:%M:%S", time.localtime(row.get("at", 0)))
            print(f"{row['id']}  {stamp}  {row.get('seconds', 0):5.1f} s  {row.get('kind', ''):9} {row.get('text', '')!r}")
        return 0
    if command == "recheck":
        return recheck_cli(argv[1] if len(argv) > 1 else "last")
    if command == "chimes":
        for name in ("listen", "done", "never", "ok", "back", "mode_on", "mode_off", "dictate_on", "piece", "dictate_off", "thinking"):
            print(name)
            chime.play(name, wait=True)
            time.sleep(0.6)
        return 0
    print(USAGE)
    return 0 if command in ("-h", "--help", "help") else 1


if __name__ == "__main__":
    os.environ.setdefault("ORT_LOGGING_LEVEL", "3")
    sys.exit(main())
