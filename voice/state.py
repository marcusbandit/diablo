import json
import os
import tempfile
import time
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.local/state/diablo"))
STATE_FILE = STATE_DIR / "voice-state.json"
MUTE_FILE = STATE_DIR / "muted"
DEAF_FILE = STATE_DIR / "deaf"


_last = ["listening"]


def write_state(stage, detail=""):
    _last[0] = stage
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"state": stage, "detail": detail, "ts": time.time()})
        handle = tempfile.NamedTemporaryFile(
            "w", dir=STATE_DIR, delete=False, prefix=".voice-state"
        )
        handle.write(payload)
        handle.close()
        os.replace(handle.name, STATE_FILE)
    except OSError:
        pass


def refresh():
    if _last[0].startswith("deaf"):
        _last[0] = "listening"
    write_state(_last[0])


def is_muted():
    try:
        return MUTE_FILE.read_text().strip() in ("1", "true", "yes")
    except OSError:
        return False


def set_muted(flag):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if flag:
            MUTE_FILE.write_text("1\n")
        else:
            MUTE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def is_deaf():
    try:
        return DEAF_FILE.read_text().strip() in ("1", "true", "yes")
    except OSError:
        return False


def set_deaf(flag):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if flag:
            DEAF_FILE.write_text("1\n")
            write_state("deaf")
        else:
            DEAF_FILE.unlink(missing_ok=True)
            if _last[0] == "deaf":
                _last[0] = "listening"
                write_state("listening")
    except OSError:
        pass
