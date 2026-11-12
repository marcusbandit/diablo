"""Speech to text for audio that reaches the hub, Telegram voice notes today.

transcription.backends is an ordered list of names, local first. Each name reads
transcription.<name>.base_url, .model, .api_key, .health and .timeout from settings; the
name cloud falls back to the older transcription_base_url, _model and _api_key keys. A
backend with health on is asked GET <base_url>/health first, with a short timeout and the
answer cached for a few seconds, so a sleeping banditbox costs under a second. A backend
that fails a request is marked down for the same few seconds and the next one in the list
takes the request.
"""

import logging
import threading
import time

import httpx

from . import settings, usage

log = logging.getLogger("diablo.stt")

HEALTH_TTL = 5.0
HEALTH_TIMEOUT = 0.8
CONNECT_TIMEOUT = 2.0
WARN_EVERY = 600.0
BACKOFF_MAX = 300.0
ERRORS = (httpx.HTTPError, httpx.InvalidURL, ValueError)
LEGACY = {"base_url": "transcription_base_url", "api_key": "transcription_api_key",
          "model": "transcription_model"}
CONTENT_TYPES = {"ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "wav": "audio/wav",
                 "mp3": "audio/mpeg", "m4a": "audio/mp4", "webm": "audio/webm", "flac": "audio/flac"}

_health = {}
_last = {"backend": "", "at": 0.0}
_refreshing = False
_warned = {}
_fails = {}
_lock = threading.Lock()


class TranscriptionError(RuntimeError):
    pass


def _values():
    return {key: value for key, value in settings.get_all().items() if key.startswith("transcription")}


def _get(values, key):
    return values.get(key) or settings.DEFAULTS.get(key, "")


def order(values=None):
    raw = _get(values if values is not None else _values(), "transcription.backends")
    return [name.strip() for name in str(raw).split(",") if name.strip()]


def backend(name, values=None):
    values = values if values is not None else _values()

    def pick(field):
        value = _get(values, f"transcription.{name}.{field}")
        if not value and name == "cloud" and field in LEGACY:
            value = _get(values, LEGACY[field])
        return str(value or "").strip()

    try:
        timeout = float(pick("timeout") or 120)
    except ValueError:
        timeout = 120.0
    return {
        "name": name,
        "base_url": pick("base_url").rstrip("/"),
        "api_key": pick("api_key"),
        "model": pick("model"),
        "checked": pick("health").lower() in ("on", "true", "1", "yes"),
        "timeout": timeout,
    }


def configured(values=None):
    values = values if values is not None else _values()
    found = (backend(name, values) for name in order(values))
    return [b for b in found if b["base_url"] and b["model"] and b["api_key"]]


def available():
    return bool(configured())


def _cache_key(b):
    return (b["name"], b["base_url"])


def _mark(b, ok, now=None, ttl=HEALTH_TTL):
    with _lock:
        _health[_cache_key(b)] = (ok, now if now is not None else time.monotonic(), ttl)


def _cached(b):
    with _lock:
        return _health.get(_cache_key(b))


def _fresh(cached, now=None):
    return bool(cached) and (now if now is not None else time.monotonic()) - cached[1] < cached[2]


def _json(response):
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("answer is not a JSON object")
    return body


def healthy(b):
    if not b["checked"]:
        return True
    now = time.monotonic()
    cached = _cached(b)
    if _fresh(cached, now):
        return cached[0]
    try:
        response = httpx.get(f"{b['base_url']}/health", headers=_auth(b), timeout=HEALTH_TIMEOUT)
        if response.status_code in (401, 403):
            _refused(b, response.status_code)
        ok = response.status_code == 200 and bool(_json(response).get("ok", False))
    except ERRORS:
        ok = False
    _mark(b, ok, now)
    return ok


def _refused(b, code):
    now = time.monotonic()
    with _lock:
        last = _warned.get(b["name"], -WARN_EVERY)
        if now - last < WARN_EVERY:
            return
        _warned[b["name"]] = now
    log.warning("stt backend %s refused the key (%s); set transcription.%s.api_key to its token",
                b["name"], code, b["name"])


def _hard(error):
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in (401, 403, 502, 503, 504)
    return not isinstance(error, ValueError)


def _failed(b, error):
    with _lock:
        count = _fails.get(_cache_key(b), 0) + 1
        _fails[_cache_key(b)] = count
    if b["checked"] and (_hard(error) or count >= 2):
        _mark(b, False, ttl=min(HEALTH_TTL * 2 ** (count - 1), BACKOFF_MAX))


def _served(b):
    with _lock:
        _fails.pop(_cache_key(b), None)


def _language(value):
    code = str(value or "en").strip().lower()
    return code if len(code) == 2 and code.isalpha() else "en"


def _auth(b):
    return {"Authorization": f"Bearer {b['api_key']}"} if b["api_key"] else {}


def _content_type(filename):
    return CONTENT_TYPES.get(filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def _post(b, data, filename):
    response = httpx.post(
        f"{b['base_url']}/audio/transcriptions",
        headers=_auth(b),
        files={"file": (filename, data, _content_type(filename))},
        data={"model": b["model"]},
        timeout=httpx.Timeout(b["timeout"], connect=min(CONNECT_TIMEOUT, b["timeout"])),
    )
    try:
        body = _json(response) if response.is_success else {}
    except ValueError:
        body = None
    model = str((body or {}).get("model") or b["model"])
    paid = usage.service_for(b["base_url"]) in usage.SERVICES
    usage.record_transcription(b["base_url"], model, data, filename, response.headers,
                               ok=response.is_success and body is not None, service=None if paid else b["name"])
    response.raise_for_status()
    if body is None:
        raise ValueError("answer is not a JSON object")
    return str(body.get("text", "")).strip(), _language(body.get("language")), model


def transcribe(data, filename):
    skipped = []
    empty = None
    for b in configured():
        if not healthy(b):
            skipped.append(f"{b['name']} unhealthy")
            continue
        start = time.monotonic()
        try:
            text, language, model = _post(b, data, filename)
        except ERRORS as error:
            _failed(b, error)
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (401, 403):
                _refused(b, error.response.status_code)
            skipped.append(f"{b['name']} failed ({type(error).__name__})")
            continue
        took = time.monotonic() - start
        _served(b)
        if not text:
            empty = empty or (b, language, model, took)
            skipped.append(f"{b['name']} heard nothing")
            continue
        return _done(b, text, language, model, took, skipped)
    if empty:
        b, language, model, took = empty
        return _done(b, "", language, model, took, skipped)
    reason = "; ".join(skipped) or "no transcription backend is set up"
    log.warning("stt failed on every backend: %s", reason)
    raise TranscriptionError(reason)


def _done(b, text, language, model, took, skipped):
    with _lock:
        _last.update(backend=b["name"], at=time.time())
    note = f", after {'; '.join(skipped)}" if skipped else ""
    log.info("stt served by %s (%s) in %.2fs%s", b["name"], model, took, note)
    return text, language


def _known(b):
    if not b["checked"]:
        return True
    cached = _cached(b)
    return cached[0] if cached else None


def _refresh(backends):
    global _refreshing
    try:
        for b in backends:
            healthy(b)
    finally:
        with _lock:
            _refreshing = False


def _view(wait):
    global _refreshing
    values = _values()
    backends = configured(values)
    if wait:
        for b in backends:
            healthy(b)
    elif any(b["checked"] and not _fresh(_cached(b)) for b in backends):
        with _lock:
            start, _refreshing = not _refreshing, True
        if start:
            threading.Thread(target=_refresh, args=(backends,), daemon=True).start()
    rows = [{"name": b["name"], "model": b["model"], "healthy": _known(b), "checked": b["checked"]}
            for b in backends]
    first = next((row["name"] for row in rows if row["healthy"]), "")
    return rows, first, order(values)


def last_backend():
    with _lock:
        return _last["backend"]


def active():
    try:
        return _view(False)[1]
    except Exception:
        log.exception("stt status failed")
        return ""


def status(wait=False):
    rows, first, names = _view(wait)
    with _lock:
        last = dict(_last)
    return {"order": names, "active": first, "backends": rows, "last": last}
