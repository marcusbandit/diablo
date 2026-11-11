"""The two sounds of a voice turn: one when he hears his name, one when he stops listening.

Both are soft bell tones made here from sine partials, so nothing is downloaded and the
pitch can be tuned in code. Listening rises a fifth, done falls back down it, which is the
same up-then-down shape phones use for "mic on" and "mic off". Never is a short low note
for a wake that heard nothing.
"""

import subprocess
import wave
from pathlib import Path

import numpy as np

RATE = 48000
DIR = Path.home() / ".local/share/diablo/chimes"
# Bump when a sound changes so the cached files are written again.
VERSION = 6

# (frequency in Hz, start in seconds) per note. A5 and E6, a perfect fifth.
SOUNDS = {
    "listen": [(880.0, 0.0), (1318.5, 0.085)],
    "done": [(1318.5, 0.0), (880.0, 0.085)],
    "never": [(659.3, 0.0)],
    # The setup's cue to speak: 90 ms, so the take starts before Marcus does.
    "tick": [(1760.0, 0.0)],
    # A thing he asked for is done: two quick rising notes, shorter than the listening chime.
    "ok": [(1318.5, 0.0), (1760.0, 0.055)],
    # Walked back (undo, go back, reopen): the same two notes falling, a fourth lower.
    "back": [(1318.5, 0.0), (987.8, 0.055)],
    # Speech control: three rising notes going on, the same three falling going off.
    "mode_on": [(659.3, 0.0), (880.0, 0.075), (1318.5, 0.15)],
    "mode_off": [(1318.5, 0.0), (880.0, 0.075), (659.3, 0.15)],
    # Dictation: a lower, softer pair, D5 and A5, so it never sounds like a command.
    "dictate_on": [(587.3, 0.0), (880.0, 0.09)],
    "dictate_off": [(880.0, 0.0), (587.3, 0.09), (1174.7, 0.2)],
    # Each dictated piece landed: one very quiet tap, there to be felt more than heard.
    "piece": [(1174.7, 0.0)],
    # An answer went to the background: a soft low lift, "hm", and he can talk again.
    "thinking": [(523.3, 0.0), (659.3, 0.11)],
}
SHORT = {"tick": (0.09, 40.0), "ok": (0.16, 22.0), "back": (0.16, 22.0), "piece": (0.07, 50.0),
         "mode_on": (0.3, 12.0), "mode_off": (0.3, 12.0), "thinking": (0.3, 14.0),
         "dictate_on": (0.32, 12.0), "dictate_off": (0.32, 12.0)}
# Sounds quieter than the rest.
LEVELS = {"piece": 0.09, "thinking": 0.14, "dictate_on": 0.17, "dictate_off": 0.17}
LEVEL = 0.22


def _note(freq, seconds=0.55, decay=7.0):
    t = np.arange(int(seconds * RATE)) / RATE
    # A bell: the fundamental plus two quieter partials that die faster.
    tone = (
        np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)
        + 0.32 * np.sin(2 * np.pi * freq * 2.0 * t) * np.exp(-t * 12.0)
        + 0.12 * np.sin(2 * np.pi * freq * 3.01 * t) * np.exp(-t * 18.0)
    )
    attack = np.minimum(t / 0.006, 1.0)
    return tone * attack


def render(name):
    """The named sound as float samples at RATE."""
    notes = SOUNDS[name]
    seconds, decay = SHORT.get(name, (0.55, 7.0))
    length = int((max(start for _, start in notes) + seconds + 0.05) * RATE)
    out = np.zeros(length)
    for freq, start in notes:
        note = _note(freq, seconds, decay)
        begin = int(start * RATE)
        out[begin:begin + len(note)] += note[: length - begin]
    out /= np.max(np.abs(out)) + 1e-9
    return out * LEVELS.get(name, LEVEL)


def seconds(name):
    return max(start for _, start in SOUNDS[name]) + SHORT.get(name, (0.55, 7.0))[0] + 0.05


def path(name):
    target = DIR / f"{name}-v{VERSION}.wav"
    if not target.exists():
        DIR.mkdir(parents=True, exist_ok=True)
        pcm = (np.clip(render(name), -1, 1) * 32767).astype("<i2")
        tmp = target.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes(pcm.tobytes())
        tmp.replace(target)
    return target


# Every sound Diablo plays carries this app name. The sound hold leaves it alone, and
# PipeWire remembers its volume apart from any other pw-play: a chime dimmed by the hold once
# left "pw-play" saved at 0.000064 and every later chime and reply played silent (2026-11-11).
APP = "Diablo"
PLAYER = ["pw-play", "-P", '{ application.name = "%s" }' % APP]


def play(name, wait=False):
    """Plays a sound through PipeWire. Returns the player so a caller can wait on it."""
    proc = subprocess.Popen(
        [*PLAYER, str(path(name))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if wait:
        proc.wait()
    return proc
