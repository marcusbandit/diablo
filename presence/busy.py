"""The hub's running turns, one JSON line per change, for the orb beside the cursor.

shell.qml runs this and reads its stdout. It opens /v1/events, asks GET /v1/busy, and prints
every `busy` event as {"turns": [...]}, asking /v1/busy again every half minute so a missed end
can not hold the orb up; a keepalive from the hub prints {"alive": true},
so the orb can tell a quiet hub from a lost one. When the hub cannot be reached it prints
{"turns": [], "lost": true} and tries again, so a dropped link never leaves the orb busy.

Standard library only: the system python runs it, no venv.
"""

import json
import os
import sys
import threading
import time
import tomllib
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")) / "diablo" / "config.toml"
PING_S = 10
RETRY_S = 3
RECHECK_S = 30


def config():
    cfg = {"hub_url": "http://diablo-pi:8755", "hub_token": ""}
    try:
        with open(CONFIG, "rb") as handle:
            data = tomllib.load(handle)
        cfg.update({key: data[key] for key in cfg if key in data})
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return cfg


LOCK = threading.Lock()


def say(body):
    with LOCK:
        sys.stdout.write(json.dumps(body) + "\n")
        sys.stdout.flush()


def shown(event):
    return {"turns": [{"words": turn.get("words") or "thinking", "origin": turn.get("origin", ""), "since": turn.get("since", 0)}
                      for turn in event.get("turns") or [] if isinstance(turn, dict)]}


def ask(base, headers):
    with urllib.request.urlopen(urllib.request.Request(f"{base}/v1/busy", headers=headers), timeout=10) as reply:
        say(shown(json.loads(reply.read())))


def recheck(base, headers, done):
    while not done.wait(RECHECK_S):
        try:
            ask(base, headers)
        except Exception as error:
            sys.stderr.write(f"busy feed: {error}\n")


def follow(cfg):
    base = cfg["hub_url"].rstrip("/")
    headers = {"X-Diablo-Token": cfg["hub_token"]}
    request = urllib.request.Request(f"{base}/v1/events?ping={PING_S}", headers={**headers, "Accept": "text/event-stream"})
    done = threading.Event()
    try:
        with urllib.request.urlopen(request, timeout=PING_S * 3) as stream:
            ask(base, headers)
            threading.Thread(target=recheck, args=(base, headers, done), daemon=True).start()
            for raw in stream:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith(":"):
                    say({"alive": True})
                elif line.startswith("data:"):
                    try:
                        event = json.loads(line[5:])
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("kind") == "busy":
                        say(shown(event))
    finally:
        done.set()


def main():
    while True:
        try:
            follow(config())
        except Exception as error:
            sys.stderr.write(f"busy feed: {error}\n")
        say({"turns": [], "lost": True})
        time.sleep(RETRY_S)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
