import io
import logging
import queue
import random
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import httpx

from . import chime, eyes, hubclient, interrupt, mouth, playback
from . import state as vstate

log = logging.getLogger("diablo.tts")


def _wav_seconds_bytes(wav_bytes):
    """Seconds of audio in a WAV, bounded by the bytes actually present.

    Streamed WAVs (Cartesia) carry a placeholder size of 0xFFFFFFFF in the
    header, which the wave module reads as a day of audio. The byte count is
    the truth about what was received, so the header may only shorten it.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes)) as handle:
            rate = handle.getframerate()
            frame_bytes = handle.getnchannels() * handle.getsampwidth()
            claimed = handle.getnframes() / rate
            present = len(wav_bytes) / (rate * frame_bytes)
            return min(claimed, present)
    except (OSError, EOFError, ValueError, ZeroDivisionError, wave.Error):
        return 0.0


def _wav_seconds(path):
    try:
        with wave.open(str(path)) as handle:
            return handle.getnframes() / handle.getframerate()
    except (OSError, EOFError):
        return 0.0


# Silence he leaves between one utterance ending and the next beginning.
# Varied so back to back replies breathe like speech instead of a tape deck.
GAP_SECONDS = (1.5, 2.5)

# Jev tags the eyes while the speech is fetched and is usually back first (about 0.3 s against
# 0.5 s). Once the audio is in, he waits at most this much longer, then speaks with neutral eyes.
EYES_WAIT = 0.3


def _spawn_player(path):
    return subprocess.Popen(
        [*chime.PLAYER, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


class Speaker:
    """Fetches speech and plays it one utterance at a time.

    Every caller goes through `say`, which holds the turn lock from the moment
    playback starts until the clip's measured length has passed and the player
    has exited. The next utterance then waits a further random gap before it
    starts, so he can never talk over himself, whichever thread asked.
    """

    def __init__(self, cfg, capture, clock=time.monotonic, sleep=time.sleep, gap=None, spawn=None, hold=None,
                 output=None):
        self.capture = capture
        self.cfg = cfg
        self._holder = hold
        self.speaking = False
        self._turn = threading.Lock()
        self._clear_at = 0.0
        self._clock = clock
        self._sleep = sleep
        self._gap = gap or (lambda: random.uniform(*GAP_SECONDS))
        self._spawn = spawn or _spawn_player
        self._output = output if output is not None else (None if spawn else playback.Output())
        # Breath between sentences of one reply; the turn gap is for separate replies.
        self.pause = 0.25
        self._killed = threading.Event()

    def stop_now(self):
        """Kills whatever is playing this instant. Nothing happens when he is silent."""
        if self.speaking:
            self._killed.set()
            return True
        return False

    def _cloud_wav(self, text, cfg):
        provider = cfg.get("tts_provider") or "none"
        if provider == "cartesia":
            response = httpx.post(
                "https://api.cartesia.ai/tts/bytes",
                headers={
                    "X-API-Key": cfg.get("tts_api_key", ""),
                    "Cartesia-Version": "2026-08-14",
                },
                json={
                    "model_id": cfg.get("tts_model") or "sonic-latest",
                    "transcript": text,
                    "voice": cfg.get("tts_voice_id") or "",
                    "speed": float(cfg.get("speech_rate") or 1.0)
                    if str(cfg.get("utterance_language") or "en").startswith("en")
                    else 1.0,
                    "output_format": {
                        "container": "wav",
                        "encoding": "pcm_s16le",
                        "sample_rate": 24000,
                    },
                },
                timeout=60,
            )
            hubclient.report_usage(cfg, "cartesia", cfg.get("tts_model") or "sonic-latest", "characters", len(text),
                                   "desk", ok=response.is_success)
            response.raise_for_status()
            return response.content
        if provider == "openai":
            response = httpx.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {cfg.get('tts_api_key', '')}"},
                json={"model": "tts-1", "input": text, "voice": "onyx", "response_format": "wav"},
                timeout=60,
            )
            response.raise_for_status()
            return response.content
        return None

    def _piper_wav(self, text, cfg):
        piper_dir = Path(cfg["piper_dir"])
        binary = piper_dir / "piper"
        model = piper_dir / "voice.onnx"
        if not binary.exists() or not model.exists():
            return None
        out = piper_dir / "out.wav"
        try:
            subprocess.run(
                [str(binary), "--model", str(model), "--output_file", str(out)],
                input=text.encode(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=True,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        return out.read_bytes()

    def _wait_for_turn(self):
        """Blocks until the gap after the previous utterance has passed."""
        remaining = self._clear_at - self._clock()
        while remaining > 0:
            self._sleep(min(remaining, 0.05))
            remaining = self._clear_at - self._clock()

    def _latency(self):
        """Seconds from starting the player to the first sound reaching the speakers, so the
        mouth opens with the sound and not before it (setting mouth_latency_ms)."""
        try:
            return float((self.cfg or {}).get("mouth_latency_ms", 60)) / 1000
        except (TypeError, ValueError):
            return 0.06

    def prime(self):
        """A reply is coming: wake the output now, while the speech is fetched."""
        if self._output is None:
            return
        try:
            self._output.prime()
        except Exception:
            log.exception("could not open the voice stream ahead of the reply")

    def _cut(self, interruptible):
        return self._killed.is_set() or (interruptible and self.capture.interrupted.is_set())

    def _until(self, at, interruptible=None):
        """Sleeps until the monotonic time `at`. False when a cut came first."""
        while True:
            if interruptible is not None and self._cut(interruptible):
                return False
            remaining = at - self._clock()
            if remaining <= 0:
                return True
            self._sleep(min(remaining, 0.005))

    def _play(self, wav_bytes, interruptible, duration, track=None, on_heard=None):
        if self._output is not None:
            try:
                return self._play_out(wav_bytes, interruptible, duration, track, on_heard)
            except playback.Unavailable:
                log.exception("the voice stream failed, playing this clip through pw-play")
        return self._play_spawned(wav_bytes, interruptible, duration, track, on_heard)

    def _play_out(self, wav_bytes, interruptible, duration, track, on_heard):
        """Plays on the stream and follows its clock: the mouth is anchored to the moment the
        first sample is heard, on_heard runs at that moment, and this returns once the last
        sample (or the cut) has been heard."""
        try:
            samples, rate = mouth._pcm(wav_bytes)
            clip = self._output.play(samples, rate)
        except Exception as error:
            raise playback.Unavailable(str(error)) from error
        asked = self._clock()
        while not clip.started.wait(0.02):
            if self._cut(interruptible):
                self._output.cut(clip)
                clip.finished.wait(1.0)
                return "interrupted", 0.0, duration
            if self._clock() - asked > 2.0:
                self._output.close()
                raise playback.Unavailable("the voice stream never asked for audio")
        if clip.heard_at is None:
            return "interrupted", 0.0, duration
        mouth.playing(track, clip.seconds, at=clip.heard_wall)
        state = "done"
        if on_heard is not None and self._until(clip.heard_at, interruptible):
            try:
                on_heard()
            except Exception:
                log.exception("the start of speech hook failed")
        while not clip.finished.wait(0.02):
            if self._clock() > clip.heard_at + clip.seconds + 2.0:
                self._output.close()
                break
            if self._cut(interruptible):
                self._output.cut(clip)
                clip.finished.wait(1.0)
                state = "interrupted"
                break
        if clip.ended_at is not None:
            self._until(clip.ended_at)
        if clip.cut:
            state = "interrupted"
        heard_at = clip.heard_at if clip.heard_at is not None else asked
        return state, max(0.0, (clip.ended_at or self._clock()) - heard_at), duration

    def _play_spawned(self, wav_bytes, interruptible, duration, track=None, on_heard=None):
        with tempfile.NamedTemporaryFile(suffix=".wav", dir="/tmp/opencode", delete=False) as handle:
            handle.write(wav_bytes)
            path = handle.name
        try:
            proc = self._spawn(path)
            started = self._clock()
            mouth.playing(track, duration, self._latency())
            if on_heard is not None:
                on_heard()
            # Hold for the clip's full measured length even if the player exits
            # early; the length is the truth about how long he is talking.
            while proc.poll() is None or self._clock() - started < duration:
                if self._cut(interruptible):
                    proc.kill()
                    proc.wait()
                    return "interrupted", self._clock() - started, duration
                self._sleep(0.04)
            return "done", self._clock() - started, duration
        finally:
            try:
                Path(path).unlink()
            except OSError:
                pass

    def _fetch(self, text, cfg):
        """Text to WAV bytes, cloud first, piper second. None when nothing can speak."""
        text = interrupt.scrub(text)
        if not text:
            return None
        try:
            wav = self._cloud_wav(text, cfg)
        except (httpx.HTTPError, OSError, ValueError):
            wav = None
        if wav is None:
            wav = self._piper_wav(text, cfg)
        return wav

    def _enter_hold(self, cfg):
        """Holds other sound for the reply, by the speech_hold setting. None when off."""
        try:
            from . import speakers

            cfg = cfg or self.cfg
            mode = speakers.speech_mode(cfg)
            if mode == "off":
                return None
            held = speakers.Hold(mode=mode, dim_to=speakers.speech_dim_to(cfg),
                                 gap=speakers.speech_gap(cfg), holder=self._holder)
            return held.__enter__()
        except Exception:
            log.exception("holding other sound failed, speaking anyway")
            return None

    def _speak(self, wavs, interruptible, cfg=None, on_heard=None, on_done=None):
        """Plays WAV byte strings back to back inside one turn.

        The mic is muted from the first clip to the last, a short pause sits
        between clips, and the turn gap is charged once at the end. Other sound
        is held from the first clip until the last has played, failed or been
        cut off (speakers.SoundHold, setting speech_hold).

        on_heard runs when the first sample of the first clip reaches the speakers, on_done when
        the last one has, before the mouth is cleared.
        """
        state, elapsed, duration = "unspoken", 0.0, 0.0
        held = None
        with self._turn:
            self._wait_for_turn()
            self._killed.clear()
            began = False
            try:
                for item in wavs:
                    wav, track = item if isinstance(item, tuple) else (item, None)
                    if not wav:
                        continue
                    if self._killed.is_set() or vstate.is_muted():
                        break
                    if began:
                        self._sleep(self.pause)
                    else:
                        self.capture.begin_playback()
                        self.speaking = True
                        began = True
                        held = self._enter_hold(cfg)
                    clip = _wav_seconds_bytes(wav) or len(wav) / 48000.0
                    if track is None:
                        track = mouth.by_loudness(wav)
                    state, took, clip = self._play(wav, interruptible, clip, track, on_heard)
                    on_heard = None
                    elapsed += took
                    duration += clip
                    if state == "interrupted":
                        break
            finally:
                if began and on_done is not None:
                    try:
                        on_done()
                    except Exception:
                        log.exception("the end of speech hook failed")
                if self._output is not None:
                    self._output.release()
                if began:
                    # cleared once the whole reply is over: between its sentences the track
                    # has simply run out, which shows the resting mouth
                    mouth.stopped()
                    self.speaking = False
                    self.capture.end_playback()
                    self._clear_at = self._clock() + self._gap()
                if held is not None:
                    try:
                        held.__exit__(None, None, None)
                    except Exception:
                        log.exception("giving back the sound failed")
        return (state, elapsed, duration)

    def say(self, text, cfg, interruptible=True, on_heard=None, on_done=None):
        from . import state as vstate
        if vstate.is_deaf():
            return ("unspoken", 0.0, 0.0)
        self.cfg = cfg
        self.prime()
        spoken = interrupt.scrub(text)
        looks = eyes.tag_soon(spoken)  # beside the speech fetch, so the reply does not wait for it
        wav = self._fetch(text, cfg)
        if wav is None:
            return ("unspoken", 0.0, 0.0)
        return self._speak([(wav, mouth.face_for(spoken, wav, looks(EYES_WAIT)))], interruptible, cfg,
                           on_heard=on_heard, on_done=on_done)

    def play_wav(self, wav, interruptible=False):
        """Plays audio already in hand, such as a cached acknowledgement."""
        from . import state as vstate
        if vstate.is_deaf() or not wav:
            return ("unspoken", 0.0, 0.0)
        return self._speak([wav], interruptible)

    def say_stream(self, parts, cfg, interruptible=False):
        """Speaks sentences as they arrive from a generator, one turn for all of them.

        Fetching runs ahead of playback in its own thread, so the second
        sentence's audio is already in hand when the first one ends.
        """
        from . import state as vstate
        if vstate.is_deaf():
            for _ in parts:
                pass
            return ("unspoken", 0.0, 0.0)
        self.cfg = cfg
        self.prime()
        ready = queue.Queue()

        def fetch_ahead():
            try:
                for text in parts:
                    if self._killed.is_set():
                        break
                    spoken = interrupt.scrub(text)
                    looks = eyes.tag_soon(spoken)
                    wav = self._fetch(text, cfg)
                    if wav is not None:
                        # Measured here, ahead of playback, while the previous sentence plays.
                        wait = 0.0 if self._killed.is_set() else EYES_WAIT
                        ready.put((wav, mouth.face_for(spoken, wav, looks(wait))))
            except Exception:
                log.exception("streamed fetch failed")
            finally:
                ready.put(None)

        threading.Thread(target=fetch_ahead, daemon=True).start()

        def wavs():
            while True:
                wav = ready.get()
                if wav is None:
                    return
                yield wav

        return self._speak(wavs(), interruptible, cfg)
