import collections
import json
import logging
import os
import queue
import re
import sys
import tempfile
import warnings
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

from . import activity, agent, engage, fast, gate, healer, hubclient, intents, phrases, skills, stt, tester, trainer, voiceid
from . import interrupt
from . import state as vstate
from .capture import Capture
from .config import load_config
from .tts import Speaker, _wav_seconds_bytes as tts_seconds
from .vad import SileroVAD

log = logging.getLogger("diablo.voice")

REBOOT_FILE = Path.home() / ".local/state/diablo/reboot"
STATE_DIR = Path.home() / ".local/state/diablo"
FAST_IN = STATE_DIR / "fast.in"
FAST_OUT = STATE_DIR / "fast.out"
TASKS_DIR = STATE_DIR / "tasks"
UI_PATTERN = re.compile(
    r"\b(your|the|his|diablo'?s?)\s+(ui|u\.i\.|dashboard|page|screen|frontend|front.?end)\b"
    r"|\bui\b",
    re.IGNORECASE,
)
UI_VERB_PATTERN = re.compile(
    r"\b(change|fix|edit|add|remove|update|alter|modify|clean|redesign|tweak|improve"
    r"|can|could|able|want|need)\b",
    re.IGNORECASE,
)


SIGHT_PATTERN = re.compile(
    r"\b(what am i doing|what was i doing|what did i|look at (what|my)|watch me"
    r"|my screen|on my screen|on screen)\b",
    re.IGNORECASE,
)


def looks_like_ui(text):
    return bool(UI_PATTERN.search(text) and UI_VERB_PATTERN.search(text))


def looks_like_sight(text):
    return bool(SIGHT_PATTERN.search(text))


def setup_logging():
    state = Path(os.path.expanduser("~/.local/state/diablo"))
    state.mkdir(parents=True, exist_ok=True)
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated")
    plain = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    class Tinted(logging.Formatter):
        ACCENT = "\033[92m"
        MIND = "\033[96m"
        STATE = "\033[33m"
        BAD = "\033[91m"
        DIM = "\033[2m"
        LEVEL = {"WARNING": STATE, "ERROR": BAD, "CRITICAL": BAD}
        # Font Awesome glyphs, one icon per meaning.
        ICON_HEARD = "\uf130 "
        ICON_REPLY = "\uf075 "
        ICON_SAID = "\uf086 "
        ICON_TASK = "\uf0ae "
        ICON_DONE = "\uf00c "
        ICON_LEARN = "\uf02d "
        ICON_TRAIN = "\uf0e7 "
        ICON_DEAF = "\uf2a4 "
        ICON_DEAF_OPEN = "\uf2a2 "
        ICON_MUTE = "\uf131 "
        ICON_QUEUE = "\uf254 "
        MINDS = (
            ("said", ICON_SAID),
            ("fast reply", ICON_REPLY),
            ("task dispatched", ICON_TASK),
            ("task finished", ICON_DONE),
            ("learned", ICON_LEARN),
            ("voice training", ICON_TRAIN),
        )
        STATES = (
            ("deafened", ICON_DEAF),
            ("deaf open", ICON_DEAF_OPEN),
            ("muted", ICON_MUTE),
            ("unmuted", ICON_MUTE),
            ("silent, text only", ICON_MUTE),
            ("queued until he is free", ICON_QUEUE),
        )

        def format(self, record):
            out = plain.format(record)
            head, _, rest = out.partition(" ")
            head2, _, rest = rest.partition(" ")
            level, _, rest = rest.partition(" ")
            src, _, rest = rest.partition(" ")
            line = f"{self.DIM}{head} {head2} {level} {src}\033[0m "
            level_tint = self.LEVEL.get(level)
            if level_tint:
                return line + f"{level_tint}{rest}\033[0m"
            if rest.startswith("heard from "):
                who, _, body = rest[len("heard from "):].partition(" (")
                return line + f"{self.ACCENT}{self.ICON_HEARD}Heard from {who}\033[0m ({body}"
            for tag, icon in self.MINDS:
                if rest.startswith(tag):
                    words, sep, tail = rest.partition(":")
                    cap = words[:1].upper() + words[1:]
                    if sep:
                        return line + f"{self.MIND}{icon}{cap}{sep}\033[0m{tail}"
                    return line + f"{self.MIND}{icon}{cap}\033[0m"
            for tag, icon in self.STATES:
                if rest.startswith(tag):
                    return line + f"{self.STATE}{icon}{rest[:1].upper()}{rest[1:]}\033[0m"
            return line + rest

    file_handler = RotatingFileHandler(
        state / "voice.log", maxBytes=2_000_000, backupCount=3
    )
    file_handler.setFormatter(plain)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(Tinted())
    # A second stream that keeps the escapes: journalctl strips ANSI, this file doesn't.
    tinted_file = RotatingFileHandler(
        state / "voice-color.log", maxBytes=2_000_000, backupCount=3
    )
    tinted_file.setFormatter(Tinted())
    root = logging.getLogger("diablo")
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    root.addHandler(tinted_file)


def transcribe(audio, sample_rate, cfg):
    with tempfile.NamedTemporaryFile(suffix=".wav", dir="/tmp/opencode", delete=False) as handle:
        path = handle.name
    try:
        stt.write_wav(audio, sample_rate, path)
        return stt.transcribe_file(path, cfg)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    setup_logging()
    cfg = load_config()
    voiceid.migrate()
    vad = SileroVAD(cfg["silero_path"])
    chat_state = {"history": [], "facts": [], "facts_ts": 0.0, "engaged_until": 0.0}
    engage_window = float(cfg.get("engage_window_s", 45.0))
    capture = Capture(
        cfg,
        vad,
        pause_fn=lambda: stt.is_dictating(cfg),
        engaged_fn=lambda: time.monotonic() < chat_state["engaged_until"],
    )
    speaker = Speaker(cfg, capture)
    work = queue.Queue()
    gate_times = collections.deque()
    limit = int(cfg["gate_calls_per_5min"])
    join_window = float(cfg.get("join_window_s", 0.8))
    busy = {"on": False}
    deaf_epoch = {"n": 0}
    training = trainer.blank()
    testing = tester.blank()

    def mic_owner():
        """The state name while a session owns the microphone, else empty."""
        if training["active"]:
            return "deaf (training)"
        if testing["active"]:
            return "deaf (testing)"
        return ""
    speech_lock = threading.Lock()
    speech_queue = []
    say_lock = threading.Lock()
    # He always listens: the room is heard from the first second, but nothing
    # is ever acted upon, spoken or answered, until his name is in it.
    vstate.set_muted(False)
    vstate.set_deaf(False)
    capture.deaf = False
    capture.start()
    log.info("always listening: nothing is answered until he is named")
    log.info(
        "listening on %s, hub %s, cancellation %s",
        capture.device_name(),
        cfg["hub_url"],
        "on" if capture.monitor_active else "off",
    )
    vstate.write_state("listening")

    def heartbeat():
        while True:
            time.sleep(15)
            if vstate.is_deaf():
                vstate.write_state("deaf")
            elif stt.is_dictating(cfg):
                vstate.write_state("deaf (dictation)")
            elif mic_owner():
                settle()
            else:
                vstate.refresh()

    threading.Thread(target=heartbeat, daemon=True).start()

    def live_settings():
        return {**cfg, **hubclient.get_settings(cfg)}

    def speaking_on(live=None):
        """Speaking and listening is a MODE, off by default. He turns it on
        from the app or by name through deaf ears; nothing speaks or engages
        while it is off."""
        live = live or live_settings()
        return str(live.get("speaking_mode") or "off").strip().lower() in ("on", "1", "true", "yes")

    def settle():
        """Back to listening, unless a training or test session still owns the microphone."""
        owner = mic_owner()
        if owner:
            vstate.write_state(owner, training["stage"] if training["active"] else "")
        elif vstate.is_deaf() or not speaking_on():
            vstate.write_state("deaf" if vstate.is_deaf() else "resting")
        else:
            vstate.write_state("listening")

    phrases.start(live_settings, speaker, log)

    def remember_exchange(live, user_text, assistant_text):
        history = chat_state["history"]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})
        del history[:-8]

    def learn(live, user_text, assistant_text):
        try:
            facts = gate.extract_facts(user_text, assistant_text, live)
        except Exception:
            log.exception("fact extraction failed")
            return
        for fact in facts:
            if hubclient.add_fact(live, fact, tags=["learned"]):
                log.info("learned: %s", fact)
                chat_state["facts"] = []

    def ask_fast(text, timeout=90):
        """Write to the fast tongue, wait for its reply."""
        try:
            fd = os.open(FAST_IN, os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, (text + "\n").encode())
            os.close(fd)
        except OSError as error:
            log.error("fast panel unreachable: %s", error)
            return ""
        try:
            before = FAST_OUT.stat().st_mtime_ns
        except OSError:
            before = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.4)
            try:
                if FAST_OUT.stat().st_mtime_ns != before:
                    return FAST_OUT.read_text().strip()
            except OSError:
                pass
        log.warning("fast reply silent for %.0fs", timeout)
        return ""

    def current_facts(live):
        if time.monotonic() - chat_state["facts_ts"] > 60 or not chat_state["facts"]:
            chat_state["facts"] = [
                str(f.get("text") or f) for f in hubclient.get_context(live, limit=1)["facts"]
            ][:10]
            chat_state["facts_ts"] = time.monotonic()
        return chat_state["facts"]

    def acknowledge(live, bank="ack"):
        """Speaks a cached line from a bank; no API call when the bank is warm."""
        cached = phrases.pick(live, bank)
        if not cached:
            return ""
        text, wav = cached
        if not vstate.is_muted():
            vstate.write_state("speaking")
            speaker.play_wav(wav)
        return text

    def hand_off(kind, name, prompt, live):
        """Dispatches to Raphael and returns what was said out loud."""
        task_id = fast.dispatch(name, prompt, kind=kind)
        if not task_id:
            fallback = "Raphael is not answering."
            if not vstate.is_muted():
                vstate.write_state("speaking")
                speaker.say(fallback, live, interruptible=False)
            return fallback
        activity.log_event("task", f"dispatched: {name}")
        return acknowledge(live) or "One moment."

    def respond_direct(text, live, started):
        """The fast tier. Returns (reply_text, spoken_text); reply empty means it failed."""
        if fast.forced_think(text):
            log.info("sent to Raphael on his word: %s", text[:80])
            spoken = hand_off("think", text[:60], text, live)
            return f"THINK: {text}", spoken
        messages = fast.build_messages(text, chat_state["history"], current_facts(live), fast.recent_tasks())
        effort = live.get("fast_reasoning_effort") or "low"
        calls = []
        deltas = fast.stream_chat(live, messages, reasoning_effort=effort, tools=skills.specs(), calls=calls)
        full = []
        marks = {"first": None, "error": None}

        def parts():
            routed = False
            try:
                for index, sentence in enumerate(fast.sentences(deltas)):
                    full.append(sentence)
                    if index == 0 and fast.route(sentence)[0] != "say":
                        routed = True
                    if routed:
                        continue
                    if marks["first"] is None:
                        marks["first"] = time.monotonic() - started
                    yield sentence
            except Exception as error:
                marks["error"] = error

        if vstate.is_muted():
            for _ in parts():
                pass
        else:
            vstate.write_state("speaking")
            speaker.say_stream(parts(), live, interruptible=False)
        reply = " ".join(full)
        if marks["error"] and not reply and not calls:
            raise marks["error"]
        if calls:
            return finish_skills(text, live, started, messages, calls, marks)
        kind, name, prompt = fast.route(reply)
        if kind != "say":
            log.info("fast tongue routes (%s): %s", kind, name or prompt[:80])
            spoken = hand_off(kind, name or prompt[:60], prompt, live)
            return reply, spoken
        log.info(
            "fast tongue: %s (first word %.1fs, done %.1fs)",
            reply[:120], marks["first"] or 0.0, time.monotonic() - started,
        )
        return reply, reply

    def finish_skills(text, live, started, messages, calls, marks):
        """Runs the skills he asked for. Success is a cached line; anything else is spoken."""
        results = []
        all_quiet = True
        for call in calls:
            kind, ok, result = skills.run(call["name"], call["args"], {"cfg": live})
            results.append(result)
            activity.log_event("tool", f"{call['name']}: {result[:120]}")
            if kind != "act" or not ok:
                all_quiet = False
        summary = "; ".join(f"{c['name']}: {r}" for c, r in zip(calls, results))
        if all_quiet:
            bank = skills.bank_for(c["name"] for c in calls)
            spoken = acknowledge(live, bank) or acknowledge(live, "done") or "Done."
            log.info("skills done in %.1fs, cached line: %s", time.monotonic() - started, spoken)
            return f"[{summary}]", spoken
        # He has something to say: the result goes back once, no tools this time.
        follow = fast.tool_turn(messages, calls, results)
        effort = live.get("fast_reasoning_effort") or "low"
        deltas = fast.stream_chat(live, follow, reasoning_effort=effort)
        full = []

        def parts():
            try:
                for sentence in fast.sentences(deltas):
                    full.append(sentence)
                    if marks["first"] is None:
                        marks["first"] = time.monotonic() - started
                    yield sentence
            except Exception as error:
                marks["error"] = error

        if vstate.is_muted():
            for _ in parts():
                pass
        else:
            vstate.write_state("speaking")
            speaker.say_stream(parts(), live, interruptible=False)
        reply = " ".join(full)
        if marks["error"] and not reply:
            raise marks["error"]
        if not reply.strip():
            bank = skills.bank_for(call["name"] for call in calls)
            reply = acknowledge(live, bank) or acknowledge(live, "done") or "Done."
            if not vstate.is_muted() and not vstate.is_deaf():
                vstate.write_state("speaking")
                with say_lock:
                    speaker.say(reply, live, interruptible=False)
        log.info("skills answered: %s (first word %.1fs)", reply[:120], marks["first"] or 0.0)
        return f"{reply} [{summary}]", reply

    def respond_panel(text, live):
        """The old road: the opencode fast panel, used only if the direct call fails."""
        names_before = set(TASKS_DIR.glob("*.name")) if TASKS_DIR.exists() else set()
        reply = ask_fast(text)
        if TASKS_DIR.exists():
            for nf in TASKS_DIR.glob("*.name"):
                if nf not in names_before:
                    activity.log_event("task", f"dispatched: {nf.read_text().strip()}")
        if not reply:
            return "", ""
        log.info("fast panel: %s", reply[:120])
        if vstate.is_muted():
            log.info("muted, text only: %s", reply[:60])
        else:
            vstate.write_state("speaking")
            speaker.say(reply, live, interruptible=False)
        return reply, reply

    def handle(text, live=None):
        activity.log_event("heard", text)
        live = live or live_settings()
        if engage.decide(text) == "ignore":
            log.info("ignored, his name was not in it: %s", text[:60])
            settle()
            return
        wanted, named = intents.match(text)
        # The message is his and is being acted upon: the window counts from
        # here, so the barge-in ear stays open while he answers. The window
        # never opens a path to a response, only the name does that.
        window_s = float(live.get("engage_window_s") or engage_window)
        chat_state["engaged_until"] = time.monotonic() + window_s
        if wanted:
            for intent in wanted:
                control(intent, live, source="order")
            hubclient.post_message(live, "voice", "user", text)
            return
        vstate.write_state("thinking")
        started = time.monotonic()
        hubclient.post_message(live, "voice", "user", text)
        reply = spoken = ""
        try:
            reply, spoken = respond_direct(text, live, started)
        except Exception:
            log.exception("direct tongue failed, using the panel")
        if not reply:
            reply, spoken = respond_panel(text, live)
        if not reply:
            settle()
            return
        if interrupt.wrong_language(spoken):
            log.warning("wrong language, not spoken: %s", spoken[:80])
            spoken = "That reply came out in the wrong language, so I dropped it. Ask me again."
            reply = ""
        activity.log_event("say", spoken)
        log.info("said: %s", spoken)
        hubclient.post_message(live, "voice", "assistant", spoken if spoken == reply else f"{spoken} [{reply[:200]}]")
        # His memory holds what he said, never the skill bookkeeping, or he starts imitating it.
        remember_exchange(live, text, reply if reply.startswith(("TASK:", "THINK:")) else spoken)
        settle()
        threading.Thread(target=learn, args=(live, text, spoken), daemon=True).start()

    def task_watch():
        announced = set()
        time.sleep(3)
        try:
            for sf in TASKS_DIR.glob("*.status"):
                if sf.read_text().strip() == "done":
                    announced.add(sf.stem)
        except OSError:
            pass
        while True:
            time.sleep(2)
            try:
                if not TASKS_DIR.exists():
                    continue
                for status_file in sorted(TASKS_DIR.glob("*.status")):
                    tid = status_file.stem
                    if tid in announced or status_file.read_text().strip() != "done":
                        continue
                    announced.add(tid)
                    try:
                        name = (TASKS_DIR / f"{tid}.name").read_text().strip()
                    except OSError:
                        name = tid
                    try:
                        result = (TASKS_DIR / f"{tid}.clean").read_text().strip()
                    except OSError:
                        try:
                            result = (TASKS_DIR / f"{tid}.out").read_text().strip()
                        except OSError:
                            result = ""
                    log.info("task finished: %s", name)
                    log.info("task report: %s", result[:2000])
                    waited = 0
                    while waited < 300 and (
                        capture.env > capture.release_floor
                        or capture.playing
                        or vstate.is_deaf()
                        or stt.is_dictating(cfg)
                        or mic_owner()
                    ):
                        time.sleep(2)
                        waited += 2
                    try:
                        kind = (TASKS_DIR / f"{tid}.kind").read_text().strip()
                    except OSError:
                        kind = "task"
                    if kind == "think":
                        spoken = result[:600] or f"Raphael had nothing to say about {name}."
                    else:
                        spoken = f"{name} is finished. {result[:500]}"
                    live = live_settings()
                    if not speaking_on(live):
                        # The report lives in the ledger, it is not spoken over the room.
                        log.info("task report, not spoken, speaking mode is off: %s", spoken[:120])
                        hubclient.post_message(live, "voice", "assistant", spoken)
                        continue
                    if interrupt.wrong_language(spoken):
                        log.warning("task report in the wrong language, not spoken: %s", spoken[:80])
                        spoken = f"{name} finished, but the report came out in the wrong language and was dropped."
                    activity.log_event("say", spoken)
                    log.info("said: %s", spoken)
                    hubclient.post_message(live, "voice", "assistant", spoken)
                    if speaking_on(live) and not vstate.is_muted() and not vstate.is_deaf():
                        vstate.write_state("speaking")
                        with say_lock:
                            speaker.say(spoken, live, interruptible=False)
                    settle()
            except Exception:
                log.exception("task watcher hiccup")
                time.sleep(4)

    def submit(text):
        activity.log_event("order", text)
        hq.put(text.strip())

    def responder():
        while True:
            text = hq.get()
            busy["on"] = True
            try:
                handle(text, live_settings())
            except Exception:
                log.exception("responder run failed")
            finally:
                busy["on"] = False
                with speech_lock:
                    queued, speech_queue[:] = speech_queue[:], []
                for held in queued:
                    hq.put(held)
                if REBOOT_FILE.exists():
                    REBOOT_FILE.unlink(missing_ok=True)
                    log.info("rebooting on his order: fresh code on wake")
                    activity.log_event("say", "rebooting: back in a moment")
                    log.info("said: rebooting: back in a moment")
                    vstate.write_state("rebooting")
                    time.sleep(0.5)
                    os._exit(0)

    def pstage(stage, detail=""):
        if not busy["on"]:
            vstate.write_state("deaf" if vstate.is_deaf() else stage, detail)

    def control(intent, live, source="voice"):
        """Voice and ears, on and off. No model, no synthesis, a cached line at most."""
        if intent == "on":
            intent = "listen" if vstate.is_deaf() else ("unmute" if vstate.is_muted() else "listen")
        elif intent == "off":
            intent = "deafen"
        log.info("control: %s (%s)", intent, source)
        if source in ("voice", "order"):
            # An order was acted upon: the barge-in ear stays open for a while,
            # but the next order still waits for his name.
            chat_state["engaged_until"] = time.monotonic() + engage_window
        activity.log_event("tool", f"control: {intent}")
        if intent == "mute":
            speaker.stop_now()
            vstate.set_muted(True)
            hubclient.set_setting(live, "speaking_mode", "off")
            hubclient.post_message(live, "voice", "assistant", "Silenced.")
        elif intent == "unmute":
            vstate.set_muted(False)
            hubclient.set_setting(live, "speaking_mode", "on")
            line = phrases.get(live, "control", 2)
            if line:
                speaker.play_wav(line[1])
        elif intent == "deafen":
            line = phrases.get(live, "control", 1)
            if line and not vstate.is_muted():
                speaker.play_wav(line[1])
            vstate.set_deaf(True)
            hubclient.set_setting(live, "speaking_mode", "off")
        elif intent == "listen":
            vstate.set_deaf(False)
            hubclient.set_setting(live, "speaking_mode", "on")
            line = phrases.get(live, "control", 0)
            if line and not vstate.is_muted():
                speaker.play_wav(line[1])
        settle()

    def processor():
        pending_text = ""
        pending_ts = 0.0
        live = live_settings()
        live_ts = time.monotonic()
        epoch_seen = deaf_epoch["n"]
        session_seen = 0.0
        while True:
            try:
                item = work.get()
                deaf_now = vstate.is_deaf()
                if deaf_epoch["n"] != epoch_seen:
                    epoch_seen = deaf_epoch["n"]
                    pending_text = ""
                # Deaf is a sentinel, not silence: only his name and a wake word get through.
                # Speaking mode off is the same sentinel: he is resting, not gone.
                if time.monotonic() - live_ts > 10:
                    live = live_settings()
                    live_ts = time.monotonic()
                sentinel = deaf_now or not speaking_on(live)
                if mic_owner():
                    settle()
                    continue
                if item is None:
                    now = time.monotonic()
                    if pending_text and now - pending_ts >= join_window:
                        text, pending_text = pending_text, ""
                        submit(text)
                    continue
                kind, audio, captured = item
                session_now = testing["active"] or training["active"]
                if session_now:
                    session_seen = time.monotonic()
                # A session owns the mic like dictation does: its words go nowhere,
                # not even the tail that lands after the session closed.
                if session_now or (session_seen and captured <= session_seen + 0.5):
                    log.info("a session has the mic, ignored")
                    pstage("listening")
                    continue
                duration = len(audio) / capture.sample_rate
                rms = float(np.sqrt(np.mean(np.square(audio))))
                if duration < float(cfg["min_speech_s"]) or rms < float(cfg["rms_floor"]):
                    log.info("drop: short or quiet (%.2fs, rms %.5f)", duration, rms)
                    pstage("listening")
                    continue
                if time.monotonic() - live_ts > 10:
                    live = live_settings()
                    live_ts = time.monotonic()
                ok, sim, speaker_name = voiceid.verify(audio, capture.sample_rate, live)
                if not ok:
                    log.warning("drop: not an enrolled voice (best %s at %.2f), %.2fs", speaker_name, sim, duration)
                    pstage("listening")
                    continue
                if not sentinel:
                    vstate.write_state("transcribing")
                try:
                    text = transcribe(audio, capture.sample_rate, cfg)
                except Exception as error:
                    log.error("stt failed: %s", error)
                    pstage("listening")
                    continue
                if stt.is_noise(text):
                    log.info("drop: noise (%.2fs)", duration)
                    pstage("listening")
                    continue
                wanted, named = intents.match(text)
                if sentinel:
                    if named and any(w in ("listen", "unmute") for w in wanted):
                        log.info("heard through deaf ears: %s", text[:60])
                        hubclient.set_setting(live, "speaking_mode", "on")
                        for intent in wanted:
                            control(intent, live)
                    else:
                        log.info("deaf, ignored, sounded like %s (%.2f): %s", speaker_name, sim, text[:60])
                    continue
                if kind == "barge":
                    cuts = [w for w in wanted if w in ("mute", "deafen")]
                    if cuts and named:
                        log.info("cut off by his word: %s", text[:60])
                        for intent in cuts:
                            control(intent, live)
                    else:
                        log.info("over his own voice, ignored: %s", text[:60])
                    continue
                if wanted and named:
                    log.info("heard from %s (%.2fs): %s", speaker_name, duration, text)
                    for intent in wanted:
                        control(intent, live)
                    continue
                log.info("heard from %s (%.2fs): %s", speaker_name, duration, text)
                rejected = interrupt.rejected_language(text)
                if rejected:
                    log.info("ignored language: %s, nothing acted upon", rejected)
                    pstage("listening")
                    continue
                now = time.monotonic()
                gate_times.append(now)
                while gate_times and now - gate_times[0] > 300:
                    gate_times.popleft()
                if len(gate_times) > limit:
                    log.warning("rate limit: dropping utterance")
                    continue
                if busy["on"]:
                    with speech_lock:
                        speech_queue.append(text)
                    log.info("queued until he is free: %s", text[:60])
                    pstage("listening")
                    continue
                if pending_text and now - pending_ts < join_window:
                    pending_text = f"{pending_text} {text}"
                    pending_ts = now
                    if pending_text[-1] in ".?!":
                        text, pending_text = pending_text, ""
                        submit(text)
                    else:
                        log.info("thought continues, waiting: %s", pending_text)
                        pstage("waiting", pending_text[-40:])
                    continue
                if pending_text:
                    text = f"{pending_text} {text}"
                    pending_text = ""
                if text[-1] in ".?!":
                    submit(text)
                else:
                    pending_text = text
                    pending_ts = now
                    log.info("thought unfinished, waiting: %s", text)
            except Exception:
                log.exception("processor step failed, recovering")
                healer.heal(traceback.format_exc(), cfg)
                time.sleep(0.5)

    def text_api():
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class SayHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                if self.path == "/v1/say":
                    try:
                        text = json.loads(self.rfile.read(length).decode()).get("text", "").strip()
                    except ValueError:
                        text = ""
                    if text:
                        log.info("text in: %s", text[:80])
                        submit(text)
                    self.respond({"ok": True})
                    return
                if self.path == "/v1/announce":
                    try:
                        text = json.loads(self.rfile.read(length).decode()).get("text", "").strip()
                    except ValueError:
                        text = ""
                    if text:
                        log.info("announce: %s", text[:80])
                        aq.put(text)
                    self.respond({"ok": True})
                    return
                if self.path == "/v1/train/start":
                    try:
                        body = json.loads(self.rfile.read(length).decode())
                        name = (body.get("name") or "").strip()
                        mode = (body.get("mode") or "").strip()
                        was_deaf = bool(body.get("was_deaf"))
                    except ValueError:
                        name, mode, was_deaf = "", "", False
                    if vstate.is_deaf():
                        self.respond({"ok": False, "error": "deaf: open your ears first, sir"})
                        return
                    ok = trainer.start(
                        live_settings(), speaker, capture, training,
                        name=name or None, mode=mode or None, was_deaf=was_deaf,
                    )
                    if ok:
                        vstate.write_state("deaf (training)", "starting")
                    log.info("voice training %s", "started" if ok else "refused: already running")
                    if ok:
                        activity.log_event("tool", "voice training started from the dashboard")
                    self.respond({"ok": ok} if ok else {"ok": False, "error": "already training"})
                    return
                if self.path == "/v1/test/start":
                    if vstate.is_deaf():
                        self.respond({"ok": False, "error": "deaf: open your ears first, sir"})
                        return
                    if training["active"]:
                        self.respond({"ok": False, "error": "training in progress"})
                        return
                    try:
                        was_deaf = bool(json.loads(self.rfile.read(length).decode()).get("was_deaf"))
                    except ValueError:
                        was_deaf = False
                    ok = tester.start(capture, testing, vad_path=cfg.get("silero_path"), was_deaf=was_deaf)
                    if ok:
                        vstate.write_state("deaf (testing)")
                        log.info("voice test started")
                    self.respond({"ok": ok} if ok else {"ok": False, "error": "already testing"})
                    return
                if self.path == "/v1/test/stop":
                    tester.stop(testing)
                    time.sleep(0.1)
                    settle()
                    log.info("voice test stopped")
                    self.respond({"ok": True})
                    return
                if self.path == "/v1/test/play":
                    try:
                        which = json.loads(self.rfile.read(length).decode()).get("what", "sample")
                    except ValueError:
                        which = "sample"
                    clip = phrases.get(live_settings(), which)
                    if not clip:
                        self.respond({"ok": False, "error": f"no cached {which} for this voice yet"})
                        return

                    def play():
                        testing["playing"] = which
                        try:
                            speaker.play_wav(clip[1], interruptible=False)
                        finally:
                            testing["playing"] = ""

                    threading.Thread(target=play, daemon=True).start()
                    self.respond({"ok": True, "seconds": round(tts_seconds(clip[1]), 1), "text": clip[0]})
                    return
                if self.path == "/v1/train/delete":
                    try:
                        body = json.loads(self.rfile.read(length).decode())
                        confirm = bool(body.get("confirm"))
                        name = (body.get("name") or "").strip() or None
                    except ValueError:
                        confirm, name = False, None
                    self.respond(trainer.delete_profile(confirm, training, name=name))
                    return
                self.send_response(404)
                self.end_headers()

            def do_GET(self):
                if self.path == "/v1/activity":
                    payload = json.dumps(activity.recent())
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload.encode())
                elif self.path == "/v1/test/state":
                    payload = json.dumps(tester.state(testing, cfg))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload.encode())
                elif self.path == "/v1/train/state":
                    payload = json.dumps(trainer.state(training))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload.encode())
                elif self.path == "/v1/level":
                    payload = json.dumps({
                        "level": getattr(capture, "level", 0.0),
                        "raw": getattr(capture, "raw", 0.0),
                        "sys": getattr(capture, "mon_level", 0.0),
                        "floor": capture.release_floor,
                        "ceiling": float(cfg.get("meter_ceiling", 0.05)),
                        "ts": time.time(),
                    })
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload.encode())
                else:
                    self.send_response(404)
                    self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 8757), SayHandler)
        server.request_queue_size = 64
        server.serve_forever()

    aq = queue.Queue()

    def announcer():
        """Speaks announcements, one at a time, through the same lock as his replies."""
        while True:
            text = aq.get()
            try:
                live = live_settings()
                if interrupt.wrong_language(text):
                    log.warning("wrong language, not spoken: %s", text[:80])
                    continue
                activity.log_event("say", text)
                log.info("said: %s", text)
                hubclient.post_message(live, "voice", "assistant", text)
                waited = 0
                while waited < 120 and (
                    capture.env > capture.release_floor
                    or capture.playing
                    or vstate.is_deaf()
                    or stt.is_dictating(cfg)
                    or mic_owner()
                ):
                    time.sleep(2)
                    waited += 2
                if vstate.is_muted() or vstate.is_deaf():
                    log.info("silent, text only: %s", text[:60])
                else:
                    vstate.write_state("speaking")
                    with say_lock:
                        speaker.say(text, live, interruptible=False)
                    settle()
            except Exception:
                log.exception("announcer hiccup")

    threading.Thread(target=text_api, daemon=True).start()
    def deaf_watch():
        was = vstate.is_deaf()
        capture.deaf = was
        mute_was = vstate.is_muted()
        while True:
            time.sleep(0.1)
            # Training counts as deaf: the send path drops everything, the taps still record.
            now = vstate.is_deaf() or bool(mic_owner())
            capture.deaf = now
            if now and not was:
                deaf_epoch["n"] += 1
                try:
                    while True:
                        capture.queue.get_nowait()
                except queue.Empty:
                    pass
                capture.reset()
                log.info("deafened: audio dies at the mic, nothing held")
            elif was and not now:
                log.info("deaf open: your voice reaches me again")
            mute_now = vstate.is_muted()
            if mute_now and not mute_was:
                if speaker.stop_now():
                    log.info("muted mid sentence: audio cut")
                log.info("muted: he speaks only in text")
            elif mute_was and not mute_now:
                log.info("unmuted: his voice returns")
            mute_was = mute_now
            was = now

    hq = queue.Queue()
    threading.Thread(target=responder, daemon=True).start()
    threading.Thread(target=announcer, daemon=True).start()
    threading.Thread(target=processor, daemon=True).start()
    threading.Thread(target=deaf_watch, daemon=True).start()
    threading.Thread(target=task_watch, daemon=True).start()
    try:
        for item in capture.utterances():
            if item is None:
                work.put(None)
            else:
                kind, audio = item
                work.put((kind, audio, time.monotonic()))
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        capture.stop()


if __name__ == "__main__":
    main()
