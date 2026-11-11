import re
import time
import wave
from pathlib import Path

import httpx
import numpy as np

HALLUCINATION = re.compile(
    r"^(?:thank you(?: for watching)?|thanks for watching|bye bye?)\s*[.!?]*$",
    re.IGNORECASE,
)

_dictation_cache = {"checked": 0.0, "value": False}


def write_wav(audio, sample_rate, path):
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def is_noise(text):
    text = text.strip()
    return len(text) < 2 or bool(HALLUCINATION.match(text))


def transcribe_file(path, cfg):
    token = Path(cfg["gvoice_token_path"]).read_text().strip()
    base = cfg["gvoice_base"].rstrip("/")
    last_error = None
    for attempt in range(3):
        try:
            response = httpx.get(
                f"{base}/file/{path}",
                headers={"X-Gvoice-Token": token},
                timeout=120,
            )
            if response.status_code in (409, 503):
                raise httpx.HTTPError(f"daemon busy: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            return str(data.get("text", "")).strip()
        except httpx.HTTPError as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.2)
    raise last_error


def dictation_status(cfg, timeout=1.0):
    """The dictation daemon's /status. Raises OSError when it is not set up or not up."""
    token_path = Path(cfg.get("gvoice_token_path", ""))
    if not token_path.is_file():
        raise FileNotFoundError(f"no gvoice token at {token_path}")
    response = httpx.get(f"{cfg['gvoice_base'].rstrip('/')}/status",
                         headers={"X-Gvoice-Token": token_path.read_text().strip()}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def is_dictating(cfg, ttl=1.0):
    now = time.monotonic()
    if now - _dictation_cache["checked"] < ttl:
        return _dictation_cache["value"]
    try:
        token = Path(cfg["gvoice_token_path"]).read_text().strip()
        base = cfg["gvoice_base"].rstrip("/")
        response = httpx.get(
            f"{base}/status",
            headers={"X-Gvoice-Token": token},
            timeout=2,
        )
        response.raise_for_status()
        value = bool(response.json().get("listening", False))
    except Exception:
        value = False
    _dictation_cache["checked"] = now
    _dictation_cache["value"] = value
    return value
