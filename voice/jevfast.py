"""Jev called straight from banditbox, skipping the trip through the hub on the Pi.

The questions and the rules that turn Jev's answer into an act are the hub's own
(hub/jev.py, hub/route.py), imported here so the two can never drift apart. Their settings
come from the hub's live settings that the ears already fetch each minute, and each call is
reported back to the hub's usage log. A line about a T3 thread still goes to the hub, which
keeps the thread list.
"""

import logging
import threading
import time

from . import hubclient

log = logging.getLogger("diablo.jevfast")

_bound = {"settings": None, "cfg": None}
# The ears' own warm copy of the hub's thread list, so a thread line needs no hub trip.
THREADS_TTL_S = 30
_threads = {"at": 0.0, "rows": [], "busy": False}


def refresh_threads():
    """Fetches the thread list from the hub on a thread, when the copy is stale."""
    settings = _bound["settings"]
    if settings is None or _threads["busy"] or time.time() - _threads["at"] < THREADS_TTL_S:
        return
    _threads["busy"] = True

    def run():
        try:
            import httpx

            cfg = settings.get()
            response = httpx.get(f"{cfg['hub_url'].rstrip('/')}/v1/voice/threads",
                                 headers={"X-Diablo-Token": cfg.get("hub_token", "")}, timeout=10)
            response.raise_for_status()
            rows = response.json().get("threads")
            if isinstance(rows, list):
                _threads["rows"] = rows
                _threads["at"] = time.time()
        except Exception as error:
            log.debug("thread list from the hub failed: %s", error)
        finally:
            _threads["busy"] = False

    threading.Thread(target=run, daemon=True, name="voice-threads").start()


def bind(settings):
    """Points the hub modules at the ears' copy of the hub settings. Called once at start."""
    from hub import jev, route, settings as hub_settings, usage

    _bound["settings"] = settings

    def get(key):
        live = settings.get() if settings is not None else {}
        value = live.get(key)
        return hub_settings.DEFAULTS.get(key, "") if value in (None, "") else value

    def record(service, model="", unit="calls", units=1, *args, origin="hub", ok=True, **kwargs):
        cfg = settings.get() if settings is not None else {}
        threading.Thread(target=hubclient.report_usage,
                         args=(cfg, service, model, unit, units, f"ears {origin}", ok), daemon=True).start()

    hub_settings.get = get
    usage.record = record
    threading.Thread(target=jev.warm, daemon=True, name="jev-warm").start()
    refresh_threads()
    return jev, route


def ask(state, questions, timeout=None, origin="jev"):
    """One Jev call with the hub's questions format, or None when the ears are not bound,
    there is no key, or Jev fails."""
    if _bound["settings"] is None:
        return None
    from hub import jev

    key = jev._key()
    if not key:
        return None
    return jev.ask(state, questions, key, timeout, origin=origin)


def control(text, desktop):
    """{"act", "jev_ms", "choice", "confidence", "answer"} for a speech control line, or None
    when this path cannot take it: not bound, or a thread line while the ears have no thread
    list yet, which the hub then answers."""
    if _bound["settings"] is None:
        return None
    from hub import jev, route

    refresh_threads()
    if jev.wants_threads(text, desktop):
        if not _threads["rows"]:
            return None
        desktop = {**(desktop or {}), "threads": list(_threads["rows"])}
    started = time.monotonic()
    answer = jev.control(text, desktop or {})
    if answer is None:
        return None
    act = route.desktop_action(answer, text, desktop or {})
    return {"act": act, "jev_ms": answer.get("ms"), "hub_ms": round((time.monotonic() - started) * 1000),
            "choice": answer.get("desktop"), "confidence": answer.get("desktop_confidence"), "answer": answer,
            "path": "direct"}
