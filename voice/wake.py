"""His name as a wake word: a small model trained on Marcus saying "Diablo".

openWakeWord turns 16 kHz audio into one 96 number embedding every 80 ms. The model here
reads the last 16 of those, about 1.3 s, and says how much they sound like Marcus calling
him. It runs on the raw audio, before any speech to text, so a word that merely transcribes
as "diablo" can never wake him.

Training data, all under DATA:
- samples/pos: Marcus saying the name, one short WAV each, recorded by `diablo-ears train`
- samples/speech: Marcus reading a passage full of near misses (diabolical, Pablo, viable)
- samples/room: the room with nobody talking
- acav_head.bin: about 214 hours of other speech, music and noise as ready features, the
  first 600k rows of openWakeWord's public ACAV100M set, used as negatives
- validation_set_features.npy: about 11 hours more of the same, never trained on. The
  threshold is the lowest one that fires zero times across it, plus a margin.

The speaker check is a second, separate gate: a resemblyzer voiceprint of the wake clip
against the average of Marcus's own wake recordings.
"""

import json
import logging
import random
import time
import wave
from pathlib import Path

import numpy as np

from . import voiceid

log = logging.getLogger("diablo.wake")

RATE = 16000
CHUNK = 1280
CLIP_S = 2.0
FRAMES = 16
DATA = Path.home() / ".local/share/diablo/wake"
SAMPLES = DATA / "samples"
MODEL = DATA / "diablo.onnx"
META = DATA / "diablo.json"
ACAV = DATA / "acav_head.bin"
VALIDATION = DATA / "validation_set_features.npy"
ACAV_URL = (
    "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/"
    "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
)
VALIDATION_URL = (
    "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/"
    "validation_set_features.npy"
)
ACAV_ROWS = 600_000
# Two frames over the threshold in a row, 160 ms, before a wake counts.
PATIENCE = 2
# After a wake, 2 s of frames that cannot wake again.
REFRACTORY = 25
MARGIN = 0.05
# His dictations train as negatives every 4th window, 0.32 s apart.
TALK_STRIDE = 4
TALK_PRINT = DATA / "talk.npy"
TALK_FLOOR = 0.62

CONFUSABLES = [
    "diabolical", "the fable", "Pablo", "Diana", "viable", "dial up", "tableau",
    "the bubble", "diagram", "Diego", "the able one", "diablo three",
    "dia de los muertos", "deal though", "D A blow", "Fabio", "via blog",
]


# ---------------------------------------------------------------- audio helpers

def read_wav(path):
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
        channels = handle.getnchannels()
    if width != 2:
        raise ValueError(f"{path}: only 16 bit WAV")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != RATE:
        audio = resample(audio, rate / RATE)
    return audio


def write_wav(path, audio, rate=RATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


def resample(audio, factor):
    """Stretch by factor (>1 shortens). Linear, which is plenty for augmentation."""
    n = max(int(len(audio) / factor), 1)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def trim(audio, pad_s=0.08):
    """The spoken part of a recording, found from a 20 ms energy envelope."""
    hop = RATE // 50
    frames = len(audio) // hop
    if frames == 0:
        return audio
    env = np.sqrt(np.mean(audio[: frames * hop].reshape(frames, hop) ** 2, axis=1))
    loud = np.where(env > max(env.max() * 0.12, 0.003))[0]
    if len(loud) == 0:
        return audio[:0]
    pad = int(pad_s * RATE)
    start = max(loud[0] * hop - pad, 0)
    end = min((loud[-1] + 1) * hop + pad, len(audio))
    return audio[start:end]


HOP = RATE // 50  # 20 ms


def envelope_db(audio):
    """Loudness in dB per 20 ms."""
    frames = len(audio) // HOP
    if frames == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(audio[: frames * HOP].reshape(frames, HOP) ** 2, axis=1))
    return 20 * np.log10(rms + 1e-9)


def segments(db, floor):
    """Runs of sound well over the room, joined across gaps under 160 ms, blips dropped."""
    loud = db > max(floor + 12, -52)
    runs, start = [], None
    for i, on in enumerate(loud):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append([start, i])
            start = None
    if start is not None:
        runs.append([start, len(loud)])
    joined = []
    for run in runs:
        if joined and run[0] - joined[-1][1] <= 8:
            joined[-1][1] = run[1]
        else:
            joined.append(run)
    return [r for r in joined if r[1] - r[0] >= 3]


def judge(audio, typical=None):
    """What one take of the name sounds like, and what is wrong with it if anything.

    Returns a dict: ok, code, why (one short spoken line), detail (the full story for the
    page), seconds (the name itself), start and end (seconds into the take), db (its
    loudest 20 ms), floor (the room), envelope (64 values 0..1 for drawing) and word (the
    name cut out with 80 ms either side, for training). typical is his usual length.
    """
    db = envelope_db(audio)
    total = len(audio) / RATE
    out = {"ok": False, "code": "", "why": "", "detail": "", "seconds": 0.0, "start": 0.0,
           "end": 0.0, "db": float(db.max()) if len(db) else -90.0, "floor": 0.0,
           "envelope": [], "word": audio[:0]}
    if len(db) == 0:
        out.update(code="nothing", why="I didn't hear anything.", detail="The take was empty.")
        return out
    floor = float(np.percentile(db, 20))
    out["floor"] = round(floor, 1)
    picks = np.array_split(db, 64)
    out["envelope"] = [round(float(np.clip((p.max() + 60) / 60, 0, 1)), 3) for p in picks]
    segs = segments(db, floor)
    if not segs:
        out.update(code="nothing", why="I didn't hear anything. Check that the microphone bar moves when you talk.",
                   detail=f"Nothing rose more than 12 dB over the room ({floor:.0f} dB). The loudest moment was {db.max():.0f} dB.")
        return out
    energy = [float(np.sum(10 ** (db[a:b] / 10))) for a, b in segs]
    main = segs[int(np.argmax(energy))]
    a, b = main
    seconds = (b - a) * HOP / RATE
    out.update(seconds=round(seconds, 2), start=round(a * HOP / RATE, 2), end=round(b * HOP / RATE, 2),
               db=round(float(db[a:b].max()), 1))
    pad = int(0.08 * RATE)
    out["word"] = audio[max(a * HOP - pad, 0): min(b * HOP + pad, len(audio))]
    others = [s for s in segs if s is not main and (s[1] - s[0]) * HOP / RATE >= 0.25]
    usual = typical or 0.6
    if a <= 1:
        out.update(code="early", why="You started before I was listening, so I missed the start of my name. Wait for the tick.",
                   detail="The name was already going when the take began, so its start is missing. Say it just after the tick.")
    elif b >= len(db) - 1:
        out.update(code="late", why="You were still talking when the take ended. Say my name right after the tick.",
                   detail=f"The sound was still going at the end of the {total:.1f} s take, so its end is missing.")
    elif seconds < 0.2:
        out.update(code="short", why="That was too short to be my name. More like a click or a cough.",
                   detail=f"The only sound lasted {seconds:.2f} s. Your name usually takes you about {usual:.1f} s.")
    elif others:
        gap = abs(others[0][0] - b if others[0][0] > b else a - others[0][1]) * HOP / RATE
        out.update(code="two", why="I heard two separate sounds. Just my name, once.",
                   detail=f"Two sounds {gap:.1f} s apart, {seconds:.1f} s and {(others[0][1] - others[0][0]) * HOP / RATE:.1f} s long. "
                          "If something else made a noise, say it again.")
    elif seconds > max(1.4, usual * 2.2):
        out.update(code="long", why=f"That one sound lasted {seconds:.1f} seconds, and my name takes you about {usual:.1f}. Was something else making noise?",
                   detail=f"One unbroken sound of {seconds:.1f} s. Your takes so far average {usual:.1f} s.")
    else:
        detail = f"The name took {seconds:.2f} s, at {out['db']:.0f} dB over a room at {floor:.0f} dB."
        if out["db"] - floor < 20:
            detail += " Faint, which is fine for a far take."
        out.update(ok=True, code="ok", detail=detail)
    return out


def to_int16(audio):
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16)


# ---------------------------------------------------------------- augmentation

def _pink(n, rng):
    white = rng.standard_normal(n)
    spectrum = np.fft.rfft(white)
    spectrum /= np.sqrt(np.arange(len(spectrum)) + 1.0)
    pink = np.fft.irfft(spectrum, n)
    return (pink / (np.std(pink) + 1e-9)).astype(np.float32)


def _reverb(audio, rng):
    rt = rng.uniform(0.05, 0.35)
    t = np.arange(int(rt * RATE)) / RATE
    ir = rng.standard_normal(len(t)) * np.exp(-t * 6.9 / rt)
    ir[0] = 1.0
    wet = np.convolve(audio, ir)[: len(audio)]
    wet /= np.max(np.abs(wet)) + 1e-9
    mix = rng.uniform(0.1, 0.45)
    return ((1 - mix) * audio + mix * wet * np.max(np.abs(audio))).astype(np.float32)


def place(word, rng, noise=None, lead=None, end_gap=None, quiet=False, tail=False):
    """One 2 s training clip with word ending end_gap seconds before the clip ends.

    With tail, half the clips carry his talk straight after the word, the way "Diablo,
    can you" sounds: takes alone all end in silence, and a model that only knows those
    misses the name when he runs on into the request."""
    total = int(CLIP_S * RATE)
    word = resample(word, rng.uniform(0.9, 1.1))
    if rng.random() < 0.5:
        word = _reverb(word, rng)
    word = word * (rng.uniform(0.08, 0.9) / (np.max(np.abs(word)) + 1e-9))
    gap = int((end_gap if end_gap is not None else rng.uniform(0.04, 0.35)) * RATE)
    clip = np.zeros(total, dtype=np.float32)
    word = word[-(total - gap):]
    end = total - gap
    clip[end - len(word): end] = word
    if lead is not None and len(lead) > 0 and rng.random() < 0.35:
        room = end - len(word)
        if room > RATE // 5:
            start = rng.integers(0, max(len(lead) - room, 1))
            piece = lead[start: start + room]
            piece = piece * (np.max(np.abs(word)) / (np.max(np.abs(piece)) + 1e-9)) * rng.uniform(0.4, 1.0)
            clip[room - len(piece): room] += piece
    if tail and lead is not None and len(lead) > gap and gap > 0 and rng.random() < 0.5:
        # Starts 0 to 60 ms after the word, as fast as a person goes on talking.
        pause = int(rng.uniform(0.0, 0.06) * RATE)
        start = rng.integers(0, max(len(lead) - gap, 1))
        piece = lead[start: start + max(gap - pause, 0)]
        if len(piece):
            piece = piece * (np.max(np.abs(word)) / (np.max(np.abs(piece)) + 1e-9)) * rng.uniform(0.5, 1.0)
            clip[end + pause: end + pause + len(piece)] += piece
    if not quiet and rng.random() < 0.85:
        if noise is not None and len(noise) > total and rng.random() < 0.6:
            start = rng.integers(0, len(noise) - total)
            bed = noise[start: start + total]
            bed = bed / (np.std(bed) + 1e-9)
        else:
            bed = _pink(total, rng)
        snr = rng.uniform(5, 35)
        speech_rms = np.sqrt(np.mean(word ** 2)) + 1e-9
        clip += bed * speech_rms / (10 ** (snr / 20))
    return clip


def windows(audio, step_s=0.25):
    """2 s windows across a longer recording, for negatives."""
    total = int(CLIP_S * RATE)
    if len(audio) < total:
        audio = np.pad(audio, (total - len(audio), 0))
    step = int(step_s * RATE)
    return [audio[i: i + total] for i in range(0, len(audio) - total + 1, step)]


# ---------------------------------------------------------------- features

_features = None


def features():
    global _features
    if _features is None:
        from openwakeword.utils import AudioFeatures, download_models

        download_models(model_names=["__none__"])
        _features = AudioFeatures(inference_framework="onnx")
    return _features


def embed(clips):
    """(N, 2 s) float clips to (N, 16, 96) features."""
    if not clips:
        return np.zeros((0, FRAMES, 96), dtype=np.float32)
    batch = np.stack([to_int16(c) for c in clips])
    out = features().embed_clips(batch, batch_size=256)
    return out[:, -FRAMES:, :].astype(np.float32)


def acav_negatives(rows=ACAV_ROWS):
    with open(ACAV, "rb") as handle:
        np.lib.format.read_magic(handle)
        np.lib.format.read_array_header_1_0(handle)
        offset = handle.tell()
    have = (ACAV.stat().st_size - offset) // (FRAMES * 96 * 2)
    return np.memmap(ACAV, dtype=np.float16, mode="r", offset=offset, shape=(min(rows, have), FRAMES, 96))


def download(progress=print):
    """Fetches the negative feature sets once. Ranged, so only the rows we use come down."""
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    jobs = [
        (VALIDATION, VALIDATION_URL, None),
        (ACAV, ACAV_URL, 128 + ACAV_ROWS * FRAMES * 96 * 2),
    ]
    for target, url, size in jobs:
        if target.exists() and (size is None or target.stat().st_size >= size):
            continue
        progress(f"fetching {target.name}")
        headers = {"Range": f"bytes=0-{size - 1}"} if size else {}
        tmp = target.with_suffix(".part")
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with open(tmp, "wb") as handle:
                for block in response.iter_bytes(1 << 20):
                    handle.write(block)
        tmp.replace(target)


# ---------------------------------------------------------------- the model

def _net():
    """The wake word brain: dilated convolutions over the 16 frames, then attention.

    The old model was two dense layers over all frames flattened, so it could only match a
    fixed picture of the name at a fixed spot. This one follows the sound across time:
    each layer looks a little further back (1, 2 and 4 frames apart, about 0.6 s of
    context), and the attention picks the frames where the name is. About 150k numbers,
    still under a millisecond per 80 ms on the CPU.
    """
    import torch
    from torch import nn

    class WakeNet(nn.Module):
        def __init__(self, width=128):
            super().__init__()
            self.norm = nn.LayerNorm(96)
            self.convs = nn.ModuleList([
                nn.Conv1d(96 if i == 0 else width, width, 3, padding=d, dilation=d) for i, d in enumerate((1, 2, 4))
            ])
            self.mix = nn.ModuleList([nn.GroupNorm(1, width) for _ in range(3)])
            self.drop = nn.Dropout(0.2)
            self.attend = nn.Conv1d(width, 1, 1)
            self.head = nn.Sequential(nn.Linear(width * 2, 64), nn.GELU(), nn.Linear(64, 1))

        def forward(self, x):
            return torch.sigmoid(self.logit(x))

        def logit(self, x):
            x = self.norm(x).transpose(1, 2)
            for i, (conv, mix) in enumerate(zip(self.convs, self.mix)):
                y = nn.functional.gelu(mix(conv(x)))
                x = y if i == 0 else x + y
            x = self.drop(x)
            weights = torch.softmax(self.attend(x), dim=2)
            pooled = torch.cat([(x * weights).sum(dim=2), x.amax(dim=2)], dim=1)
            return self.head(pooled)

    return WakeNet()


def _fit(pos, hard, acav, steps=4000, seed=0, progress=print):
    import torch

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    net = _net().to(device)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=1e-3, total_steps=steps)
    pos_t = torch.tensor(pos, device=device)
    hard_t = torch.tensor(hard, device=device) if len(hard) else None
    n_pos, n_hard, n_acav = 128, 128 if len(hard) else 0, 512
    for step in range(steps):
        rows = np.sort(rng.choice(len(acav), n_acav, replace=False))
        parts = [pos_t[torch.randint(len(pos_t), (n_pos,), device=device)],
                 torch.tensor(np.asarray(acav[rows], dtype=np.float32), device=device)]
        if hard_t is not None:
            parts.insert(1, hard_t[torch.randint(len(hard_t), (n_hard,), device=device)])
        x = torch.cat(parts)
        y = torch.cat([torch.ones(n_pos, device=device), torch.zeros(n_hard + n_acav, device=device)])
        # Negatives weigh more as training goes on: early it learns the word, late it learns
        # to keep quiet for everything else.
        neg_weight = 1.0 + 9.0 * min(step / (steps * 0.6), 1.0)
        weight = torch.where(y > 0, torch.ones_like(y), torch.full_like(y, neg_weight))
        net.train()
        # On the raw score, never a clamped probability: a clamp passes no gradient once the
        # model saturates, which froze an earlier run answering no to everything.
        loss = torch.nn.functional.binary_cross_entropy_with_logits(net.logit(x).squeeze(1), y, weight=weight)
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
        opt.step()
        sched.step()
        if step % 1000 == 0:
            progress(f"  step {step}/{steps}, loss {loss.item():.4f}")
    net.eval()
    return net.cpu()


def _fit_mined(pos, hard, acav, steps, seed, progress=print, keep=3000):
    """Two passes: train, find the negatives it likes most, train again with them weighted in.

    Random batches see a near miss in 214 hours only now and then; the second pass sees
    every one it got wrong in every batch.
    """
    net = _fit(pos, hard, acav, steps=steps, seed=seed, progress=progress)
    scores = np.concatenate([_score(net, acav[i: i + 50_000]) for i in range(0, len(acav), 50_000)])
    top = np.sort(np.argsort(scores)[-keep:])
    progress(f"  {int((scores > 0.1).sum())} negatives scored over 0.1, retraining with the top {keep}")
    mined = np.asarray(acav[top], dtype=np.float32)
    hard = np.concatenate([hard, mined]) if len(hard) else mined
    return _fit(pos, hard, acav, steps=steps, seed=seed + 100, progress=progress)


def _score(net, x, batch=8192):
    import torch

    out = []
    with torch.no_grad():
        for i in range(0, len(x), batch):
            out.append(net(torch.tensor(np.asarray(x[i: i + batch], dtype=np.float32))).squeeze(1).numpy())
    return np.concatenate(out) if out else np.zeros(0)


def stream_windows(frames):
    """(T, 96) continuous frames to (T-15, 16, 96) sliding windows, a view."""
    from numpy.lib.stride_tricks import sliding_window_view

    return sliding_window_view(frames, (FRAMES, 96))[:, 0]


def fire_frames(scores, threshold, patience=PATIENCE, refractory=REFRACTORY):
    """The frame of each wake in a score stream, the same rule the live detector uses."""
    fires, run, quiet = [], 0, 0
    for i, s in enumerate(scores):
        if quiet:
            quiet -= 1
            continue
        run = run + 1 if s >= threshold else 0
        if run >= patience:
            fires.append(i)
            run, quiet = 0, refractory
    return fires


def count_fires(scores, threshold, patience=PATIENCE, refractory=REFRACTORY):
    """Wakes in a score stream, the same rule the live detector uses."""
    return len(fire_frames(scores, threshold, patience, refractory))


def fire_end_s(frame):
    """Where in the clip a wake at this score frame ends, in seconds (the dictation check's rule)."""
    return round((frame + FRAMES + 8) * 0.08, 2)


def _peak_run(scores, patience=PATIENCE):
    """Highest threshold that still gives patience frames in a row: min over each run."""
    if len(scores) < patience:
        return 0.0
    from numpy.lib.stride_tricks import sliding_window_view

    return float(sliding_window_view(scores, patience).min(axis=1).max())


def _export(net, path):
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.onnx")
    torch.onnx.export(
        net, torch.zeros(1, FRAMES, 96), str(tmp), input_names=["x"], output_names=["score"],
        dynamic_axes={"x": {0: "batch"}, "score": {0: "batch"}}, opset_version=17, dynamo=False,
    )
    tmp.replace(path)


AMBIENT_USED = 1500


def load_samples():
    """Takes of the name, and everything from this mic that is not the name.

    The second part is what keeps the model from learning "this microphone" instead of
    "Diablo": his speech, near words in his voice, noise he made on purpose, the quiet
    room, and everyday clips the service keeps while asleep (the newest AMBIENT_USED).
    """
    from .denoise import ensure_clean

    def wavs(kind, newest=None):
        # Recordings from before the noise filter get cleaned once, so training hears what
        # the live mic hears. Piper's near misses are synthetic and stay as they are.
        if kind != "confusable":
            ensure_clean(SAMPLES / kind, read_wav, write_wav)
        files = sorted((SAMPLES / kind).glob("*.wav"))
        return [read_wav(p) for p in (files[-newest:] if newest else files)]

    mine = {
        "speech": wavs("speech"), "room": wavs("room"), "noise": wavs("noise"),
        "notname": wavs("notname"), "ambient": wavs("ambient", AMBIENT_USED),
    }
    return wavs("pos"), mine, wavs("confusable")


def synth_confusables(piper_dir, progress=print):
    """Near miss phrases in the local piper voice, written to samples/confusable once."""
    import subprocess

    piper_dir = Path(piper_dir)
    binary, model = piper_dir / "piper", piper_dir / "voice.onnx"
    target = SAMPLES / "confusable"
    if not binary.exists() or not model.exists() or (target.exists() and any(target.glob("*.wav"))):
        return
    target.mkdir(parents=True, exist_ok=True)
    progress("speaking near misses in the piper voice")
    for i, phrase in enumerate(CONFUSABLES):
        out = target / f"{i:02d}.wav"
        subprocess.run([str(binary), "--model", str(model), "--output_file", str(out)],
                       input=phrase.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60, check=False)


def _build(pos_words, mine, confusable, rng, per_word=60):
    beds = mine["room"] + mine["noise"] + mine["ambient"]
    noise = np.concatenate(beds) if beds else None
    lead = np.concatenate(mine["speech"]) if mine["speech"] else None
    pos = embed([place(w, rng, noise, lead, tail=True) for w in pos_words for _ in range(per_word)])
    hard_clips = []
    for rec in mine["speech"] + mine["room"] + mine["noise"]:
        hard_clips += windows(rec)
    # Everyday clips are 2 s already: each is one window, as the mic heard it.
    hard_clips += [rec for rec in mine["ambient"] if len(rec) >= int(CLIP_S * RATE)]
    # Near words in his own voice weigh most: "Diplo" said by him is the closest miss.
    for rec in mine["notname"]:
        word = judge(rec)["word"]
        if len(word):
            hard_clips += [place(word, rng, noise, lead, tail=True) for _ in range(40)]
    for rec in confusable:
        word = trim(rec)
        if len(word):
            hard_clips += [place(word, rng, noise, lead, tail=True) for _ in range(20)]
    # The name cut off before its end, so half a word never counts.
    for w in pos_words:
        cut = w[: int(len(w) * rng.uniform(0.35, 0.6))]
        hard_clips += [place(cut, rng, noise, lead, tail=True) for _ in range(4)]
    hard = embed(hard_clips)
    return pos, hard


def train(progress=print, steps=6000, seed=0):
    """Trains, calibrates and writes MODEL and META. Returns the metrics dict."""
    started = time.time()
    rng = np.random.default_rng(seed)
    random.seed(seed)
    pos_raw, mine, confusable = load_samples()
    # The name itself, found the same way the setup judged it; takes cut off at either
    # end (the setup saved some before it could tell) are left out and counted.
    left_out = {}
    pos_words = []
    for r in pos_raw:
        verdict = judge(r)
        if verdict["ok"]:
            pos_words.append(verdict["word"])
        else:
            left_out[verdict["code"]] = left_out.get(verdict["code"], 0) + 1
    if left_out:
        progress("left out: " + ", ".join(f"{n} {code}" for code, n in left_out.items()))
    if len(pos_words) < 10:
        raise RuntimeError(f"only {len(pos_words)} usable recordings of the name, 10 or more needed")
    download(progress)
    acav = acav_negatives()
    # His own dictations: most of them train as "not the name", every fifth is kept back
    # to count false wakes on his talk, the case that matters most at this desk.
    from . import dictation

    talk_index = dictation.double_check(dictation.import_new(progress), progress)
    talk_train, talk_held, held_names = dictation.wake_streams(talk_index, names=True)
    talk_windows = (np.concatenate([stream_windows(s)[::TALK_STRIDE] for s in talk_train if len(s) >= FRAMES])
                    if talk_train else np.zeros((0, FRAMES, 96), dtype=np.float32))
    progress(f"{len(talk_train)} dictations to learn from, {len(talk_held)} kept back to check on")
    order = rng.permutation(len(pos_words))
    held = [pos_words[i] for i in order[: max(len(pos_words) // 5, 2)]]
    kept = [pos_words[i] for i in order[max(len(pos_words) // 5, 2):]]

    progress(f"{len(pos_words)} takes of the name; from this mic: " + ", ".join(f"{len(v)} {k}" for k, v in mine.items()))
    progress("checking on recordings held out of training")
    pos, hard = _build(kept, mine, confusable, rng)
    hard = np.concatenate([hard, talk_windows])
    net = _fit_mined(pos, hard, acav, steps=steps, seed=seed, progress=progress)
    beds = mine["room"] + mine["noise"] + mine["ambient"]
    noise = np.concatenate(beds) if beds else None
    held_clean = _held_peaks(net, held, rng, None, quiet=True)
    held_noisy = _held_peaks(net, held, rng, noise)

    progress("training on every recording")
    pos, hard = _build(pos_words, mine, confusable, rng)
    hard = np.concatenate([hard, talk_windows])
    net = _fit_mined(pos, hard, acav, steps=steps, seed=seed + 1, progress=progress)

    progress("calibrating on 11 hours it never heard")
    val = np.load(VALIDATION, mmap_mode="r")
    val_scores = _score(net, stream_windows(np.asarray(val, dtype=np.float32)))
    # Hard negatives are separate clips, so the highest single score is the bar to clear.
    # The talk windows are left out here: one stray high window in hours of his talk would
    # lift the floor with no patience applied. His held out talk has its own check below.
    near = hard[: len(hard) - len(talk_windows)]
    hard_peak = float(_score(net, near).max()) if len(near) else 0.0
    # The lowest threshold with zero wakes on the 11 hours, at the shortest patience that
    # gets there under the cap. A model that cannot is marked not ready.
    long_enough = [(n, s) for n, s in zip(held_names, talk_held) if len(s) >= FRAMES]
    talk_scores = [_score(net, stream_windows(s)) for _, s in long_enough]
    talk_hours = sum(len(s) for s in talk_held) * 0.08 / 3600
    for patience in (PATIENCE, 3, 4, 5):
        talk_peak = max((_peak_run(t, patience) for t in talk_scores), default=0.0)
        floor = max(_peak_run(val_scores, patience), hard_peak, talk_peak)
        threshold = float(min(max(floor + MARGIN, 0.5), 0.97))
        if count_fires(val_scores, threshold, patience) == 0 and not _talk_fires(talk_scores, threshold, patience):
            break
    hours = len(val) * 0.08 / 3600
    metrics = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "threshold": threshold,
        "patience": patience,
        "recordings": len(pos_words),
        "left_out": left_out,
        "held_out": len(held),
        "held_out_recall_clean": _recall(held_clean, threshold, patience),
        "held_out_recall_noisy": _recall(held_noisy, threshold, patience),
        "validation_hours": round(hours, 1),
        "validation_fires": count_fires(val_scores, threshold, patience),
        "validation_peak": float(_peak_run(val_scores, patience)),
        "hard_negative_peak": hard_peak,
        "talk_hours": round(talk_hours, 1),
        "talk_fires": _talk_fires(talk_scores, threshold, patience),
        # Where each of those wakes was, so the Voice page can play it back.
        "talk_fire_clips": [
            {"clip": name, "end_s": fire_end_s(i)}
            for (name, _), t in zip(long_enough, talk_scores)
            for i in fire_frames(t, threshold, patience)
        ][:20],
        "talk_peak": float(max((_peak_run(t, patience) for t in talk_scores), default=0.0)),
        "dictation": dictation.summary(talk_index),
        "mine": {k: len(v) for k, v in mine.items()},
        "seconds": round(time.time() - started, 1),
    }
    metrics["ready"] = (metrics["validation_fires"] == 0 and metrics["talk_fires"] == 0
                        and metrics["held_out_recall_clean"] >= 0.8)
    prints = {}
    metrics.update(speaker_profile(pos_words, dictation.voice_parts(talk_index),
                                   dictation.voice_parts(talk_index, held_out=True), prints=prints))
    old = meta() if trained() else {}
    better = (not old or metrics["ready"] or not old.get("ready", old.get("validation_fires", 1) == 0)
              and metrics["validation_fires"] <= old.get("validation_fires", 10 ** 9))
    if not better:
        # Kept beside the working model for a look, never loaded by the service.
        metrics["installed"] = False
        _export(net, MODEL.with_name("diablo.candidate.onnx"))
        MODEL.with_name("diablo.candidate.json").write_text(json.dumps(metrics, indent=1))
        return metrics
    metrics["installed"] = True
    # The prints go with the model they were measured with, never ahead of it.
    _save_prints(prints)
    tmp = META.with_suffix(".tmp")
    tmp.write_text(json.dumps(metrics, indent=1))
    tmp.replace(META)
    _export(net, MODEL)
    return metrics


def _talk_fires(streams, threshold, patience):
    return sum(count_fires(t, threshold, patience) for t in streams)


def _held_peaks(net, words, rng, noise, quiet=False):
    """Per held out word, the score streams of three placements, as the live detector sees them."""
    streams = []
    for w in words:
        clips = [place(w, rng, noise, None, end_gap=g, quiet=quiet) for g in (0.05, 0.15, 0.3)]
        mine = []
        for clip in clips:
            padded = np.concatenate([np.zeros(RATE, dtype=np.float32), clip, np.zeros(RATE // 2, dtype=np.float32)])
            emb = features().embed_clips(to_int16(padded)[None], batch_size=1)[0]
            mine.append(_score(net, stream_windows(emb)))
        streams.append(mine)
    return streams


def _recall(streams, threshold, patience):
    """Share of held out words that wake at this threshold and patience."""
    if not streams:
        return 0.0
    return float(np.mean([max(_peak_run(s, patience) for s in word) >= threshold for word in streams]))


# ---------------------------------------------------------------- speaker check

def _voice_embed(audio, timeout=None):
    from resemblyzer import preprocess_wav

    encoder = voiceid._load_encoder()
    if encoder is None:
        return None
    wav = preprocess_wav(audio.astype(np.float32), source_sr=RATE)
    if len(wav) < RATE // 4:
        return None
    try:
        return voiceid.embed(wav, timeout=timeout)
    except TimeoutError:
        log.warning("voiceprint thread busy, the check passes")
        return None


def speaker_profile(words, talk=None, talk_held=None, prints=None):
    """Two voiceprints: one for the wake word, one for the request after it.

    One word is too little for resemblyzer to know a voice by. Measured on 2026-11-09
    against 40 LibriSpeech strangers: a print from hours of his dictations scored his own
    "Diablo" at 0.59 and a stranger's half second at 0.49, too close to split. So the
    wake word is checked against his takes of the name, a print of how he says that
    word, and the request he speaks after the chime, seconds of him talking, is checked
    against the print from his dictations, where 1.6 s of him sits near 0.83 and 1.2 s
    of a stranger near 0.52.
    """
    embs = [e for e in (_voice_embed(w) for w in words) if e is not None]
    if len(embs) < 3:
        return {"speaker_min": None}
    embs = np.stack(embs)
    embs = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9)
    centroid = embs.mean(axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-9
    # Leave one out, so each recording is judged against a profile that never saw it.
    sims = []
    for i in range(len(embs)):
        rest = np.delete(embs, i, axis=0).mean(axis=0)
        rest /= np.linalg.norm(rest) + 1e-9
        sims.append(float(embs[i] @ rest))
    # Into prints, not onto disk: train saves them only if the new model is installed.
    prints = {} if prints is None else prints
    prints["speaker"] = centroid.astype(np.float32)
    minimum = float(max(np.percentile(sims, 5) - 0.06, 0.55))
    out = {"speaker_min": minimum, "speaker_self_low": float(min(sims)), "speaker_self_mean": float(np.mean(sims))}
    if talk is not None and len(talk) >= 200 and talk_held is not None and len(talk_held) >= 50:
        from .dictation import _robust_centre

        voice = _robust_centre(np.asarray(talk, dtype=np.float32))
        held = np.asarray(talk_held, dtype=np.float32) @ voice
        # Pieces of held out dictations he never trained on: the bar lets 98% of them
        # through, less a margin. The floor of 0.62 let 4% of 1.2 s stranger crops by,
        # and a whole request is longer, which pulls strangers further down.
        talk_min = float(max(np.percentile(held, 2) - 0.05, TALK_FLOOR))
        prints["talk"] = voice.astype(np.float32)
        out.update({"talk_min": talk_min, "talk_self_mean": float(held.mean()),
                    "talk_pass": float(np.mean(held >= talk_min)), "talk_pieces": int(len(talk))})
    else:
        prints["talk"] = None
    return out


def _save_prints(prints):
    """Writes the voiceprints atomically: the service may be loading one mid write."""
    for name, path in (("speaker", DATA / "speaker.npy"), ("talk", TALK_PRINT)):
        if name not in prints:
            continue
        if prints[name] is None:
            path.unlink(missing_ok=True)
            continue
        tmp = path.with_suffix(".tmp.npy")
        np.save(tmp, prints[name])
        tmp.replace(path)


TALK_MIN_S = 1.2


def talk_score(audio):
    """The request after the chime against the print from his dictations, or None.

    None for under 1.2 s of speech: the bar was set on 1.6 s pieces, and one word of
    his scores near 0.59, so "stop" alone would be refused. A short request passes.
    """
    if not TALK_PRINT.exists():
        return None
    from resemblyzer import preprocess_wav

    if len(preprocess_wav(audio.astype(np.float32), source_sr=RATE)) < TALK_MIN_S * RATE:
        return None
    emb = _voice_embed(audio, timeout=voiceid.LIVE_TIMEOUT_S)
    if emb is None:
        return None
    return float(np.load(TALK_PRINT) @ emb / (np.linalg.norm(emb) + 1e-9))


def speaker_score(audio):
    path = DATA / "speaker.npy"
    if not path.exists():
        return None
    emb = _voice_embed(audio, timeout=voiceid.LIVE_TIMEOUT_S)
    if emb is None:
        return None
    ref = np.load(path)
    return float(ref @ emb / (np.linalg.norm(emb) + 1e-9))


# ---------------------------------------------------------------- live detection

def trained():
    return MODEL.exists() and META.exists()


def meta():
    try:
        return json.loads(META.read_text())
    except (OSError, ValueError):
        return {}


class Detector:
    """Feed 80 ms of int16 audio at a time; feed() returns True on a wake."""

    def __init__(self, threshold=None, patience=None):
        from openwakeword.model import Model

        from openwakeword.utils import download_models

        info = meta()
        self.threshold = float(threshold if threshold is not None else info.get("threshold", 0.8))
        self.patience = int(patience or info.get("patience", PATIENCE))
        download_models(model_names=["__none__"])
        self.model = Model(wakeword_models=[str(MODEL)], inference_framework="onnx")
        self.name = MODEL.stem
        self.run = 0
        self.last = 0.0
        self.mtime = MODEL.stat().st_mtime

    def feed(self, chunk):
        self.last = float(self.model.predict(chunk)[self.name])
        self.run = self.run + 1 if self.last >= self.threshold else 0
        if self.run >= self.patience:
            self.run = 0
            return True
        return False

    def reset(self):
        self.model.reset()
        self.run = 0

    def stale(self):
        try:
            return MODEL.stat().st_mtime != self.mtime
        except OSError:
            return False
