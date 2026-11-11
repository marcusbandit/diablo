import logging
import threading
import time

import numpy as np

log = logging.getLogger("diablo.cancel")

FRAME = 512
TAPS = 4096
FLOOR = 0.15
RECAL_INTERVAL = 2.0


class Canceler:
    """Two-channel echo suppression from a passive sink monitor tap.

    Mic audio is cleaned by subtracting the magnitude spectrum of what the
    speakers are playing (monitor tap) from the mic spectrum, so anything the
    machine itself plays vanishes from the feed while room-foreign sound,
    meaning the human voice, survives. No system audio routing is touched.
    """

    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.lock = threading.Lock()
        self.mon_ring = np.zeros(TAPS, dtype=np.float32)
        self.mic_ring = np.zeros(TAPS, dtype=np.float32)
        self.delay = 0
        self.gain = 1.0
        self.calibrated = False
        self.last_cal = 0.0
        self.window = np.hanning(FRAME).astype(np.float32)

    def feed(self, monitor_chunk):
        with self.lock:
            self.mon_ring = np.roll(self.mon_ring, -len(monitor_chunk))
            self.mon_ring[-len(monitor_chunk):] = monitor_chunk

    def _recalibrate(self):
        mic = self.mic_ring
        energy = float(np.sqrt(np.mean(mic**2)))
        if energy < 0.0005:
            return
        best_lag = 0
        best_score = -1.0
        mic_mag = np.abs(np.fft.rfft(mic * np.hanning(TAPS)))
        for lag in range(0, TAPS - FRAME, 4):
            shifted = np.roll(self.mon_ring, -lag)
            mon_mag = np.abs(np.fft.rfft(shifted * np.hanning(TAPS)))
            score = float(np.dot(mic_mag, mon_mag))
            if score > best_score:
                best_score = score
                best_lag = lag
        self.delay = best_lag
        mon_energy = float(np.sqrt(np.mean((np.roll(self.mon_ring, -best_lag)) ** 2)))
        self.gain = max(0.5, min(4.0, energy / max(mon_energy, 1e-9)))
        self.calibrated = True
        self.last_cal = time.monotonic()
        log.debug("canceler recalibrated: delay %d samples, gain %.2f", self.delay, self.gain)

    def monitor_window(self, length=FRAME):
        """Monitor segment aligned to the newest mic samples, or None."""
        with self.lock:
            if not self.calibrated:
                return None
            d = self.delay
            if d > 0:
                seg = self.mon_ring[-(length + d):-d]
            else:
                seg = self.mon_ring[-length:]
            return seg.copy()

    def process(self, mic_chunk):
        with self.lock:
            self.mic_ring = np.roll(self.mic_ring, -len(mic_chunk))
            self.mic_ring[-len(mic_chunk):] = mic_chunk
            now = time.monotonic()
            if not self.calibrated or now - self.last_cal > RECAL_INTERVAL:
                self._recalibrate()
            if not self.calibrated:
                return mic_chunk, 0.0
            d = self.delay
            if d > 0:
                mon_frame = self.mon_ring[-(FRAME + d):-d]
            else:
                mon_frame = self.mon_ring[-FRAME:]
            mic_frame = self.mic_ring[-FRAME:]
            mag_mic = np.abs(np.fft.rfft(mic_frame * self.window))
            mag_mon = np.abs(np.fft.rfft(mon_frame * self.window))
            bin_gain = np.maximum(1.0 - self.gain * mag_mon / np.maximum(mag_mic, 1e-9), FLOOR)
            clean = np.fft.irfft(np.fft.rfft(mic_frame) * bin_gain, FRAME)
            explained = float(
                np.sum(np.minimum(mag_mic, self.gain * mag_mon * 1.5))
                / np.sum(mag_mic + 1e-12)
            )
            clean = np.nan_to_num(clean, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
            return clean, explained
