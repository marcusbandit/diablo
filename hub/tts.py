import logging
import pathlib
import shutil
import struct
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import pronunciation, saying, settings, usage

log = logging.getLogger("diablo.tts")

CARTESIA_BASE = "https://api.cartesia.ai"

def _cartesia(text, language="en", origin="voice note"):
    speed = float(settings.get("speech_rate") or 1.0) if language.startswith("en") else 1.0
    response = httpx.post(
        f"{CARTESIA_BASE}/tts/bytes",
        headers={"X-API-Key": settings.get("tts_api_key"), "Cartesia-Version": "2026-08-14"},
        json={
            "model_id": settings.get("tts_model") or "sonic-latest",
            "transcript": saying.for_speech(text),
            "voice": settings.get("tts_voice_id"),
            "speed": speed,
            "language": language,
            "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 24000},
        },
        timeout=60,
    )
    usage.record_speech(settings.get("tts_model") or "sonic-latest", text, ok=response.is_success, origin=origin)
    response.raise_for_status()
    return response.content


def _openai(text):
    response = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {settings.get('tts_api_key')}"},
        json={"model": "tts-1", "input": saying.for_speech(text), "voice": "onyx", "response_format": "wav"},
        timeout=60,
    )
    response.raise_for_status()
    return response.content


def _ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _to_opus(wav_bytes):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as source:
        source.write(wav_bytes)
        source_path = source.name
    target_path = source_path[:-4] + ".ogg"
    try:
        subprocess.run(
            [_ffmpeg(), "-y", "-i", source_path, "-c:a", "libopus", "-b:a", "32k", target_path],
            capture_output=True,
            timeout=60,
            check=True,
        )
        return pathlib.Path(target_path).read_bytes()
    finally:
        pathlib.Path(source_path).unlink(missing_ok=True)
        pathlib.Path(target_path).unlink(missing_ok=True)


def _pcm(audio):
    """(fmt chunk, sample bytes) of a wav, the data cut to what arrived and to whole frames, or None."""
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return None
    fmt, at = None, 12
    while at + 8 <= len(audio):
        kind, size = audio[at:at + 4], struct.unpack("<I", audio[at + 4:at + 8])[0]
        body = at + 8
        if kind == b"fmt ":
            fmt = audio[body:body + size]
        elif kind == b"data":
            if fmt is None:
                return None
            data = audio[body:body + min(size, len(audio) - body)]
            block = struct.unpack("<H", fmt[12:14])[0] if len(fmt) >= 14 else 2
            return fmt, data[:len(data) - len(data) % max(block, 1)]
        at = body + size + (size & 1)
    return None


def _wav(fmt, data):
    head = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data))
    return b"RIFF" + struct.pack("<I", 4 + len(head) + len(data)) + b"WAVE" + head + data


def whole_wav(audio):
    """A wav with its real sizes. Cartesia streams one with placeholder sizes (0xFFFFFFFF) that
    Android's player refuses; the fmt chunk and every byte after the data header are kept."""
    found = _pcm(audio)
    return _wav(*found) if found else audio


def joined_wav(clips):
    """One wav of the clips back to back, all in the first clip's format."""
    found = [part for part in (_pcm(clip) for clip in clips) if part]
    if not found:
        return clips[0] if clips else b""
    kept = [data for fmt, data in found if fmt == found[0][0]]
    if len(kept) < len(clips):
        log.warning("speech joined %d of %d clips, the rest were not wavs in one format", len(kept), len(clips))
    return _wav(found[0][0], b"".join(kept))


def _cartesia_runs(text, language, origin):
    """Listed words in their own language (hub/pronunciation.py), each run its own request."""
    runs = [(code, run.strip()) for code, run in pronunciation.segments(text, language) if run.strip()]
    if not runs:
        return _cartesia(text, language, origin)
    if len(runs) > 1 or runs[0][0] != language:
        log.info("speech in %d runs: %s", len(runs),
                 "; ".join(f"{code}: {run!r}" if code != language else f"{code}: {len(run)} chars" for code, run in runs))

    def render(run):
        code, words = run
        try:
            return _cartesia(words, code, origin)
        except httpx.HTTPStatusError as error:
            if code == language or error.response.status_code not in (400, 422):
                raise
            log.warning("cartesia refused language %s (%s), said in %s", code, error.response.status_code, language)
            return _cartesia(words, language, origin)

    if len(runs) == 1:
        return render(runs[0])
    with ThreadPoolExecutor(max_workers=min(len(runs), 3)) as pool:
        return joined_wav(list(pool.map(render, runs)))


def speak(text, voice_note=False, language="en", origin="voice note"):
    provider = settings.get("tts_provider")
    if provider == "cartesia":
        audio = _cartesia_runs(text, language, origin)
    elif provider == "openai":
        audio = _openai(text)
    else:
        raise RuntimeError("no tts provider configured")
    if voice_note:
        return _to_opus(audio)
    return audio
