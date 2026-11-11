"""Voiceprint gate: only the master's voice drives him.

A d-vector encoder turns each utterance into an embedding. Utterances whose
similarity to the enrolled profile falls below the threshold are dropped
before transcription: his own playback, the television, other people.
Profiles are named: ~/.local/share/diablo/voices/<name>.npz, and the one
under watch is named in the voice-active file.
"""

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

log = logging.getLogger("diablo.voiceid")

BASE = Path.home() / ".local/share/diablo"
LEGACY = BASE / "voice-profile.npz"
PROFILES = BASE / "voices"
ACTIVE = BASE / "voice-active"
_encoder = None
_failed_at = 0.0
_pool = None
_pool_lock = threading.Lock()


def migrate():
    """Move the single legacy profile into the named scheme, once, and backfill history."""
    PROFILES.mkdir(parents=True, exist_ok=True)
    if LEGACY.exists() and not (PROFILES / "default.npz").exists():
        LEGACY.rename(PROFILES / "default.npz")
    for path in PROFILES.glob("*.npz"):
        if not history_path(path.stem).exists():
            backfill(path.stem)
    if not ACTIVE.exists():
        first = sorted(PROFILES.glob("*.npz"))
        if first:
            set_active(first[0].stem)


def active_name():
    try:
        name = ACTIVE.read_text().strip()
    except OSError:
        name = ""
    return name or "default"


def set_active(name):
    ACTIVE.write_text(name)


def profile_path(name=None):
    name = name or active_name()
    path = PROFILES / f"{name}.npz"
    if not path.exists() and name == "default" and LEGACY.exists():
        return LEGACY
    return path


def profiles():
    return sorted(p.stem for p in PROFILES.glob("*.npz")) if PROFILES.exists() else []


def history_path(name=None):
    return PROFILES / f"{name or active_name()}.json"


def history(name=None):
    """Every training session of a voice, oldest first. Empty when there is none."""
    try:
        doc = json.loads(history_path(name).read_text())
        sessions = doc.get("sessions")
        if isinstance(sessions, list):
            return {"sessions": sessions}
    except (OSError, ValueError, AttributeError):
        pass
    return {"sessions": []}


def backfill(name):
    """A voice trained before history existed gets one session: its blend count, dated by the file.

    The blend caps at eight, so this is a floor on what was recorded, never the full count.
    """
    path = profile_path(name)
    n = _samples(name)
    if n <= 0 or not path.exists():
        return
    at = datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat()
    _write_history(name, {"sessions": [{"at": at, "rounds": n, "seconds": n * 8, "backfill": True}]})


def _write_history(name, doc):
    path = history_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, prefix=f".{name}")
    try:
        json.dump(doc, handle, indent=1)
        handle.close()
        os.replace(handle.name, path)
    except OSError:
        handle.close()
        os.unlink(handle.name)
        raise


def record_session(name, rounds, seconds_each):
    """Append one finished session to the voice's history, written atomically."""
    doc = history(name)
    doc["sessions"].append({
        "at": datetime.now().replace(microsecond=0).isoformat(),
        "rounds": int(rounds),
        "seconds": int(rounds) * int(seconds_each),
    })
    _write_history(name, doc)
    return doc


def _samples(name):
    path = profile_path(name)
    if not path.exists():
        return 0
    try:
        return int(np.load(path)["n"])
    except (OSError, ValueError, KeyError):
        return 0


def stats(name=None):
    """Samples in the blend, recordings and sessions ever, and when the last one was."""
    sessions = history(name)["sessions"]
    return {
        "samples": _samples(name),
        "recordings": sum(int(s.get("rounds", 0)) for s in sessions),
        "sessions": len(sessions),
        "last": sessions[-1].get("at") if sessions else None,
    }


def all_stats():
    return {name: stats(name) for name in profiles()}


def rename(old, new):
    """Move a profile and its history to a new name; the active pointer follows."""
    src = PROFILES / f"{old}.npz"
    if not src.exists():
        return False
    try:
        src.rename(PROFILES / f"{new}.npz")
        old_history = PROFILES / f"{old}.json"
        if old_history.exists():
            old_history.rename(PROFILES / f"{new}.json")
    except OSError as error:
        log.warning("rename %s to %s failed: %s", old, new, error)
        return False
    if active_name() == old:
        set_active(new)
    return True


def _load_encoder():
    global _encoder, _failed_at
    if _encoder is not None:
        return _encoder
    if _failed_at and time.monotonic() - _failed_at < 3600:
        return None
    try:
        from resemblyzer import VoiceEncoder

        _encoder = _worker().submit(VoiceEncoder, "cpu").result()
        log.info("voiceprint encoder ready")
    except Exception as error:
        _failed_at = time.monotonic()
        log.warning("voiceprint unavailable, gate passes everyone: %s", error)
        return None
    return _encoder


def _one_thread():
    import torch

    torch.set_num_threads(1)


def _worker():
    """The one thread every embed runs on. Torch pays a warm-up of about 12 s on a busy box
    the first time each OS thread embeds, and on six threads a 1.6 s embed took 1 to 8 s
    instead of 20 ms. The thread count it sets holds for the whole process; the other torch
    work in the ears, the mouth aligner, runs on the GPU."""
    global _pool
    with _pool_lock:
        if _pool is None:
            from concurrent.futures import ThreadPoolExecutor

            _pool = ThreadPoolExecutor(1, thread_name_prefix="voiceprint", initializer=_one_thread)
        return _pool


LIVE_TIMEOUT_S = 3.0


def embed(wav, timeout=None, **kwargs):
    """encoder.embed_utterance on the voiceprint thread. Call after _load_encoder(). Raises
    TimeoutError when the thread is held up longer than `timeout`."""
    return _worker().submit(_encoder.embed_utterance, wav, **kwargs).result(timeout=timeout)


def warm():
    """Loads the encoder and runs one embed, which pays a warm-up of about 2 s, so the first
    wake after a start does not. Returns the seconds it took, or None without an encoder."""
    started = time.monotonic()
    encoder = _load_encoder()
    if encoder is None:
        return None
    try:
        from resemblyzer import preprocess_wav

        noise = (np.random.default_rng(0).standard_normal(32000) * 0.05).astype(np.float32)
        preprocess_wav(noise, source_sr=16000)
        embed(noise)
    except Exception as error:
        log.warning("voiceprint warm-up failed: %s", error)
    return time.monotonic() - started


def profile_exists(name=None):
    return profile_path(name).exists()


def gate_on(cfg):
    """The voiceprint toggle, honest about strings from the hub settings."""
    value = cfg.get("voiceprint", True)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def verify(audio, sample_rate, cfg):
    """Return (passed, similarity, speaker). Passes when any enrolled voice
    matches; passes everything when unenrolled or off."""
    if not gate_on(cfg):
        return True, 1.0, "anyone (gate off)"
    names = profiles()
    if not names:
        return True, 1.0, "anyone (no profiles)"
    encoder = _load_encoder()
    if encoder is None:
        return True, 1.0, "anyone (no encoder)"
    try:
        from resemblyzer import preprocess_wav

        wav = preprocess_wav(audio.astype(np.float32), source_sr=sample_rate)
        emb = embed(wav)
    except Exception as error:
        log.warning("voiceprint embed failed, passing: %s", error)
        return True, 1.0, "anyone (embed failed)"
    minimum = float(cfg.get("voiceprint_min", 0.70))
    best, best_name = -1.0, "a stranger"
    for name in names:
        path = profile_path(name)
        if not path.exists():
            continue
        profile = np.load(path)
        sim = float(np.dot(profile["emb"], emb) / (np.linalg.norm(profile["emb"]) * np.linalg.norm(emb) + 1e-9))
        if sim > best:
            best, best_name = sim, name
    return best >= minimum, best, best_name


def scores(audio, sample_rate):
    """Cosine similarity of one clip against every enrolled voice, as {name: score}.

    Empty when there is nothing to compare against or the encoder is down.
    """
    names = profiles()
    encoder = _load_encoder() if names else None
    if encoder is None:
        return {}
    try:
        from resemblyzer import preprocess_wav

        wav = preprocess_wav(audio.astype(np.float32), source_sr=sample_rate)
        emb = embed(wav)
    except Exception as error:
        log.warning("voiceprint embed failed: %s", error)
        return {}
    out = {}
    for name in names:
        path = profile_path(name)
        if not path.exists():
            continue
        ref = np.load(path)["emb"]
        out[name] = float(np.dot(ref, emb) / (np.linalg.norm(ref) * np.linalg.norm(emb) + 1e-9))
    return out


def delete(name=None):
    """Remove a named profile; if it was the watched one, the gate opens."""
    path = profile_path(name)
    existed = path.exists()
    try:
        path.unlink(missing_ok=True)
        history_path(name).unlink(missing_ok=True)
    except OSError as error:
        return {"ok": False, "error": str(error)}
    if name is None or name == active_name():
        try:
            ACTIVE.unlink()
        except OSError:
            pass
    return {"ok": True, "deleted": existed, "name": name or active_name()}


def enroll(audio, sample_rate, max_samples=8, name=None):
    """Blend a new recording into the named profile. Returns the embedding."""
    from resemblyzer import preprocess_wav

    encoder = _load_encoder()
    if encoder is None:
        raise RuntimeError("encoder unavailable")
    wav = preprocess_wav(audio.astype(np.float32), source_sr=sample_rate)
    emb = embed(wav)
    path = profile_path(name)
    if path.exists():
        profile = np.load(path)
        old = profile["emb"]
        n = int(profile["n"])
        blended = (old * n + emb) / (n + 1)
        blended /= np.linalg.norm(blended) + 1e-9
        n = min(n + 1, max_samples)
    else:
        blended, n = emb / (np.linalg.norm(emb) + 1e-9), 1
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, emb=blended.astype(np.float32), n=n)
    set_active(path.stem)
    return blended
