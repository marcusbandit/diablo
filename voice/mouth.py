"""Diablo's mouth while he speaks: which drawn mouth shows at each moment of a clip.

The timing is measured from the audio, not taken from the speech service. Cartesia does send
sound timestamps, but inside a word it only splits the time evenly, and even its word edges
miss the audio by 40 to 130 ms (measured 2026-11-11, issue #227). So the known text is aligned
against the clip itself with a phoneme CTC model (wav2vec2 trained on espeak sounds), which
finds where each sound starts. When the model is missing or the clip has no text (a cached
acknowledgement), the mouth follows the loudness of the clip instead: still in sync, only
three shapes.

A track is a list of (seconds from clip start, shape). While a clip plays, the track sits in
~/.local/state/diablo/mouth.json with the wall clock time the sound starts, and the faces
play it against their own clock.

The shapes are Marcus's drawings in assets/faces/mouths/<shape>/: rest (m b p, silence),
open (vowels), mid (s t k n and most consonants), fv, oo (w r oo), l (l th), ee (ee y), h.
"""
import io
import json
import logging
import os
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from . import state as vstate

log = logging.getLogger("diablo.mouth")

TRACK_FILE = vstate.STATE_DIR / "mouth.json"
MODEL = "facebook/wav2vec2-lv-60-espeak-cv-ft"
RETRY = 300            # seconds before a failed model load is tried again
LONGEST_SOUND = 0.22   # a sound with no next one closes after this, so pauses fall back to rest
FRAME = 0.04           # loudness fallback resolution

VOWELS = set("aɑæʌɔoɒɐəɛeɪɜᵻäɤ")
# The languages Marcus speaks, as espeak names them. Japanese is read through kana first, since
# espeak cannot read kanji.
LANGS = {"en": "en-us", "da": "da", "es": "es", "ja": "ja"}
_PUNCTUATION = ".,!?;:\"'()、。！？「」『』…"


def shape(phone):
    """One espeak/IPA sound to the drawn mouth that says it."""
    p = phone.replace("ː", "").replace("ˈ", "").replace("ˌ", "")
    if p in ("m", "b", "p", "β", ""):
        return "rest"
    if p in ("f", "v", "ʋ"):
        return "fv"
    if p in ("w", "u", "ʊ", "ɹ", "r", "ɚ", "ɝ", "ʊɹ", "y", "ʏ", "ø", "œ", "ɶ", "ʉ", "o̞"):
        return "oo"
    if p in ("l", "θ", "ð", "ɫ", "ʎ"):
        return "l"
    if p in ("j", "i", "ɯ", "ɯᵝ", "ɨ"):
        return "ee"
    if p == "h":
        return "h"
    if p[0] in VOWELS:
        return "open"
    return "mid"


def _pcm(wav_bytes):
    """Mono float samples and the rate, from WAV bytes. Streamed WAVs carry a bogus length,
    so the samples are read from what is actually there."""
    with wave.open(io.BytesIO(wav_bytes)) as handle:
        rate, width, channels = handle.getframerate(), handle.getsampwidth(), handle.getnchannels()
        handle.rewind()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError("only 16 bit audio")
    samples = np.frombuffer(raw[: len(raw) // (2 * channels) * 2 * channels], dtype="<i2").astype(np.float32) / 32768
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(1)
    return samples, rate


def seconds(wav_bytes):
    """The clip's length from its header, bounded by the bytes present (streamed WAVs claim a
    day of audio), without decoding the samples."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as handle:
            rate = handle.getframerate()
            frame = handle.getnchannels() * handle.getsampwidth()
            return min(handle.getnframes() / rate, len(wav_bytes) / (rate * frame))
    except (wave.Error, EOFError, ValueError, ZeroDivisionError):
        return 0.0


def by_loudness(wav_bytes):
    """Mouth from loudness alone: rest, mid or open every 40 ms. Always in sync, few shapes."""
    try:
        samples, rate = _pcm(wav_bytes)
    except (wave.Error, EOFError, ValueError):
        return []
    step = int(rate * FRAME)
    if step <= 0 or len(samples) < step:
        return []
    frames = samples[: len(samples) // step * step].reshape(-1, step)
    rms = np.sqrt((frames ** 2).mean(1))
    peak = np.percentile(rms, 95) or 1.0
    level = rms / peak
    track, last = [], None
    for i, v in enumerate(level):
        s = "open" if v > 0.55 else "mid" if v > 0.18 else "rest"
        if s != last:
            track.append((round(i * FRAME, 3), s))
            last = s
    return track


def _pretrained(cls):
    """From the local cache without a network call; the hub only when the cache has no copy.
    Transformers otherwise asks the hub for a safetensors copy in a thread of its own, even
    for a cached model."""
    os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")
    try:
        return cls.from_pretrained(MODEL, local_files_only=True)
    except OSError:
        log.info("%s is not cached yet, fetching it", MODEL)
        return cls.from_pretrained(MODEL)


class Aligner:
    """Finds where each sound of a known sentence starts in its audio. Loads the model once,
    on first use, on the GPU when there is one."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ready = None   # None: not tried yet, False: unavailable until _retry_at
        self._retry_at = 0.0

    def _load(self):
        """Call with the lock held. A failed load is tried again after RETRY seconds, so a
        busy GPU or an unreachable model hub at boot does not cost the mouth for good."""
        if self._ready or (self._ready is False and time.monotonic() < self._retry_at):
            return self._ready
        try:
            import torch
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

            started = time.monotonic()
            self._torch = torch
            self._proc = _pretrained(Wav2Vec2Processor)
            if not torch.cuda.is_available():
                # on the CPU a sentence takes most of a second, which a reply would wait for
                raise RuntimeError("no GPU for the mouth aligner")
            self._device = "cuda"
            self._model = _pretrained(Wav2Vec2ForCTC).eval().to(self._device)
            self._vocab = self._proc.tokenizer.get_vocab()
            self._blank = self._proc.tokenizer.pad_token_id
            with torch.inference_mode():  # the first pass on a GPU pays its warm-up, here and not in a reply
                self._model(torch.zeros(1, 16000, device=self._device))
            self._ready = True
            log.info("mouth aligner ready on %s in %.1f s", self._device, time.monotonic() - started)
        except Exception:
            log.exception("mouth aligner unavailable, mouths follow loudness")
            self._ready = False
            self._retry_at = time.monotonic() + RETRY
        return self._ready

    def _phone(self, phone):
        """espeak's spelling of a sound as the model's vocabulary has it, or None. Danish
        writes the stød as a mark in front of the vowel and uses a Greek epsilon."""
        phone = phone.replace("ε", "ɛ")
        if phone not in self._vocab and phone[:1] in ("?", "ʔ"):
            phone = phone[1:]
        return phone if phone in self._vocab else None

    def _targets(self, text, lang="en"):
        """The sounds to find, and for each the index of its word in text.split()."""
        ids, words = [], []
        espeak = LANGS.get(lang, "en-us")
        for index, word in enumerate(text.split()):
            word = word.strip(_PUNCTUATION)
            if not word:
                continue
            if lang == "ja":
                word = kana(word)
            for phone in self._proc.tokenizer.phonemize(word, phonemizer_lang=espeak).split():
                phone = self._phone(phone)
                if phone:
                    ids.append(self._vocab[phone])
                    words.append(index)
        return ids, words

    def _viterbi(self, logp, ids):
        """CTC forced alignment over the blank-interleaved targets: the frame each target first wins."""
        ext = [self._blank]
        for i in ids:
            ext += [i, self._blank]
        frames, states = logp.shape[0], len(ext)
        if frames < len(ids):
            return None
        neg = -1e30
        skip = np.array([s >= 2 and ext[s] != self._blank and ext[s] != ext[s - 2] for s in range(states)])
        score = np.full(states, neg)
        score[0], score[1] = logp[0, ext[0]], logp[0, ext[1]]
        back = np.zeros((frames, states), dtype=np.int8)
        emit = logp[:, ext]
        for t in range(1, frames):
            stay = score
            step = np.concatenate([[neg], score[:-1]])
            jump = np.where(skip, np.concatenate([[neg, neg], score[:-2]]), neg)
            options = np.stack([stay, step, jump])
            choice = options.argmax(0)
            score = options[choice, np.arange(states)] + emit[t]
            back[t] = choice
        if max(score[states - 1], score[states - 2]) < neg / 2:
            return None  # no path fits: too few frames for these sounds
        s = states - 1 if score[states - 1] > score[states - 2] else states - 2
        starts = {}
        for t in range(frames - 1, -1, -1):
            if s % 2 == 1:
                starts[s // 2] = t
            s -= int(back[t, s])
        return [starts.get(i) for i in range(len(ids))]

    def warm(self):
        with self._lock:
            self._load()

    def measure(self, text, wav_bytes, lang="en"):
        """(mouth cues, start of each word in text.split() or None), or None when the clip
        cannot be measured. `lang` is one of LANGS."""
        with self._lock:
            if not text or not self._load():
                return None
            try:
                samples, rate = _pcm(wav_bytes)
                at16 = np.interp(np.arange(0, len(samples), rate / 16000), np.arange(len(samples)), samples).astype(np.float32)
                ids, word_of = self._targets(text, lang)
                if not ids or len(at16) < 1600:
                    return None
                torch = self._torch
                with torch.inference_mode():
                    x = self._proc(at16, sampling_rate=16000, return_tensors="pt").input_values.to(self._device)
                    logp = torch.log_softmax(self._model(x).logits[0], -1).float().cpu().numpy()
                starts = self._viterbi(logp, ids)
                if starts is None:
                    return None
                per = len(at16) / 16000 / logp.shape[0]
                tokens = self._proc.tokenizer.convert_ids_to_tokens(ids)
                cues, last = [], None
                timed = [(f * per, shape(tok)) for f, tok in zip(starts, tokens) if f is not None]
                word_starts = [None] * len(text.split())
                for f, w in zip(starts, word_of):
                    if f is not None and word_starts[w] is None:
                        word_starts[w] = round(f * per, 3)
                for i, (at, s) in enumerate(timed):
                    following = timed[i + 1][0] if i + 1 < len(timed) else at + LONGEST_SOUND
                    if s != last:
                        cues.append((round(at, 3), s))
                        last = s
                    if following - at > LONGEST_SOUND:
                        cues.append((round(at + LONGEST_SOUND, 3), "rest"))
                        last = "rest"
                if last != "rest":
                    cues.append((round(timed[-1][0] + LONGEST_SOUND, 3), "rest"))
                return cues, word_starts
            except Exception:
                log.exception("aligning a clip failed, this one follows loudness")
                return None


_aligner = Aligner()


_kakasi = []


def kana(text):
    """Japanese text with its kanji read out in hiragana, which espeak can say. The reader
    loads its dictionaries once."""
    if not _kakasi:
        try:
            import pykakasi
        except ImportError:
            log.warning("pykakasi is missing: Japanese kanji cannot be read, the mouth follows loudness")
            raise
        _kakasi.append(pykakasi.kakasi())
    return "".join(part["hira"] for part in _kakasi[0].convert(text))


def spaced(text, lang):
    """Japanese runs its words together; a space after each 、 and 。 gives the clauses and the
    aligner something to count as words."""
    if lang != "ja":
        return text
    import re

    return re.sub(r"([、。！？])\s*", r"\1 ", text).strip()


def face_for(text, wav_bytes, looks=None, lang="en"):
    """The whole face track for a clip: {"mouth": cues, "eyes": cues}. `looks` is one eye
    look per clause (voice/eyes.py); without them the eyes stay neutral. `lang` is one of
    LANGS; Japanese text should have gone through spaced() first."""
    from . import eyes

    measured = _aligner.measure(text, wav_bytes, lang) if text else None
    mouth_cues, word_starts = measured if measured else (by_loudness(wav_bytes), None)
    duration = seconds(wav_bytes)
    looks = looks or [eyes.NEUTRAL] * len(eyes.clauses(text))
    return {"mouth": mouth_cues, "eyes": eyes.cues(text, looks, word_starts, duration) if text else []}


def warm():
    """Loads the model in the background so the first reply does not wait for it. Returns the
    thread, so a caller can wait for it to finish."""
    thread = threading.Thread(target=_aligner.warm, name="mouth-warm", daemon=True)
    thread.start()
    return thread


def _write(payload):
    try:
        TRACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile("w", dir=TRACK_FILE.parent, delete=False, prefix=".mouth")
        handle.write(json.dumps(payload))
        handle.close()
        os.replace(handle.name, TRACK_FILE)
    except OSError:
        pass


def playing(track, duration, latency=0.0):
    """Called the moment the player starts: the faces play `track` from now plus the player's
    start delay. `track` is a face track from face_for, or a bare list of mouth cues."""
    face = track if isinstance(track, dict) else {"mouth": track or [], "eyes": []}
    _write({"start": round(time.time() + latency, 3), "duration": round(duration, 3),
            "cues": face.get("mouth") or [], "eyes": face.get("eyes") or []})


def stopped():
    _write({"start": 0, "duration": 0, "cues": [], "eyes": []})
