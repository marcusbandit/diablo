#!/usr/bin/env python3
"""How turns end with other voices in the room, before and after his own voice is judged.

    .venv-voice/bin/python scripts/ownvoice_eval.py --strangers DIR [--music FILE ...] [--clips 8]

His clips are gvoice's dictations (voice/dictation.py), only ones held out of the talk print,
so the print never heard them. --strangers is a folder of other people's speech with one
folder per speaker (LibriSpeech's layout works). --music is any song with singing. Every case
is mixed in a room, cleaned with RNNoise like the live mic and replayed through
ears.record() with Silero and a probe that reads every line as a finished sentence, so the
base quiet ends it. The judge runs in real time on its own thread, as it does live.

Cases: him alone; him, then a stranger who keeps talking; a stranger talking under him the
whole time and after; him over music. It prints numbers only, never his audio or words.
"""

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import dictation, ears, ownvoice, voiceid, wake  # noqa: E402
from voice.denoise import clean  # noqa: E402
from voice.vad import SileroVAD  # noqa: E402

RATE = wake.RATE
FRAME = 512
TAIL_S = 10.0
FINISHED = "That is all I wanted for now."


def load(path):
    import soundfile as sf
    from scipy.signal import resample_poly

    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if rate != RATE:
        audio = resample_poly(audio, RATE, rate).astype(np.float32)
    return audio


def speech(vad, audio):
    vad.reset()
    return np.array([vad.prob(audio[i:i + FRAME]) >= ears.SPEECH_PROB for i in range(0, len(audio) - FRAME + 1, FRAME)])


def level(vad, audio):
    mask = speech(vad, audio)
    frames = [audio[i * FRAME:(i + 1) * FRAME] for i in np.flatnonzero(mask)] or [audio]
    return float(np.sqrt(np.mean(np.concatenate(frames) ** 2))) + 1e-9


def room(audio, rng, rt60=0.4):
    from scipy.signal import fftconvolve

    n = int(rt60 * RATE)
    tail = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / RATE / rt60) * 0.25
    tail[0] = 1.0
    tail /= np.sqrt(np.sum(tail ** 2))
    return fftconvolve(audio, tail, mode="full")[: len(audio)].astype(np.float32)


def stranger(folder, seconds, rng):
    out, total = [], 0
    for path in sorted(folder.rglob("*.flac")) + sorted(folder.rglob("*.wav")):
        part = load(path)
        out += [part, np.zeros(int(rng.uniform(0.2, 0.6) * RATE), dtype=np.float32)]
        total += len(part)
        if total >= seconds * RATE:
            break
    return np.concatenate(out)[: int(seconds * RATE)]


def fit(audio, n):
    return np.pad(audio, (0, max(0, n - len(audio))))[:n]


class Mic:
    """Blocks of the mix, stamped like the live mic; in real time when paced."""

    def __init__(self, audio, paced):
        self.audio, self.paced, self.at = audio, paced, 0
        self.t0 = time.monotonic()

    def read(self, timeout=1.0):
        if self.at + wake.CHUNK > len(self.audio):
            raise RuntimeError("the mix ran out before the turn ended")
        block = self.audio[self.at:self.at + wake.CHUNK]
        self.at += wake.CHUNK
        due = self.t0 + self.at / RATE
        if self.paced and due > time.monotonic():
            time.sleep(due - time.monotonic())
        return (due if self.paced else time.monotonic()), block


def replay(mix, judge, vad):
    heard = ears.record(Mic(mix, judge is not None), {"ears.adaptive": "1"}, 0.0, vad,
                        probe=lambda clip: FINISHED, judge=judge)
    return heard


def score(heard, judge, his_mask, other_mask, his_end_s):
    turn_s = len(heard.full) / RATE
    cuts = judge.cuts() if judge is not None else []
    kept = np.ones(len(his_mask), dtype=bool)
    for start, end in cuts:
        kept[start // FRAME: end // FRAME] = False
    n = min(len(kept), len(his_mask), len(heard.full) // FRAME)
    his = his_mask[:n]
    other = other_mask[:n]
    his_heard = float(np.mean(kept[:n][his])) if his.any() else 1.0
    lost = his_mask[n:].sum() / max(1, his_mask.sum())
    return {"end": turn_s - his_end_s, "cut_off": turn_s < his_end_s - 0.05,
            "his_kept": his_heard * (1 - lost), "other_kept": float(np.mean(kept[:n][other])) if other.any() else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strangers", required=True, type=Path)
    parser.add_argument("--music", nargs="*", default=[], type=Path)
    parser.add_argument("--clips", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--under-db", type=float, default=-9.0)
    parser.add_argument("--lag", type=float, default=ownvoice.LAG_S)
    args = parser.parse_args()
    ownvoice.LAG_S = args.lag
    rng = np.random.default_rng(args.seed)
    random.seed(args.seed)
    vad = SileroVAD(str(Path.home() / ".local/share/diablo/silero_vad.onnx"))
    index = dictation.load_index()
    names = sorted(n for n, e in index["clips"].items()
                   if dictation.usable(e) and dictation.held(n) and 5 <= e["seconds"] <= 14)
    random.shuffle(names)
    speakers = sorted(p for p in args.strangers.rglob("*") if p.is_dir() and any(p.glob("*/*.flac")))
    random.shuffle(speakers)
    songs = [load(p) for p in args.music]
    minimum = wake.meta().get("talk_min")
    talk_print = np.load(wake.TALK_PRINT)
    voiceid.warm()
    print(f"talk_min {minimum}, lag {ownvoice.LAG_S} s, {len(names)} held out clips to pick from, {len(speakers)} strangers")
    rows = []
    for k, name in enumerate(names[: args.clips]):
        him = clean(wake.read_wav(dictation.CLIPS / name))
        him = np.concatenate([np.zeros(int(0.3 * RATE), dtype=np.float32), him])
        his_mask = speech(vad, him)
        his_end_s = (np.flatnonzero(his_mask)[-1] + 1) * FRAME / RATE
        loud = level(vad, him)
        talk = len(him) + int(TAIL_S * RATE)
        n = talk + int(5 * RATE)
        floor = (rng.standard_normal(n) * 1e-4).astype(np.float32)
        voice = room(stranger(speakers[k % len(speakers)], 30, rng), rng)
        voice *= loud / level(vad, voice)
        cases = {"alone": (fit(him, n), np.zeros(n, dtype=np.float32), 0.0)}
        after = np.zeros(n, dtype=np.float32)
        begin = int(his_end_s * RATE) + int(0.4 * RATE)
        after[begin:talk] = fit(voice * 10 ** (-3 / 20), talk - begin)
        cases["after"] = (fit(him, n), after, 0.0)
        cases["under"] = (fit(him, n), fit(fit(voice, talk), n) * 10 ** (args.under_db / 20), 0.0)
        if songs:
            song = songs[k % len(songs)]
            start = int(rng.uniform(20, max(21, len(song) / RATE - n / RATE - 1)) * RATE)
            song = fit(room(fit(song[start:], talk), rng), n)
            song *= loud / level(vad, song)
            cases["music"] = (fit(him, n), song * 10 ** (args.under_db / 20), ears.BARGE_SLACK)
        for case, (mine, other, slack) in cases.items():
            mix = mine + other + floor
            peak = float(np.max(np.abs(mix)))
            if peak > 0.99:
                mix *= 0.99 / peak
            mix = clean(mix.astype(np.float32))
            hm = np.pad(his_mask, (0, n // FRAME + 1))[: n // FRAME]
            om = speech(vad, other.astype(np.float32)) if other.any() else np.zeros(n // FRAME, dtype=bool)
            om = np.pad(om, (0, n // FRAME + 1))[: n // FRAME]
            for mode in ("before", "after"):
                judge = None
                if mode == "after":
                    judge = ownvoice.Judge(ownvoice.Voice(minimum, talk_print, slack=slack))
                heard = replay(mix, judge, vad)
                rows.append((case, mode, score(heard, judge, hm, om, his_end_s)))
                got = rows[-1][2]
                print(f"clip {k} {case:6s} {mode:6s} end {got['end']:+6.2f} s  cut off {got['cut_off']!s:5s} "
                      f"his kept {got['his_kept']:.3f}  others kept {got['other_kept'] if got['other_kept'] is None else round(got['other_kept'], 3)}",
                      flush=True)
    print()
    print("case    mode    median end  worst end  cut off  his kept  others kept")
    for case in ("alone", "after", "under", "music"):
        for mode in ("before", "after"):
            got = [r for c, m, r in rows if c == case and m == mode]
            if not got:
                continue
            ends = np.array([r["end"] for r in got])
            others = [r["other_kept"] for r in got if r["other_kept"] is not None]
            print(f"{case:7s} {mode:7s} {np.median(ends):+8.2f} s {ends.max():+8.2f} s  {sum(r['cut_off'] for r in got):2d}/{len(got)}"
                  f"   {np.mean([r['his_kept'] for r in got]):.3f}    {np.mean(others) if others else float('nan'):.3f}")


if __name__ == "__main__":
    main()
