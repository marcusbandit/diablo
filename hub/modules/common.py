import time


class ActFailed(RuntimeError):
    pass


def notice(text, **extra):
    return {"kind": "notice", "text": text, **extra}


def unavailable(text, **extra):
    """A source that is down: the app draws it as a failure with a Retry that re-reads the module."""
    return notice(text, **{"tone": "fail", "retry": True, **extra})


class Cache:
    def __init__(self, seconds, keep_none=False):
        self.seconds = seconds
        self.keep_none = keep_none
        self.value = None
        self.at = 0.0
        self.ready = False

    def get(self, make):
        now = time.monotonic()
        if not self.ready or now - self.at > self.seconds:
            self.value = make()
            self.at = now
            self.ready = self.keep_none or self.value is not None
        return self.value

    def clear(self):
        self.value = None
        self.ready = False
