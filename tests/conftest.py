import os
import sys
from pathlib import Path

# Tests never read this box's own values: no local.env, a made-up tailnet and home subnet.
os.environ["DIABLO_LOCAL_ENV"] = "/nonexistent/diablo-local.env"
os.environ["DIABLO_TAILNET"] = "tailnet.example.ts.net"
os.environ["DIABLO_HOME_SUBNET"] = "192.168.1.0/24"
os.environ["DIABLO_MAIL_ACCOUNTS"] = "gmail=marcus@example.com,rosado=rosado@example.com"
os.environ["DIABLO_MAIL_AGENT"] = "jorbis@example.com"

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import lockguard


@pytest.fixture(autouse=True)
def lock_state_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(lockguard, "STATE", tmp_path / "lockguard")
    monkeypatch.setattr(lockguard, "LOG", tmp_path / "lockguard" / "locked.log")


@pytest.fixture(autouse=True)
def no_haiku_in_tests(monkeypatch):
    monkeypatch.setenv("DIABLO_AUTOSHIP", "0")
    from hub import threads

    async def offline(self, text):
        raise RuntimeError("no claude in tests")

    monkeypatch.setattr(threads.Quick, "ask", offline)
    monkeypatch.setattr(threads, "executable", lambda: None)
    from hub import brains

    monkeypatch.setattr(brains.Lanes, "fill_soon", lambda self: None)
    brains._lanes.clear()


@pytest.fixture(autouse=True)
def mouth_off_the_live_orb(tmp_path, monkeypatch):
    """Speech in tests writes its mouth track to a temp file, never the one the orb watches,
    and follows loudness instead of loading the aligner model."""
    try:
        from voice import mouth
    except ImportError:
        return
    monkeypatch.setattr(mouth, "TRACK_FILE", tmp_path / "mouth.json")
    monkeypatch.setattr(mouth._aligner, "_ready", False)
    monkeypatch.setattr(mouth._aligner, "_retry_at", float("inf"))
