#!/usr/bin/env python3
"""Relay a finished worker task report to the hub; the hub pushes it to Telegram.

Called by oc-think.sh reap with the task id and name as arguments, the report on stdin.
Best effort by design: a missed push never breaks the task itself.
"""

import json
import pathlib
import sys
import tomllib
import urllib.error
import urllib.request

STATE = pathlib.Path.home() / ".local/state/diablo/tasks"


def main():
    tid, name = sys.argv[1], sys.argv[2]
    report = sys.stdin.read().strip()
    if not report:
        try:
            report = (STATE / f"{tid}.out").read_text(errors="replace").strip()
        except OSError:
            report = ""
    text = f"{name} is finished. {report[:3500]}" if report else f"{name} finished with no report."
    cfg = tomllib.loads((pathlib.Path.home() / ".config/diablo/config.toml").read_text())
    url = str(cfg.get("hub_url") or "").rstrip("/")
    token = str(cfg.get("hub_token") or "")
    if not url or not token:
        return
    request = urllib.request.Request(
        f"{url}/v1/notify",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json", "X-Diablo-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            pass
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        pass


if __name__ == "__main__":
    main()
