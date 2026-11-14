import logging
import re
import threading
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

log = logging.getLogger("diablo.stt")

LOCAL_TIMEOUT_S = 5.0
LOCAL_CONNECT_S = 1.0
BACKOFF_START_S = 30.0
BACKOFF_MAX_S = 300.0
BUSY = (409, 503)
_down = {"until": 0.0, "fails": 0}
_down_lock = threading.Lock()


class Busy(httpx.HTTPError):
    pass


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


def transcribe_file(path, cfg, fallback=True):
    return transcribe(path, cfg, fallback)[0]


def transcribe(path, cfg, fallback=True):
    """(words, path) for a wav. stt_via gvoice asks the dictation daemon on this box, and when it
    fails, with fallback, sends the clip to the hub with local skipped, so Groq takes it. stt_via
    hub always sends it to the hub, which tries diablo-stt and then Groq. path names
    who transcribed it: local, or hub plus the hub's backend."""
    if str(cfg.get("stt_via") or "gvoice").strip().lower() == "hub":
        text, backend = transcribe_via_hub(path, cfg, with_backend=True)
        return text, f"hub {backend or 'unknown'}"
    started = time.monotonic()
    skipped = _local_down()
    if skipped and not fallback:
        raise httpx.ConnectError("gvoice is backed off after a failure")
    if not skipped:
        try:
            text = _gvoice(path, cfg)
        except (httpx.HTTPError, OSError, ValueError) as error:
            if not isinstance(error, Busy):
                _local_failed()
            if not fallback:
                raise
            reason = f"local failed ({type(error).__name__}: {error})"
        else:
            _local_served()
            if fallback:
                log.info("stt served by local in %.2fs", time.monotonic() - started)
            return text, "local"
    else:
        reason = "local backed off"
    text, backend = transcribe_via_hub(path, cfg, with_backend=True, skip="local")
    log.warning("stt served by hub %s in %.2fs, %s", backend or "unknown", time.monotonic() - started, reason)
    return text, f"hub {backend or 'unknown'}"


def _gvoice(path, cfg):
    token = Path(cfg["gvoice_token_path"]).read_text().strip()
    base = cfg["gvoice_base"].rstrip("/")
    try:
        read_s = float(cfg.get("stt_local_timeout_s") or LOCAL_TIMEOUT_S)
    except (TypeError, ValueError):
        read_s = LOCAL_TIMEOUT_S
    last_error = None
    for attempt in range(3):
        try:
            response = httpx.get(
                f"{base}/file/{path}",
                headers={"X-Gvoice-Token": token},
                timeout=httpx.Timeout(read_s, connect=LOCAL_CONNECT_S),
            )
            if response.status_code in BUSY:
                raise Busy(f"daemon busy: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            return str(data.get("text", "")).strip()
        except Busy as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.2)
    raise last_error


def _local_down():
    with _down_lock:
        return time.monotonic() < _down["until"]


def _local_failed():
    with _down_lock:
        _down["fails"] += 1
        wait = min(BACKOFF_START_S * 2 ** (_down["fails"] - 1), BACKOFF_MAX_S)
        _down["until"] = time.monotonic() + wait


def _local_served():
    with _down_lock:
        _down.update(until=0.0, fails=0)


def transcribe_via_hub(path, cfg, with_backend=False, skip=""):
    data = Path(path).read_bytes()
    headers = {"X-Diablo-Token": cfg.get("hub_token", ""), "Content-Type": "audio/wav",
               "X-Filename": Path(path).name}
    if skip:
        headers["X-Stt-Skip"] = skip
    for attempt in range(2):
        try:
            response = httpx.post(
                f"{cfg['hub_url'].rstrip('/')}/v1/stt",
                headers=headers,
                content=data,
                timeout=httpx.Timeout(45, connect=4),
            )
        except httpx.ConnectError:
            if attempt:
                raise
            time.sleep(0.5)
            continue
        response.raise_for_status()
        body = response.json()
        text = str(body.get("text", "")).strip()
        return (text, str(body.get("backend") or "")) if with_backend else text


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
