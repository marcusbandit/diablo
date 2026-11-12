import logging
import socket
import threading

import httpx

log = logging.getLogger("diablo.hub")


def _headers(cfg):
    return {"X-Diablo-Token": cfg.get("hub_token", "")}


def post_message(cfg, source, role, text, meta=None):
    try:
        response = httpx.post(
            f"{cfg['hub_url'].rstrip('/')}/v1/messages",
            headers=_headers(cfg),
            json={"source": source, "role": role, "text": text, "meta": meta or {}},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hub post failed: %s", error)
        return False


def report_ears(cfg, state, detail=""):
    try:
        response = httpx.post(
            f"{cfg['hub_url'].rstrip('/')}/v1/ears/report",
            headers=_headers(cfg),
            json={"state": state, "detail": detail, "host": socket.gethostname()},
            timeout=5,
        )
        response.raise_for_status()
        return True
    except (httpx.HTTPError, ValueError) as error:
        log.debug("ears report failed: %s", error)
        return False


def get_context(cfg, limit=20):
    base = cfg["hub_url"].rstrip("/")
    try:
        messages = httpx.get(
            f"{base}/v1/messages", headers=_headers(cfg), params={"limit": limit}, timeout=10
        )
        messages.raise_for_status()
        facts = httpx.get(
            f"{base}/v1/facts", headers=_headers(cfg), params={"limit": 10}, timeout=10
        )
        facts.raise_for_status()
        return {
            "messages": messages.json().get("messages", []),
            "facts": facts.json().get("facts", []),
        }
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hub context failed: %s", error)
        return {"messages": [], "facts": []}


def get_settings(cfg):
    try:
        response = httpx.get(
            f"{cfg['hub_url'].rstrip('/')}/v1/settings", headers=_headers(cfg), timeout=10
        )
        response.raise_for_status()
        return response.json().get("settings", {})
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hub settings failed: %s", error)
        return {}


def add_fact(cfg, text, tags=None):
    try:
        response = httpx.post(
            f"{cfg['hub_url'].rstrip('/')}/v1/facts",
            headers=_headers(cfg),
            json={"text": text, "tags": tags or []},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hub fact failed: %s", error)
        return False


def set_setting(cfg, key, value):
    try:
        response = httpx.put(
            f"{cfg['hub_url'].rstrip('/')}/v1/settings/{key}",
            headers=_headers(cfg),
            json={"value": str(value)},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hub set failed: %s", error)
        return False


def report_usage(cfg, service, model, unit, units, origin, ok=True):
    """Tells the hub about one paid call made here, on a thread so speech never waits on it."""
    body = {"service": service, "model": model, "unit": unit, "units": units, "origin": origin, "ok": ok}

    def send():
        try:
            httpx.post(f"{cfg['hub_url'].rstrip('/')}/v1/usage", headers=_headers(cfg), json=body, timeout=5)
        except (httpx.HTTPError, KeyError, ValueError) as error:
            log.debug("usage report failed: %s", error)

    threading.Thread(target=send, name="usage-report", daemon=True).start()
