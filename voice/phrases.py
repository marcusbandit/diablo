"""Pre-recorded acknowledgements, cached per voice.

When Diablo hands work up to Raphael he says one of these lines. They never
change, so paying the speech API for them every time is waste: each line is
fetched once per voice and kept on disk. A new voice id on the hub gets its own
folder, so switching voices regenerates the set instead of playing the old one.
"""

import json
import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path.home() / ".local/share/diablo/phrases"

ACKS = [
    "One moment.",
    "I'll be right on that, sir.",
    "Consider it underway.",
    "Leave it with me.",
    "Already moving on it.",
    "Give me a moment, I will look.",
    "At once, sir.",
    "I shall see to it.",
    "On it. I will report back.",
    "A moment, and you shall have it, sir.",
]

# The training cue. One clip, so its length is known before a round starts.
COUNTDOWN = ["Three, two, one."]

# About ten seconds of him for the voice test. Measured at fourteen characters a
# second with the archie voice, so this sits a little over ten.
SAMPLE = [
    "Allow me a moment of your attention. I am Diablo, and this household runs on my word. "
    "The lights, the doors, the machines that hum through the night, all answer to me."
]

# Spoken when a quick skill just worked. No generation, no synthesis, no doubt.
DONE = [
    "Right away, sir.",
    "Consider it done.",
    "It is already so.",
    "Naturally.",
    "Done, before you finished asking.",
    "As you wish.",
    "Of course. It is handled.",
    "Your will, made real.",
    "Done. Was there ever any doubt?",
    "Handled, with pleasure.",
]

# Spoken on the control words. Index matters: listen, deafen, unmute.
CONTROL = ["Listening.", "I will not hear another word.", "My voice returns."]

# A photo or a screenshot just landed in Pictures.
CAMERA = [
    "Captured, and rather flattering.",
    "Taken. It is in your pictures.",
    "One image, kept for posterity.",
]

# The pizza page is open in his browser. He orders, Diablo holds the door.
PIZZA = [
    "The pizza house awaits your order, sir.",
    "Dinner is a click away. I have opened the page.",
    "Your usual haunt is on the screen. Choose well.",
]

# An app, a page or a search just opened.
OPEN = [
    "Opening it now.",
    "It is on your screen.",
    "There, before you.",
]

BANKS = {
    "ack": ACKS,
    "countdown": COUNTDOWN,
    "sample": SAMPLE,
    "done": DONE,
    "control": CONTROL,
    "camera": CAMERA,
    "pizza": PIZZA,
    "open": OPEN,
}

WATCH_SECONDS = 60


def bank_dir(voice_id):
    return ROOT / voice_id


def _file(voice_id, index, bank="ack"):
    return bank_dir(voice_id) / f"{bank}-{index:02d}.wav"


def _enabled(cfg):
    voice_id = str(cfg.get("tts_voice_id") or "").strip()
    provider = str(cfg.get("tts_provider") or "none")
    return voice_id if voice_id and provider != "none" else ""


def ensure(cfg, speaker, log=None):
    """Fetch any missing line for the current voice. Returns how many were fetched."""
    voice_id = _enabled(cfg)
    if not voice_id:
        return 0
    folder = bank_dir(voice_id)
    folder.mkdir(parents=True, exist_ok=True)
    fetched = 0
    for bank, lines in BANKS.items():
        for index, text in enumerate(lines):
            target = _file(voice_id, index, bank)
            if target.exists():
                continue
            try:
                wav = speaker._cloud_wav(text, cfg)
            except (httpx.HTTPError, OSError, ValueError):
                continue
            if not wav:
                continue
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(wav)
            os.replace(tmp, target)
            fetched += 1
    manifest = {
        "voice_id": voice_id,
        "tts_model": cfg.get("tts_model"),
        "name": cfg.get("tts_voice_name") or "",
        "lines": ACKS,
        "banks": BANKS,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = folder / "voice.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, folder / "voice.json")
    if fetched and log:
        log.info("cached %d acknowledgements for voice %s", fetched, voice_id)
    return fetched


def pick(cfg, bank="ack"):
    """A random cached line for the current voice, as (text, wav bytes), or None."""
    voice_id = _enabled(cfg)
    if not voice_id:
        return None
    lines = BANKS.get(bank, [])
    ready = [
        (text, _file(voice_id, i, bank))
        for i, text in enumerate(lines)
        if _file(voice_id, i, bank).exists()
    ]
    if not ready:
        return None
    text, path = random.choice(ready)
    try:
        return text, path.read_bytes()
    except OSError:
        return None


def get(cfg, bank, index=0):
    """One specific cached line, as (text, wav bytes), or None. No network."""
    voice_id = _enabled(cfg)
    lines = BANKS.get(bank, [])
    if not voice_id or index >= len(lines):
        return None
    path = _file(voice_id, index, bank)
    try:
        return lines[index], path.read_bytes()
    except OSError:
        return None


def watch(live_settings_fn, speaker, log, sleep=time.sleep, stop=None):
    """Keep the bank complete for whatever voice the hub currently names.

    Meant for a daemon thread. Re-reads live settings every minute so a voice
    change regenerates the set without a restart. `stop` is an optional Event
    for tests.
    """
    while True:
        try:
            ensure(live_settings_fn(), speaker, log)
        except Exception:
            log.exception("phrase bank refresh failed")
        if stop is not None and stop.is_set():
            return
        sleep(WATCH_SECONDS)


def start(live_settings_fn, speaker, log):
    thread = threading.Thread(target=watch, args=(live_settings_fn, speaker, log), daemon=True)
    thread.start()
    return thread
