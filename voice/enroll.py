"""Enroll the master's voice: record a few passes and blend them into the profile.

Run inside the diablo tmux session or detached. Speaks its progress on stdout.
Read anything aloud, naturally, for each pass: a paragraph, a rant, a story.
"""

import time

import numpy as np
import sounddevice as sd

from . import voiceid
from .config import load_config

PASSES = 3
SECONDS = 8


def main():
    cfg = load_config()
    rate = int(cfg["sample_rate"])
    for i in range(PASSES):
        print(f"pass {i + 1}/{PASSES}: speak for {SECONDS} seconds...", flush=True)
        audio = sd.rec(SECONDS * rate, samplerate=rate, channels=1, dtype="float32")
        sd.wait()
        mono = audio[:, 0]
        rms = float(np.sqrt(np.mean(np.square(mono))))
        if rms < 0.002:
            print(f"pass {i + 1} too quiet (rms {rms:.5f}), redo it", flush=True)
            continue
        emb = voiceid.enroll(mono, rate)
        print(f"pass {i + 1} enrolled (rms {rms:.5f})", flush=True)
        time.sleep(0.5)
    if voiceid.profile_exists():
        profile = np.load(voiceid.PROFILE)
        print(f"profile saved: {profile['n']} samples at {voiceid.PROFILE}", flush=True)
    else:
        print("no profile: every pass was silent", flush=True)


if __name__ == "__main__":
    main()
