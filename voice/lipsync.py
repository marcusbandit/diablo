"""How far his mouth runs behind his voice, tuned by eye, and the tuner's sound.

The voice stream knows when each sample is heard (voice/playback.py) and the orb plays the
mouth track against that moment. What is left over sits after the clock: the speakers and
the room, the screen and the compositor, and what an eye reads as in step, which leads the
sound a little. That last part is a matter of taste, so it is tuned by eye and kept per box in
~/.config/diablo/lipsync.json as {"delay_ms": n}. A positive delay moves the mouth later than
the sound, a negative one earlier. The orb reads that file (presence/shell.qml) and applies it
to every track it plays, the tuner included, so a saved value takes effect at once.

The tuner (presence/tuner.qml) runs `python -m voice.lipsync serve`, which plays a spoken
sample in a loop on the same stream his answers use and prints one JSON line when each pass is
heard and one when it has ended:

    {"event": "heard", "start": <wall clock>, "duration": s, "cues": [...], "eyes": [...]}
    {"event": "done"}

`diablo-ears mouth` opens the tuner; so does Settings in the central card.
"""

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("diablo.lipsync")

LIMIT_MS = 400
GAP_S = 0.9
SAMPLE = ("Perhaps, sir, a modest proposal. I make the coffee, you fetch the biscuits, "
          "and we both pretend the washing up will do itself.")
REPO = Path(__file__).resolve().parent.parent


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "diablo" / "lipsync.json"


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "diablo" / "lipsync"


def clamp(ms):
    if not math.isfinite(ms):
        raise ValueError(f"not a delay: {ms}")
    return max(-LIMIT_MS, min(LIMIT_MS, int(round(ms))))


def delay_ms(path=None):
    """The saved delay in milliseconds, 0 when nothing is saved or the file is unreadable."""
    try:
        return clamp(float(json.loads((path or config_path()).read_text()).get("delay_ms", 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def save(ms, path=None):
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"delay_ms": clamp(ms)}) + "\n")
    os.replace(tmp, path)
    return clamp(ms)


class _Quiet:
    interrupted = threading.Event()


def _settings():
    from . import hubclient
    from .config import load_config

    cfg = load_config()
    try:
        fetched = hubclient.get_settings(cfg)
    except Exception:
        log.exception("hub settings unavailable, speaking with the local config")
        fetched = None
    return {**cfg, **(fetched or {})}


def sample(text=SAMPLE, fetch=None, face=None, folder=None):
    """(wav bytes, face track) for the tuner's line, in his own voice. Fetched and aligned once,
    then kept in the cache, keyed by the line and the voice, so later runs start at once."""
    from . import mouth

    cfg = None
    if fetch is None:
        cfg = _settings()
    voice = "" if cfg is None else f"{cfg.get('tts_provider')}:{cfg.get('tts_voice_id')}:{cfg.get('speech_rate')}"
    # the aligner's own code is in the key, so a change to it never leaves the tuner on old cues
    aligner = hashlib.sha1(Path(mouth.__file__).read_bytes()).hexdigest()
    key = hashlib.sha1(f"{voice}\n{aligner}\n{text}".encode()).hexdigest()[:16]
    folder = folder or cache_dir()
    wav_file, track_file = folder / f"{key}.wav", folder / f"{key}.json"
    if wav_file.exists() and track_file.exists():
        try:
            return wav_file.read_bytes(), json.loads(track_file.read_text())
        except (OSError, ValueError):
            pass
    if fetch is None:
        from . import tts

        speaker = tts.Speaker(cfg, _Quiet(), output=object())
        wav = speaker._fetch(text, cfg)
    else:
        wav = fetch(text)
    if not wav:
        raise RuntimeError("no speech: the voice service gave nothing back")
    from . import interrupt

    # the same words a reply aligns, so a delay tuned here holds for replies
    track = (face or mouth.face_for)(interrupt.scrub(text), wav)
    folder.mkdir(parents=True, exist_ok=True)
    wav_file.write_bytes(wav)
    track_file.write_text(json.dumps(track))
    return wav, track


def _say(out, payload):
    out.write(json.dumps(payload) + "\n")
    out.flush()


def _until(at, clock=time.monotonic):
    while True:
        left = at - clock()
        if left <= 0:
            return
        time.sleep(min(left, 0.005))


def serve(out=sys.stdout, rounds=None, source=sample, output=None, stop=None):
    """Plays the sample over and over on his voice stream and says when each pass is heard and
    when it ends. Stops after `rounds` passes, when `stop` is set, or when stdin closes."""
    from . import mouth, playback

    stop = stop or threading.Event()
    if rounds is None:
        def watch():
            sys.stdin.read()
            stop.set()

        threading.Thread(target=watch, name="stdin", daemon=True).start()
    _say(out, {"event": "status", "text": "Getting his voice ready"})
    try:
        wav, track = source()
    except Exception as error:
        log.exception("the tuner's sample failed")
        _say(out, {"event": "error", "text": f"Could not get his voice: {error}"})
        return 1
    samples, rate = mouth._pcm(wav)
    output = output or playback.Output()
    output.prime(rate)
    passes = 0
    try:
        while not stop.is_set() and (rounds is None or passes < rounds):
            clip = output.play(samples, rate)
            if not clip.started.wait(3.0) or clip.heard_at is None:
                _say(out, {"event": "error", "text": "The sound never started"})
                return 1
            # sent before the sound, so a mouth tuned to lead it has its track in time
            _say(out, {"event": "heard", "start": round(clip.heard_wall, 4), "duration": round(clip.seconds, 3),
                       "cues": track.get("mouth") or [], "eyes": track.get("eyes") or []})
            while not clip.finished.wait(0.05):
                if stop.is_set():
                    output.cut(clip)
            if clip.ended_at is not None:
                _until(clip.ended_at)
            _say(out, {"event": "done"})
            passes += 1
            stop.wait(GAP_S)
    finally:
        output.close()
    return 0


def tuner_command(qs=None):
    return [qs or shutil.which("qs") or "qs", "-n", "-p", str(REPO / "presence" / "tuner.qml")]


def tuner_open():
    return subprocess.run(["pgrep", "-f", str(REPO / "presence" / "tuner.qml")],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def open_tuner():
    """Opens the tuner window on this desktop and returns at once. One tuner at a time."""
    command = tuner_command()
    if tuner_open():
        print("The tuner is already open")
        return 0
    if not shutil.which(command[0]):
        print("quickshell (qs) is not installed, the tuner needs it", file=sys.stderr)
        return 1
    subprocess.Popen(command, cwd=REPO, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    print(f"Tuner open. Saved delay: {delay_ms():+d} ms ({config_path()})")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else "open"
    if command == "serve":
        logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        return serve()
    if command == "open":
        return open_tuner()
    if command == "show":
        print(f"{delay_ms():+d} ms")
        return 0
    if command == "set" and len(argv) > 1:
        try:
            print(f"{save(float(argv[1])):+d} ms saved")
        except ValueError:
            print("set takes milliseconds, such as 40 or -20", file=sys.stderr)
            return 1
        return 0
    print("usage: diablo-ears mouth [open | show | set MS]", file=sys.stderr)
    return 0 if command in ("-h", "--help", "help") else 1


if __name__ == "__main__":
    sys.exit(main())
