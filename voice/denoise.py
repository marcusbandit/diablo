"""Noise removal for everything the ears hear, with RNNoise.

RNNoise is a small recurrent network trained to keep speech and drop everything else:
hiss from a high mic gain, fans, typing, a hand rubbing the mic. It runs on the mic stream
before the wake word model, the meter and speech to text, and on every saved recording
before training, so the model learns from exactly what it will hear.

Streaming keeps state between calls, so 20 ms pieces come out clean with no seams; the
output can lag the input by a frame or so, which the caller absorbs with a small FIFO.
"""

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("diablo.denoise")

RATE = 16000
MARKER = ".denoised"
# When the live mic first ran through the filter. Recordings older than this came off the
# raw mic and get cleaned once; newer ones are clean already and are never cleaned twice.
SINCE = Path.home() / ".local/share/diablo/wake/denoised-since"


def mark_start():
    """Called by the service when its mic runs through the filter."""
    if not SINCE.exists():
        SINCE.parent.mkdir(parents=True, exist_ok=True)
        SINCE.write_text(str(__import__("time").time()))


def started_at():
    try:
        return float(SINCE.read_text().strip())
    except (OSError, ValueError):
        return None


class Denoiser:
    """Stateful: feed float pieces in order, get float pieces of clean audio back."""

    def __init__(self, rate=RATE):
        from pyrnnoise import RNNoise

        self.engine = RNNoise(rate)

    def feed(self, audio):
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        out = [np.asarray(frame, dtype=np.float32).reshape(-1) / 32768.0
               for _, frame in self.engine.denoise_chunk(pcm)]
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


def clean(audio, rate=RATE):
    """A whole recording, cleaned, the same length as it came in."""
    if len(audio) == 0:
        return audio
    engine = Denoiser(rate)
    out = engine.feed(audio)
    # Flush: push silence through so the last frames come out, then cut to length.
    tail = engine.feed(np.zeros(rate // 10, dtype=np.float32))
    out = np.concatenate([out, tail])
    if len(out) < len(audio):
        out = np.pad(out, (0, len(audio) - len(out)))
    return out[: len(audio)].astype(np.float32)


def ensure_clean(folder, read, write):
    """Cleans every recording in a folder once, in place, for takes saved before the filter.

    New recordings come off the cleaned mic stream already; a marker file says the old
    ones have been done, so a folder is only ever processed once.
    """
    folder = Path(folder)
    if not folder.is_dir() or (folder / MARKER).exists():
        return 0
    since = started_at()
    done, failed = 0, 0
    for path in sorted(folder.glob("*.wav")):
        if since is not None and path.stat().st_mtime >= since:
            continue
        try:
            write(path, clean(read(path)))
            done += 1
        except Exception as error:
            failed += 1
            log.warning("could not clean %s: %s", path.name, error)
    # Only a folder that came through whole is marked; a failure is tried again next time.
    if not failed:
        (folder / MARKER).touch()
    return done
