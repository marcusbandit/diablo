"""Marcus's dictations, as training data for the ears.

gvoice keeps the audio of every dictation in ~/.local/share/gvoice/clips and the text in
confidence.jsonl. That is hours of his own voice on this mic, which is worth two things:

- His talk is the negative that matters most. A model that has only heard "Diablo" from
  this mic learns "this mic"; one that has heard nine hours of him saying everything else
  learns the word.
- A voiceprint averaged over hours of his speech is far steadier than one from forty
  half second takes of one word.

Each clip is imported once: cleaned with RNNoise like the live mic, then kept as wake
features and voiceprint pieces under ~/.local/share/diablo/wake/dictation. The audio stays
where gvoice put it. A clip is left out of training when it says Diablo, holds too little
speech, or does not sound like him (a video playing, someone else in the room).
"""

import json
import logging
import re
import time
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np

log = logging.getLogger("diablo.dictation")

GVOICE = Path.home() / ".local/share/gvoice"
CLIPS = GVOICE / "clips"
TEXTS = GVOICE / "confidence.jsonl"
STORE = Path.home() / ".local/share/diablo/wake/dictation"
INDEX = STORE / "index.json"
VERSION = 1

# Whisper's spellings of the name, and the near ones that should not be trained as "not him".
NAME = re.compile(r"\b(dia+blo'?s?|diabolo|dia blo|diavolo)\b", re.I)
MIN_VOICED_S = 1.0
# A clip whose voice sits this far under his average is someone else, or him mixed with
# something loud. On his first 902 clips 95% sat at 0.73 or more; under 0.70 was mostly
# noise, a lone short word or him over other sound.
OWN_VOICE = 0.70
# Every n-th clip is never trained on and counts false wakes on his talk instead.
HELD_EVERY = 5


def _start(path):
    try:
        return datetime.strptime(path.stem, "%Y%m%d-%H%M%S").timestamp()
    except ValueError:
        return None


def transcripts(files):
    """Clip name to its text, by time: a clip owns the lines written before the next began."""
    starts = [(_start(p), p.name) for p in files]
    starts = sorted((s, n) for s, n in starts if s is not None)
    texts = {n: [] for _, n in starts}
    try:
        lines = [json.loads(line) for line in TEXTS.read_text().splitlines() if line.strip()]
    except OSError:
        return {n: "" for n in texts}
    at = [s for s, _ in starts]
    for line in lines:
        i = int(np.searchsorted(at, line.get("at", 0), side="right")) - 1
        text = (line.get("text") or "").strip()
        if i >= 0 and text and text not in texts[starts[i][1]]:
            texts[starts[i][1]].append(text)
    return {n: " ".join(t) for n, t in texts.items()}


def held(name):
    """Stable across runs, so a clip is always either trained on or checked against."""
    return zlib.crc32(name.encode()) % HELD_EVERY == 0


def load_index():
    try:
        index = json.loads(INDEX.read_text())
        return index if index.get("version") == VERSION else {"version": VERSION, "clips": {}}
    except (OSError, ValueError):
        return {"version": VERSION, "clips": {}}


def _save_index(index):
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=1))
    tmp.replace(INDEX)


def import_new(progress=print, workers=None):
    """Imports every clip not seen yet, several at once. Returns the index.

    RNNoise runs at about seven times real time on one core, so hours of dictation take
    an hour alone; spread over the cores it is minutes.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from multiprocessing import get_context

    STORE.mkdir(parents=True, exist_ok=True)
    index = load_index()
    files = sorted(CLIPS.glob("*.wav"))
    # A clip from the last two minutes may still be recording, and its text still coming:
    # it waits for the next training.
    settled = time.time() - 120
    new = [p for p in files if p.name not in index["clips"] and p.stat().st_mtime < settled]
    if not new:
        return index
    progress(f"taking in {len(new)} new dictations")
    texts = transcripts(files)
    # Each worker holds one to two gigabytes (torch, onnxruntime, a long clip), so four at
    # most: eight ran out of an 8 GB cap.
    workers = workers or max(1, min(4, (os.cpu_count() or 2) - 2))
    workers = min(workers, len(new))
    # spawn, not fork: torch and onnxruntime do not survive a fork once loaded. A worker
    # grows with every clip (RNNoise and onnxruntime keep what they allocated), so a fresh
    # pool takes each batch instead of letting one grow until the cap kills it.
    batch = workers * 8
    for first in range(0, len(new), batch):
        with ProcessPoolExecutor(workers, mp_context=get_context("spawn"), initializer=_worker_start) as pool:
            jobs = {pool.submit(_take_in, str(p), texts.get(p.name, "")): p.name for p in new[first: first + batch]}
            for job in as_completed(jobs):
                name = jobs[job]
                try:
                    index["clips"][name] = job.result()
                except Exception as error:
                    # Not written down, so it is tried again next time: a worker killed for
                    # memory takes every clip still queued with it, and those are fine.
                    log.warning("dictation %s not taken in: %s", name, error)
        _save_index(index)
        progress(f"  {min(first + batch, len(new))}/{len(new)} dictations")
    _rate(index)
    _save_index(index)
    return index


def double_check(index, progress=print, model=None, floor=0.1, peaks=5):
    """Listens again wherever a wake model hears something like the name.

    The text in confidence.jsonl comes from gvoice's live pieces and misses words: on the
    first scan two clips held out as "not the name" said "Diablo" plainly. A clip trained
    as a negative while it says the name teaches him to ignore his own name, so each
    usable clip is scored with a model that never trained on it, and the 3 s around its
    highest moments go back through whisper. A name heard there takes the clip out.
    """
    import tempfile

    import onnxruntime as ort

    from . import stt, wake
    from .config import load_config

    model = Path(model or wake.MODEL)
    if not model.exists():
        return index
    todo = [(n, e) for n, e in sorted(index["clips"].items()) if usable(e) and not e.get("checked")]
    if not todo:
        return index
    progress(f"listening again to {len(todo)} dictations for the name")
    cfg = load_config()
    session = ort.InferenceSession(str(model))
    feed = session.get_inputs()[0].name
    tmp = Path("/tmp/opencode")
    tmp.mkdir(exist_ok=True)
    for k, (name, entry) in enumerate(todo):
        path = STORE / Path(name).with_suffix(".wake.npy").name
        if path.exists():
            frames = np.load(path).astype(np.float32)
            scores = (session.run(None, {feed: np.ascontiguousarray(wake.stream_windows(frames))})[0].reshape(-1)
                      if len(frames) >= wake.FRAMES else np.zeros(0))
            audio = None
            for i in _peaks(scores, floor, peaks):
                if audio is None:
                    try:
                        audio = wake.read_wav(CLIPS / name)
                    except OSError as error:
                        log.warning("dictation %s gone: %s", name, error)
                        break
                end = int((i + wake.FRAMES + 8) * 0.08 * wake.RATE)
                piece = audio[max(0, end - int(3.2 * wake.RATE)): end]
                with tempfile.NamedTemporaryFile(suffix=".wav", dir=tmp, delete=False) as handle:
                    cut = Path(handle.name)
                try:
                    stt.write_wav(piece, wake.RATE, str(cut))
                    heard = stt.transcribe_file(str(cut), cfg)
                except Exception as error:
                    log.warning("could not listen again to %s: %s", name, error)
                    break
                finally:
                    cut.unlink(missing_ok=True)
                if NAME.search(heard):
                    entry["name"], entry["heard"] = True, heard
                    break
            else:
                entry["checked"] = True
        else:
            entry["checked"] = True  # under 2 s: never a wake window, never trained on
        if k % 100 == 99:
            _save_index(index)
            progress(f"  {k + 1}/{len(todo)} listened to again")
    _save_index(index)
    return index


def _peaks(scores, floor, most):
    """Frames of the highest separate moments over floor, at least 2 s apart."""
    out = []
    for i in np.argsort(scores)[::-1]:
        if scores[i] < floor or len(out) >= most:
            break
        if all(abs(int(i) - j) > 25 for j in out):
            out.append(int(i))
    return out


def _worker_start():
    import torch

    torch.set_num_threads(1)


def _take_in(path, text):
    """One clip: cleaned like the live mic, then its wake features and voiceprint pieces saved."""
    from . import wake
    from .denoise import clean

    path = Path(path)
    audio = clean(wake.read_wav(path))
    entry = {"text": text, "seconds": round(len(audio) / wake.RATE, 2), "name": bool(NAME.search(text))}
    parts, voiced = _voice_parts(audio)
    entry["voiced"] = round(voiced, 2)
    stem = STORE / path.stem
    if parts is not None:
        np.save(stem.with_suffix(".voice.npy"), parts.astype(np.float16))
    if len(audio) >= wake.RATE * 2:
        np.save(stem.with_suffix(".wake.npy"), _wake_frames(audio).astype(np.float16))
    return entry


PIECE_S = 30


def _wake_frames(audio):
    """Wake features of a clip, 30 s at a time and joined.

    In one go, an eleven minute dictation takes several gigabytes inside onnxruntime and
    the worker is killed. A seam costs a window or two that straddle two pieces, which
    are still his talk.
    """
    from . import wake

    piece = PIECE_S * wake.RATE
    out = []
    for start in range(0, len(audio), piece):
        chunk = audio[start: start + piece]
        if len(chunk) < wake.RATE * 2:
            break
        out.append(wake.features().embed_clips(wake.to_int16(chunk)[None], batch_size=1)[0])
    return np.concatenate(out)


def _voice_parts(audio):
    """resemblyzer's 1.6 s pieces of the speech in a clip, the same length the live check scores."""
    from resemblyzer import preprocess_wav

    from . import voiceid

    encoder = voiceid._load_encoder()
    wav = preprocess_wav(audio.astype(np.float32), source_sr=16000)
    voiced = len(wav) / 16000
    if encoder is None or voiced < 0.8:
        return None, voiced
    _, parts, _ = voiceid.embed(wav, return_partials=True)
    return parts, voiced


def _rate(index):
    """How much each clip sounds like him, against the average of all his clips."""
    names, means = [], []
    for name, entry in index["clips"].items():
        path = STORE / Path(name).with_suffix(".voice.npy").name
        if path.exists():
            names.append(name)
            parts = np.load(path).astype(np.float32)
            means.append(parts.mean(axis=0) / (np.linalg.norm(parts.mean(axis=0)) + 1e-9))
    if len(means) < 5:
        return
    means = np.stack(means)
    centre = _robust_centre(means)
    for name, sim in zip(names, means @ centre):
        index["clips"][name]["voice"] = round(float(sim), 3)


def _robust_centre(vectors, keep=80):
    centre = vectors.mean(axis=0)
    for _ in range(3):
        centre /= np.linalg.norm(centre) + 1e-9
        sims = vectors @ centre
        centre = vectors[sims >= np.percentile(sims, 100 - keep)].mean(axis=0)
    return centre / (np.linalg.norm(centre) + 1e-9)


def usable(entry):
    # No text at all is left out too: whisper heard nothing it could write, so nobody can
    # tell whether the name was in it.
    return ("error" not in entry and not entry.get("name") and entry.get("voiced", 0) >= MIN_VOICED_S
            and entry.get("text", "").strip() != "" and entry.get("voice", 0) >= OWN_VOICE)


def summary(index=None):
    """Counts for the page and the metrics: what was taken in and why the rest was not."""
    index = index or load_index()
    clips = index["clips"].values()
    out = {"clips": len(index["clips"]), "hours": round(sum(e.get("seconds", 0) for e in clips) / 3600, 2),
           "says_name": sum(1 for e in clips if e.get("name")),
           "little_speech": sum(1 for e in clips if not e.get("name") and (e.get("voiced", 0) < MIN_VOICED_S
                                                                             or not e.get("text", "").strip())),
           "other_voice": sum(1 for e in clips if not e.get("name") and e.get("voiced", 0) >= MIN_VOICED_S
                              and e.get("text", "").strip() and e.get("voice", 1) < OWN_VOICE),
           "broken": sum(1 for e in clips if "error" in e)}
    good = [e for e in clips if usable(e)]
    out["used"] = len(good)
    out["used_hours"] = round(sum(e["seconds"] for e in good) / 3600, 2)
    return out


def wake_streams(index=None, names=False):
    """(train, held) lists of (T, 96) feature streams from the usable clips.

    With names, also the clip name of each held stream, in the same order."""
    index = index or load_index()
    train, check, check_names = [], [], []
    for name, entry in sorted(index["clips"].items()):
        path = STORE / Path(name).with_suffix(".wake.npy").name
        if usable(entry) and path.exists():
            stream = np.load(path).astype(np.float32)
            if held(name):
                check.append(stream)
                check_names.append(name)
            else:
                train.append(stream)
    return (train, check, check_names) if names else (train, check)


def voice_parts(index=None, held_out=False, max_parts=20000):
    """His 1.6 s voiceprint pieces from usable clips, trained on or held out, at most max_parts."""
    index = index or load_index()
    parts = []
    for name, entry in sorted(index["clips"].items()):
        path = STORE / Path(name).with_suffix(".voice.npy").name
        if usable(entry) and held(name) == held_out and path.exists():
            parts.append(np.load(path).astype(np.float32))
    if not parts:
        return np.zeros((0, 256), dtype=np.float32)
    parts = np.concatenate(parts)
    if len(parts) > max_parts:
        parts = parts[np.random.default_rng(0).choice(len(parts), max_parts, replace=False)]
    return parts
