"""diablo-act: Diablo acts out a recording of your voice, as a video.

    diablo-act --last                  the newest gvoice dictation clip
    diablo-act --record                record from the mic until Enter
    diablo-act talk.m4a                any audio file ffmpeg can read
    diablo-act --last --dk             say the language (--en/--us, --da/--dk, --es, --ja/--jp);
                                       without it every stretch of speech is guessed (auto)

The words come from Whisper (or --text), the mouth is aligned to the recording sound by
sound (voice/mouth.py), Jev picks the eyes for every clause (voice/eyes.py, or --look pins
one look for the whole take), and the face is assembled from Marcus's drawings the same way
the orb does it: horns, eyes and mouth stacked, line boil every 250 ms, blinks every 2 to 6 s.
The video lands in ~/Videos/diablo/ and opens when it is done.
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FACES = REPO / "assets/faces"
CLIPS = Path.home() / ".local/share/gvoice/clips"
OUT_DIR = Path.home() / "Videos/diablo"
STT_PYTHON = REPO / ".venv-stt/bin/python"
STT_ENV = Path.home() / ".config/diablo-stt/env"
WHISPER = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
FPS = 30
BOIL = 0.25
BLINK = 0.13


def looks():
    return sorted(p.name for p in (FACES / "eyes").iterdir() if p.is_dir())


def say(message):
    print(f"diablo-act: {message}", file=sys.stderr, flush=True)


def record(path):
    """Records the default mic to `path` until Enter."""
    proc = subprocess.Popen(["pw-record", "--rate", "48000", "--channels", "1", str(path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        input("diablo-act: recording, press Enter to stop ")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        proc.terminate()
        proc.wait()


def to_wav(source, target):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-ac", "1", "-ar", "24000",
                    "-c:a", "pcm_s16le", str(target)], check=True)


LANGS = ("en", "da", "es", "ja")
ALIASES = {"us": "en", "uk": "en", "english": "en", "dk": "da", "danish": "da", "dansk": "da",
           "spanish": "es", "espanol": "es", "jp": "ja", "japanese": "ja"}

# Runs in the speech-to-text venv. Prints the speech as JSON segments with their language.
# Auto picks the likeliest of LANGS for the whole take, then checks each stretch of speech on
# its own, so a take that switches language keeps the right words for each part.
_WHISPER = """
import json, sys
from faster_whisper import WhisperModel, decode_audio
LANGS = %r
try:  # the cached copy, without asking Hugging Face; the hub only when there is none
    m = WhisperModel(%r, device="cuda", compute_type="float16", local_files_only=True)
except Exception:
    m = WhisperModel(%r, device="cuda", compute_type="float16")
path, lang, text = sys.argv[1], sys.argv[2], sys.argv[3]
audio = decode_audio(path, sampling_rate=16000)

def best(clip):
    probs = dict(m.detect_language(clip)[2])
    return max(LANGS, key=lambda l: probs.get(l, 0.0))

def words(clip, language):
    return " ".join(s.text.strip() for s in m.transcribe(clip, language=language, vad_filter=True)[0]).strip()

first = best(audio) if lang == "auto" else lang
if text:
    print(json.dumps([{"start": 0.0, "end": len(audio) / 16000, "text": text, "lang": first}]))
    sys.exit()
out = []
for s in m.transcribe(audio, language=first, vad_filter=True)[0]:
    language, said = first, s.text.strip()
    clip = audio[int(s.start * 16000):int(s.end * 16000)]
    if lang == "auto" and len(clip) > 16000:
        language = best(clip)
        if language != first:
            said = words(clip, language) or said
    out.append({"start": s.start, "end": s.end, "text": said, "lang": language})
print(json.dumps(out))
"""


def transcribe(wav, lang="auto", text=""):
    """[{start, end, text, lang}]: what is said and in which language, from Whisper on the GPU
    in the speech-to-text venv. With `text` the words are taken as given, and Whisper only
    names the language when it is auto."""
    env = dict(os.environ)
    if STT_ENV.exists():
        for line in STT_ENV.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"')
    if text and lang != "auto":
        from .mouth import seconds

        # words and language both given: nothing for Whisper to do
        return [{"start": 0.0, "end": seconds(Path(wav).read_bytes()), "text": text, "lang": lang}]
    script = _WHISPER % (LANGS, WHISPER, WHISPER)
    out = subprocess.run([str(STT_PYTHON), "-c", script, str(wav), lang, text or ""],
                         capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"Whisper failed: {out.stderr.strip()[-400:]}")
    found = next((line for line in reversed(out.stdout.splitlines()) if line.startswith("[")), None)
    try:
        return json.loads(found)
    except (TypeError, ValueError):
        raise RuntimeError(f"Whisper gave no answer: {out.stdout.strip()[-200:] or 'nothing'}") from None


def measure(wav, segments, pinned=None):
    """The face for the whole take, each stretch of speech aligned in its own language and
    shifted to where it sits. Returns (face, looks, text).

    A stretch is padded a little into the quiet around it, never into its neighbour, and a
    new stretch first drops any cue of the one before that falls after its start, so the
    cues always run forward in time."""
    import io
    import wave

    import numpy as np

    from . import eyes, mouth

    samples, rate = mouth._pcm(Path(wav).read_bytes())
    total = len(samples) / rate
    spoken = [s for s in segments if str(s.get("text") or "").strip()]
    face, looks, texts = {"mouth": [], "eyes": []}, [], []
    for i, seg in enumerate(spoken):
        lang = seg.get("lang") if seg.get("lang") in LANGS else "en"
        text = mouth.spaced(seg["text"], lang)
        before = float(spoken[i - 1]["end"]) if i else 0.0
        after = float(spoken[i + 1]["start"]) if i + 1 < len(spoken) else total
        begin = max(before, float(seg["start"]) - 0.1)
        end = min(after, float(seg["end"]) + 0.1)
        clip = samples[int(begin * rate):int(end * rate)]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes((np.clip(clip, -1, 1) * 32767).astype("<i2").tobytes())
        picked = tag_eyes(text, pinned)
        part = mouth.face_for(text, buf.getvalue(), picked, lang)
        for key in ("mouth", "eyes"):
            face[key] = [cue for cue in face[key] if cue[0] < begin]
        if face["mouth"]:
            face["mouth"].append((round(begin, 3), "rest"))
        for key in ("mouth", "eyes"):
            face[key] += [(round(at + begin, 3), value) for at, value in part[key]]
        looks += picked
        texts.append(text)
        say(f"{lang}: " + ", ".join(f"{look} ({clause})" for (clause, _), look in zip(eyes.clauses(text), picked)))
    return face, looks, " ".join(texts)


_jev = {"bound": False}


def tag_eyes(text, pinned):
    from . import eyes

    if pinned:
        return [pinned] * len(eyes.clauses(text))
    from . import jevfast

    if _jev["bound"] or jevfast._bound["settings"] is not None:
        # already bound: once by this run, or by the ears this runs inside
        return eyes.tag(text)
    _jev["bound"] = True
    try:
        from . import config, hubclient

        live = hubclient.get_settings(config.load_config()) or {}  # fetched once for the whole run

        class Once:
            def get(self):
                return live

        jevfast.bind(Once())
    except Exception as error:
        say(f"no Jev ({error}), neutral eyes")
    return eyes.tag(text)


def at(cues, t, default):
    value = default
    for cue in cues:
        if cue[0] > t:
            break
        value = cue[1]
    return value


def render(wav, face, out, size, colour, background, transparent, seed):
    import numpy as np
    from PIL import Image

    tint = tuple(int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    bg = tuple(int(background.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    cache = {}

    def layer(path):
        if path not in cache:
            shade, alpha = Image.open(path).convert("LA").split()
            rgb = Image.merge("RGB", [shade.point(lambda v, c=c: v * c // 255) for c in tint]).convert("RGBA")
            rgb.putalpha(alpha)
            cache[path] = rgb.resize((size, size), Image.LANCZOS)
        return cache[path]

    from .mouth import seconds

    duration = seconds(Path(wav).read_bytes())
    rnd = random.Random(seed)
    frame, take, tick, shape_was, look_was = 1, 1, -1, None, None
    blink_until, next_blink = -1.0, rnd.uniform(2, 6)
    pix = "rgba" if transparent else "rgb24"
    codec = (["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30"] if transparent
             else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"])
    audio = ["-c:a", "libopus"] if transparent else ["-c:a", "aac", "-b:a", "192k"]
    ffmpeg = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", pix,
                               "-s", f"{size}x{size}", "-r", str(FPS), "-i", "-", "-i", str(wav),
                               *codec, *audio, "-shortest", str(out)], stdin=subprocess.PIPE)
    for f in range(int(duration * FPS) + 1):
        t = f / FPS
        if int(t / BOIL) != tick:
            tick = int(t / BOIL)
            frame = rnd.choice([n for n in range(1, 5) if n != frame])
        shape = at(face["mouth"], t, "rest")
        if shape != shape_was:
            take = rnd.choice([n for n in range(1, 4) if n != take])
            shape_was = shape
        look = at(face["eyes"], t, "neutral")
        if look != look_was:
            if look_was is not None and rnd.random() < 0.5:
                blink_until = t + BLINK
            look_was = look
        if t >= next_blink:
            blink_until, next_blink = t + BLINK, t + rnd.uniform(2, 6)
        shown = "closed" if t < blink_until else look
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0) if transparent else bg + (255,))
        for path in (FACES / "horns" / f"{frame}.png", FACES / "eyes" / shown / f"{frame}.png",
                     FACES / "mouths" / shape / f"{take}.png"):
            canvas.alpha_composite(layer(path))
        ffmpeg.stdin.write(np.asarray(canvas if transparent else canvas.convert("RGB")).tobytes())
    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        raise SystemExit("diablo-act: ffmpeg failed to write the video")


def out_path(transparent=False):
    """A new file in OUT_DIR, unique even for two takes finished in the same second."""
    import uuid

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR / time.strftime(f"act-%Y%m%d-%H%M%S-{uuid.uuid4().hex[:4]}.{'webm' if transparent else 'mp4'}")


def make(wav, text, out, face, looks, size=1080, colour="#0040ff", background="#f2f1ee", transparent=False):
    """Renders a measured take: the video at `out` and its text, looks and cues beside it as
    .json. Both diablo-act and the ears' "animate me" end here."""
    out = Path(out)
    out.with_suffix(".json").write_text(json.dumps({"text": text, "looks": looks, **face}, indent=1))
    render(wav, face, out, size, colour, background, transparent, seed=len(text))
    return out


def build_parser():
    parser = argparse.ArgumentParser(prog="diablo-act", description=__doc__.split("\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("audio", nargs="?", help="an audio file")
    source.add_argument("--last", action="store_true", help="the newest gvoice dictation clip")
    source.add_argument("--record", action="store_true", help="record from the mic until Enter")
    parser.add_argument("--text", help="what is said, instead of asking Whisper")
    parser.add_argument("--lang", default="auto", type=lambda v: ALIASES.get(v.lower(), v.lower()),
                        choices=("auto",) + LANGS, help="the language spoken: auto (default), en, da, es or ja")
    for flag, lang in (("--en", "en"), ("--us", "en"), ("--da", "da"), ("--dk", "da"), ("--es", "es"),
                       ("--ja", "ja"), ("--jp", "ja")):
        parser.add_argument(flag, dest="lang", action="store_const", const=lang, help=f"same as --lang {lang}")
    parser.add_argument("--look", choices=looks(), help="one eye look for the whole take instead of Jev's")
    parser.add_argument("-o", "--out", help="where the video goes (default ~/Videos/diablo/act-<time>.mp4)")
    parser.add_argument("--size", type=int, default=1080, help="width and height in pixels (default 1080)")
    parser.add_argument("--colour", default="#0040ff", help="ink colour (default #0040ff)")
    parser.add_argument("--background", default="#f2f1ee", help="paper colour (default #f2f1ee)")
    parser.add_argument("--transparent", action="store_true", help="no background: a .webm with alpha, for editing")
    parser.add_argument("--no-open", action="store_true", help="do not open the video when done")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    suffix = ".webm" if args.transparent else ".mp4"
    if args.out and Path(args.out).suffix.lower() != suffix:
        # the codec decides the container: VP9 with alpha needs webm, H.264 needs mp4
        args.out = str(Path(args.out).with_suffix(suffix))
        say(f"writing {args.out}, the {suffix} container fits {'--transparent' if args.transparent else 'H.264'}")

    with tempfile.TemporaryDirectory(prefix="diablo-act-") as tmp:
        tmp = Path(tmp)
        if args.record:
            raw = tmp / "take.wav"
            record(raw)
        elif args.last:
            clips = sorted(CLIPS.glob("*.wav"))
            if not clips:
                raise SystemExit(f"diablo-act: no clips in {CLIPS}")
            raw = clips[-1]
            say(f"using {raw.name}")
        else:
            raw = Path(args.audio).expanduser()
            if not raw.exists():
                raise SystemExit(f"diablo-act: no such file: {raw}")
        wav = tmp / "voice.wav"
        to_wav(raw, wav)

        started = time.monotonic()
        try:
            segments = transcribe(wav, args.lang, args.text or "")
        except RuntimeError as error:
            raise SystemExit(f"diablo-act: {error}") from None
        if not any(seg["text"].strip() for seg in segments):
            raise SystemExit("diablo-act: heard no words")
        face, picked, text = measure(wav, segments, args.look)
        say(f"words: {text}")

        out = Path(args.out).expanduser() if args.out else out_path(args.transparent)
        make(wav, text, out, face, picked, args.size, args.colour, args.background, args.transparent)
        say(f"done in {time.monotonic() - started:.0f} s: {out}")
        print(out)
    if not args.no_open:
        subprocess.Popen(["xdg-open", str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


if __name__ == "__main__":
    main()
