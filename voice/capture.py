import collections
import logging
import queue
import subprocess
import threading
import time

import numpy as np
import sounddevice as sd

from . import cancel as cancel_mod
from . import state as vstate

CHUNK = 512

log = logging.getLogger("diablo.capture")


class Capture:
    def __init__(self, cfg, vad, pause_fn=None, engaged_fn=None):
        self.cfg = cfg
        self.vad = vad
        self.pause_fn = pause_fn
        self.engaged_fn = engaged_fn
        self.voice_floor = float(cfg.get("voice_rms_floor", 0.0018))
        self.env_decay = float(cfg.get("env_decay", 0.97))
        self.release_floor = float(cfg.get("release_floor", 0.004))
        self.env = 0.0
        self.idle_s = float(cfg.get("idle_yield_s", 0.5))
        self.sample_rate = int(cfg["sample_rate"])
        self.queue = queue.SimpleQueue()
        self.taps = []
        self.deaf = False
        self._was_deaf = False
        self.playing = False
        self.interrupted = threading.Event()
        self.canceler = cancel_mod.Canceler(self.sample_rate)
        self.monitor_active = False
        self.monitor_proc = None
        self.level = 0.0
        self.raw = 0.0
        self.mon_level = 0.0
        self._paused = False
        self._was_paused = False
        self._reset = threading.Event()
        self._running = True
        self.device = self._pick_device(cfg.get("mic_device", ""))
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=CHUNK,
            device=self.device,
            callback=self._callback,
        )
        self._open_monitor()

    def _pick_device(self, wanted):
        candidates = []
        for index, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0 or "monitor" in dev["name"].lower():
                continue
            hostapi = sd.query_hostapis(dev["hostapi"])["name"]
            if "pipewire" not in hostapi.lower():
                continue
            candidates.append((index, dev["name"]))
        if wanted:
            for index, name in candidates:
                if wanted.lower() in name.lower():
                    return index
        default = sd.default.device[0]
        if any(index == default for index, _ in candidates):
            return default
        return candidates[0][0] if candidates else default

    def _open_monitor(self):
        try:
            sink = subprocess.run(
                ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            target = f"{sink}.monitor"
            self.monitor_proc = subprocess.Popen(
                [
                    "pw-record", "--raw", "--format", "f32",
                    "--rate", str(self.sample_rate), "--channels", "1",
                    "--target", target, "-",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            self.monitor_active = True
            threading.Thread(target=self._monitor_reader, daemon=True).start()
            log.info("monitor tap open on %s, cancellation active", target)
        except Exception as error:
            log.warning("monitor tap failed, running without cancellation: %s", error)
            self.monitor_active = False

    def _monitor_reader(self):
        proc = self.monitor_proc
        while self._running and proc.poll() is None:
            data = proc.stdout.read(CHUNK * 4)
            if not data:
                break
            samples = np.frombuffer(data, np.float32).copy()
            if self.deaf:
                self.mon_level = 0.0
                continue
            rms = float(np.sqrt(np.mean(np.square(samples))))
            self.mon_level = max(rms, self.mon_level * 0.995)
            self.canceler.feed(samples)

    def _callback(self, indata, frames, time_info, status):
        mic = indata[:, 0].copy()
        if self.monitor_active:
            cleaned, explained = self.canceler.process(mic)
        else:
            cleaned, explained = mic, 0.0
        chunk_rms = float(np.sqrt(np.mean(np.square(cleaned))))
        self.env = max(chunk_rms, self.env * self.env_decay)
        # Deaf and playing still feed the loop: deaf so the wake word can reach
        # him, playing so a control word can cut him off. The loop drops the
        # rest. The meter goes dark so the dashboard shows he is not listening.
        quiet_meter = self.deaf or self.playing
        self.level = 0.0 if quiet_meter else self.env
        self.raw = 0.0 if quiet_meter else chunk_rms
        self.queue.put((cleaned, explained))
        for tap in self.taps:
            tap.put((cleaned, explained))

    def reset(self):
        self._reset.set()

    def begin_playback(self):
        self.interrupted.clear()
        self.playing = True

    def end_playback(self):
        self.playing = False
        self.interrupted.clear()
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass
        self._reset.set()

    def start(self):
        self.stream.start()

    def stop(self):
        self._running = False

    def device_name(self):
        return sd.query_devices(self.device)["name"]

    def utterances(self):
        tail_s = float(self.cfg["silence_tail_s"])
        tail_chunks = max(1, int(tail_s * self.sample_rate / CHUNK))
        max_chunks = int(120 * self.sample_rate / CHUNK)
        ring = collections.deque(maxlen=12)
        voiced = []
        speech_run = 0
        silence_run = 0
        in_speech = False
        env_below = 0
        barge = []
        barge_speech = 0
        barge_silence = 0
        over_playback = False
        max_barge = int(8 * self.sample_rate / CHUNK)
        watch = collections.deque(maxlen=48)
        high_run = 0
        check = 0
        echo = 0.0
        last_emit = time.monotonic()
        while self._running:
            try:
                chunk, explained = self.queue.get(timeout=self.idle_s)
            except queue.Empty:
                yield None
                continue
            echo = 0.6 * echo + 0.4 * explained
            if time.monotonic() - last_emit > 0.5:
                last_emit = time.monotonic()
                yield None
            if self._reset.is_set():
                self._reset.clear()
                self.vad.reset()
                ring.clear()
                voiced = []
                speech_run = 0
                silence_run = 0
                in_speech = False
                env_below = 0
            # The dashboard reads the state file; write it on the transition, not on the 15 s heartbeat.
            if self.deaf != getattr(self, "_was_deaf", False):
                self._was_deaf = self.deaf
                if self.deaf:
                    vstate.write_state("deaf")
                else:
                    vstate.refresh()
            if self.pause_fn is not None:
                check += 1
                if self._paused or check % 31 == 0:
                    self._paused = self.pause_fn()
                    if self._paused:
                        if not self._was_paused:
                            log.info("dictation active: going deaf")
                            vstate.write_state("deaf (dictation)")
                        self._was_paused = True
                        self.vad.reset()
                        ring.clear()
                        voiced = []
                        speech_run = 0
                        silence_run = 0
                        in_speech = False
                        env_below = 0
                        try:
                            while True:
                                self.queue.get_nowait()
                        except queue.Empty:
                            pass
                        continue
                    if self._was_paused:
                        log.info("dictation over: listening again")
                        self._was_paused = False
                        vstate.refresh()
            # While he speaks, the ear stays open through the echo-cancelled
            # monitor: sustained real speech stops the playback mid-sentence
            # and what was heard comes out as a barge utterance to handle.
            if self.playing:
                if not self.monitor_active:
                    continue
                prob = self.vad.prob(chunk)
                watch.append(chunk)
                chunk_rms = float(np.sqrt(np.mean(np.square(chunk))))
                engaged = self.engaged_fn is None or self.engaged_fn()
                if prob > 0.85 and chunk_rms > self.voice_floor and echo < 0.65 and engaged:
                    high_run += 1
                else:
                    high_run = 0
                if high_run >= 8:
                    audio = np.concatenate(list(watch))
                    watch.clear()
                    high_run = 0
                    self.playing = False
                    self.interrupted.set()
                    log.info("barge-in detected, stopping playback")
                    last_emit = time.monotonic()
                    yield ("barge", audio)
                continue
            if barge:
                barge.clear()
                barge_speech = 0
                barge_silence = 0
            prob = self.vad.prob(chunk)
            chunk_rms = float(np.sqrt(np.mean(np.square(chunk))))
            voiced_chunk = (
                prob > 0.5 and chunk_rms > self.voice_floor and echo < 0.65
            )
            engaged = self.engaged_fn is None or self.engaged_fn()
            if not in_speech:
                ring.append(chunk)
                if voiced_chunk:
                    speech_run += 1
                    if speech_run >= 3:
                        in_speech = True
                        over_playback = self.playing
                        voiced = list(ring)
                        silence_run = 0
                else:
                    speech_run = 0
            else:
                voiced.append(chunk)
                if len(voiced) >= max_chunks:
                    last_emit = time.monotonic()
                    yield ("barge" if over_playback else "utt", np.concatenate(voiced))
                    self.vad.reset()
                    voiced = []
                    in_speech = False
                    speech_run = 0
                    silence_run = 0
                    env_below = 0
                    ring.clear()
                    continue
                if self.env < self.release_floor:
                    env_below += 1
                else:
                    env_below = 0
                if env_below >= 15:
                    last_emit = time.monotonic()
                    yield ("barge" if over_playback else "utt", np.concatenate(voiced))
                    self.vad.reset()
                    voiced = []
                    in_speech = False
                    speech_run = 0
                    silence_run = 0
                    env_below = 0
                    ring.clear()
                else:
                    silence_run = 0
