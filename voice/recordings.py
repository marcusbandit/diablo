"""Every turn's recording, kept so a transcript can be checked against what he really said.

The ears save the audio of each request (from just after the name to the end of the turn)
with what speech to text made of it. `diablo-ears recordings` lists them and
`diablo-ears recheck [id]` runs one through gvoice again, his normal dictation, and prints both.
"""

import json
import time
from pathlib import Path

from . import wake

DIR = Path.home() / ".local" / "share" / "diablo" / "turns"
KEEP = 200


def keep(audio, text, kind="wake", early=False):
    """Saves one request's audio and its transcript. Returns the id, or None."""
    clip = str(int(time.time() * 1000))
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        wake.write_wav(DIR / f"{clip}.wav", audio)
        (DIR / f"{clip}.json").write_text(json.dumps({
            "id": clip, "at": time.time(), "kind": kind, "text": text, "early": bool(early),
            "seconds": round(len(audio) / wake.RATE, 2)}))
        for old in sorted(DIR.glob("*.wav"))[:-KEEP]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
    except (OSError, ValueError):
        return None
    return clip


def listed(last=10):
    rows = []
    for meta in sorted(DIR.glob("*.json"))[-last:]:
        try:
            rows.append(json.loads(meta.read_text()))
        except (OSError, ValueError):
            continue
    return rows


def path(clip):
    """The wav for an id, or for "last"."""
    if clip in (None, "", "last"):
        found = sorted(DIR.glob("*.wav"))
        return found[-1] if found else None
    target = DIR / f"{clip}.wav"
    return target if target.exists() else None
