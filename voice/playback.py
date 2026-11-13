"""His voice, played in this process, so the voice knows when each sample reaches the speakers.

A player started as a separate process (pw-play) says nothing about when its sound starts: on
banditbox that took 67 to 330 ms from the spawn, the most when the sink had gone to sleep. Here
one PipeWire stream is kept open through a reply and its callback learns, for every buffer, the
time its first sample leaves the converter (PortAudio's outputBufferDacTime, which counts the
stream's and the device's latency). Each clip gets the moment its first sample is heard and the
moment its last one is, on time.monotonic and on the wall clock, so the mouth and the face run
on the playback clock instead of on when the audio arrived.

Measured with the interface's own mic hearing the speakers, the predicted time lands 0 to 5 ms
before the sound (tests/live_tts_sync.py).

The stream opens on prime(), which the speaker calls when it starts fetching a reply, so a
sleeping sink wakes during the fetch. It closes IDLE_S after the last clip, so the sink can go
back to sleep; once primed it waits up to PRIMED_S for the first clip, since a reply can take
that long to fetch. It plays as application.name "Diablo", the name the sound hold leaves alone.
"""

import collections
import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("diablo.playback")

RATE = 24000
IDLE_S = 4.0
PRIMED_S = 20.0
LATENCY_S = 0.04
APP = "Diablo"
_env_lock = threading.Lock()


class Unavailable(Exception):
    """The stream could not play a clip; the caller plays it another way."""


class Clip:
    """One clip queued on the stream. heard_at and ended_at are monotonic seconds, heard_wall
    the wall clock; each is set once the callback has placed that sample."""

    def __init__(self, samples, rate):
        self.samples = samples
        self.rate = rate
        self.pos = 0
        self.cut = False
        self.heard_at = None
        self.heard_wall = None
        self.ended_at = None
        self.started = threading.Event()
        self.finished = threading.Event()

    @property
    def seconds(self):
        return len(self.samples) / self.rate


def _open_stream(rate, callback):
    import sounddevice as sd

    props = '{ application.name = "%s" node.name = "diablo-voice" application.process.id = %d }' % (APP, os.getpid())
    with _env_lock:
        before = os.environ.get("PIPEWIRE_PROPS")
        os.environ["PIPEWIRE_PROPS"] = props
        try:
            stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", latency=LATENCY_S,
                                     callback=callback)
        finally:
            if before is None:
                os.environ.pop("PIPEWIRE_PROPS", None)
            else:
                os.environ["PIPEWIRE_PROPS"] = before
    stream.start()
    return stream


class Output:
    """The stream and its queue of clips. play() returns a Clip whose events say when it is
    heard and when it is over; cut() silences one from the next buffer on."""

    def __init__(self, opener=_open_stream, clock=time.monotonic, wall=time.time, idle_s=IDLE_S):
        self._opener = opener
        self._clock = clock
        self._wall = wall
        self._idle_s = idle_s
        self._lock = threading.Lock()
        self._queue = collections.deque()
        self._stream = None
        self._rate = None
        self._closer = None

    def prime(self, rate=RATE):
        """Opens the stream now, playing silence, so the device is awake when the clip comes. A
        stream already open stays as it is, whatever its rate, so a clip playing is never cut."""
        with self._lock:
            if self._stream is None:
                self._ensure(rate)
            if not self._queue:
                self._schedule_close(PRIMED_S)

    def play(self, samples, rate):
        clip = Clip(np.asarray(samples, dtype=np.float32), rate)
        with self._lock:
            self._ensure(rate)
            self._cancel_close()
            self._queue.append(clip)
        return clip

    def cut(self, clip):
        clip.cut = True

    def release(self):
        """The reply is over: close once nothing has played for IDLE_S."""
        with self._lock:
            self._schedule_close()

    def close(self):
        with self._lock:
            self._cancel_close()
            self._shut()

    def _ensure(self, rate):
        """Call with the lock held."""
        if self._stream is not None and self._rate == rate:
            return
        self._shut()
        self._stream = self._opener(rate, self._callback)
        self._rate = rate

    def _shut(self):
        """Call with the lock held. Anything still queued ends here."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("closing the voice stream failed")
        now = self._clock()
        while self._queue:
            clip = self._queue.popleft()
            clip.cut = True
            self._finish(clip, now)

    def _cancel_close(self):
        if self._closer is not None:
            self._closer.cancel()
            self._closer = None

    def _schedule_close(self, after=None):
        self._cancel_close()
        self._closer = threading.Timer(self._idle_s if after is None else after, self._close_if_idle)
        self._closer.daemon = True
        self._closer.start()

    def _close_if_idle(self):
        with self._lock:
            if self._closer is not threading.current_thread():
                return
            if self._queue:
                self._schedule_close()
                return
            self._closer = None
            self._shut()

    def _finish(self, clip, at):
        """A clip taken off the queue early. One that never played keeps heard_at None."""
        clip.ended_at = at
        clip.started.set()
        clip.finished.set()

    def _callback(self, out, frames, ti, status):
        now, wall = self._clock(), self._wall()
        lead = ti.outputBufferDacTime - ti.currentTime
        if not 0.0 <= lead < 1.0:
            lead = LATENCY_S
        out.fill(0)
        rate = self._rate or RATE
        filled = 0
        while filled < frames and self._queue:
            clip = self._queue[0]
            at = lead + filled / rate
            if clip.cut:
                self._queue.popleft()
                self._finish(clip, now + at)
                continue
            if clip.pos == 0:
                clip.heard_at, clip.heard_wall = now + at, wall + at
                clip.started.set()
            n = min(frames - filled, len(clip.samples) - clip.pos)
            out[filled:filled + n, 0] = clip.samples[clip.pos:clip.pos + n]
            clip.pos += n
            filled += n
            if clip.pos >= len(clip.samples):
                self._queue.popleft()
                clip.ended_at = now + lead + filled / rate
                clip.finished.set()
