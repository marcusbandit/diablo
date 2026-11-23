import pathlib
import shutil
import struct
import subprocess
import tempfile

import httpx

from . import settings, usage

CARTESIA_BASE = "https://api.cartesia.ai"

def _cartesia(text, language="en", origin="voice note"):
    speed = float(settings.get("speech_rate") or 1.0) if language.startswith("en") else 1.0
    response = httpx.post(
        f"{CARTESIA_BASE}/tts/bytes",
        headers={"X-API-Key": settings.get("tts_api_key"), "Cartesia-Version": "2026-08-14"},
        json={
            "model_id": settings.get("tts_model") or "sonic-latest",
            "transcript": text,
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


def _openai(text, origin="voice note"):
    response = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {settings.get('tts_api_key')}"},
        json={"model": "tts-1", "input": text, "voice": "onyx", "response_format": "wav"},
        timeout=60,
    )
    usage.record_speech("tts-1", text, ok=response.is_success, origin=origin)
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


def whole_wav(audio):
    """A wav with its real sizes. Cartesia streams one with placeholder sizes (0xFFFFFFFF) that
    Android's player refuses; the fmt chunk and every byte after the data header are kept."""
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    fmt, at = None, 12
    while at + 8 <= len(audio):
        kind, size = audio[at:at + 4], struct.unpack("<I", audio[at + 4:at + 8])[0]
        body = at + 8
        if kind == b"fmt ":
            fmt = audio[body:body + size]
        elif kind == b"data":
            if fmt is None:
                return audio
            data = audio[body:body + min(size, len(audio) - body)]
            data = data[:len(data) - len(data) % 2]
            head = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data))
            return b"RIFF" + struct.pack("<I", 4 + len(head) + len(data)) + b"WAVE" + head + data
        at = body + size + (size & 1)
    return audio


def speak(text, voice_note=False, language="en", origin="voice note"):
    provider = settings.get("tts_provider")
    if provider == "cartesia":
        audio = _cartesia(text, language, origin)
    elif provider == "openai":
        audio = _openai(text, origin)
    else:
        raise RuntimeError("no tts provider configured")
    if voice_note:
        return _to_opus(audio)
    return audio
