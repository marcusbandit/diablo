"""Live activity feed: what he is doing right now, for the dashboard."""

import collections
import threading
import time

_lock = threading.Lock()
_events = collections.deque(maxlen=60)


def log_event(kind, text):
    with _lock:
        _events.append({"t": time.time(), "kind": kind, "text": str(text)[:400]})


def recent(n=40):
    with _lock:
        return list(_events)[-n:]
