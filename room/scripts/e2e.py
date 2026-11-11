#!/usr/bin/env python3
"""End to end on the real lamps: drive the daemon from a fake desktop, read the bridge back.

Walks focus -> cinema -> intermission, takes a lamp over by hand, checks room leaves it
alone, gives it back, then puts every lamp back the way it found it. About 40 seconds.

usage: python3 scripts/e2e.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from room import color, config, hue, lamps  # noqa: E402
from room.cli import restore_body  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="room-e2e-"))
fake = tmp / "snapshot.json"
env = dict(os.environ, ROOM_FAKE_SNAPSHOT=str(fake), ROOM_STATE_DIR=str(tmp / "state"),
           ROOM_CONFIG_DIR=str(tmp / "config"), PYTHONUNBUFFERED="1", ROOM_DEBUG="1")
YT = {"name": "firefox.instance_1", "status": "Playing", "url": "https://www.youtube.com/watch?v=x", "title": "x"}
failures = []


def desktop(**snap):
    fake.write_text(json.dumps(snap))


def by_name(bridge):
    return {d["metadata"]["name"]: d for d in bridge.lights()}


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label} {detail}")
    if not cond:
        failures.append(label)


def settle(bridge, name, want, tol=3, wait=4.0):
    """Some bulbs (the filament one) report their level late; poll until it lands or time runs out."""
    end = time.time() + wait
    while True:
        bri = by_name(bridge)[name]["dimming"]["brightness"]
        if abs(bri - want) < tol or time.time() >= end:
            return bri
        time.sleep(0.4)


def status():
    try:
        return json.loads((tmp / "state" / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def main():
    host, key = config.bridge_credentials()
    bridge = hue.Bridge(host, key)
    before = bridge.lights()
    (tmp / "before.json").write_text(json.dumps(before))
    print(f"saved {len(before)} lamps to {tmp / 'before.json'}")
    desktop(cls="com.t3tools.T3Code", title="T3 Code")
    proc = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                             "from room import daemon; daemon.main()", str(ROOT)], env=env,
                            stdout=open(tmp / "daemon.log", "w"), stderr=subprocess.STDOUT)
    try:
        night = lamps.night_factor(time.time(), config.DEFAULTS["night"])
        cap = 100 - night * (100 - config.DEFAULTS["night"]["cap"])
        time.sleep(3)
        print("focus: T3 Code focused")
        s = status()
        check("state is focus", s.get("state") == "focus", s.get("reason", ""))
        for name in by_name(bridge):
            bri = settle(bridge, name, min(100, cap))
            check(f"{name} on near {min(100, cap):.0f} %", abs(bri - min(100, cap)) < 3, f"(is {bri})")

        print("cinema: Zen fullscreen, YouTube playing")
        desktop(cls="zen", title="x - YouTube", fullscreen=True, players=[YT])
        time.sleep(4.5)
        s = status()
        check("state is cinema", s.get("state") == "cinema", s.get("reason", ""))
        ls = by_name(bridge)
        check("panel off", not ls["Hue Surimu panel"]["on"]["on"])
        wood = ls["Wood lamp"]
        amber = color.hex_to_xy("#ff7a2e")
        xy = wood["color"]["xy"]
        check("wood lamp low amber", wood["on"]["on"] and wood["dimming"]["brightness"] < 12
              and abs(xy["x"] - amber[0]) < 0.04, f"(bri {wood['dimming']['brightness']}, xy {xy})")
        bri = settle(bridge, "The corner", 4, tol=1.5)
        check("corner at 4 %", abs(bri - 4) < 1.5, f"(is {bri})")

        print("intermission: paused")
        desktop(cls="zen", title="x - YouTube", fullscreen=True, players=[{**YT, "status": "Paused"}])
        time.sleep(4.0)
        s = status()
        check("state is intermission", s.get("state") == "intermission", s.get("reason", ""))
        ls = by_name(bridge)
        check("panel back on at 30 %", ls["Hue Surimu panel"]["on"]["on"]
              and abs(ls["Hue Surimu panel"]["dimming"]["brightness"] - 30) < 3)

        print("hand change: Wood lamp set to 60 % from outside")
        wood_id = ls["Wood lamp"]["id"]
        bridge.put_light(wood_id, {"dimming": {"brightness": 60}})
        time.sleep(2.0)
        s = status()
        held = [lamp for lamp in s.get("lamps", []) if lamp["held"]]
        check("Wood lamp held", [lamp["name"] for lamp in held] == ["Wood lamp"], str([lamp["held_what"] for lamp in held]))

        print("everyday: Discord focused; the held lamp must stay at 60 %")
        desktop(cls="vesktop", title="Discord")
        time.sleep(5.0)
        s = status()
        check("state is everyday", s.get("state") == "everyday")
        ls = by_name(bridge)
        check("wood lamp still 60 %", abs(ls["Wood lamp"]["dimming"]["brightness"] - 60) < 2,
              f"(is {ls['Wood lamp']['dimming']['brightness']})")
        check("panel at everyday 70 %", abs(ls["Hue Surimu panel"]["dimming"]["brightness"] - min(70, cap)) < 3)

        print("resume: room takes the lamp back")
        (tmp / "state" / "clear-holds").touch()
        time.sleep(2.5)
        ls = by_name(bridge)
        check("wood lamp back to everyday", abs(ls["Wood lamp"]["dimming"]["brightness"] - min(70, cap)) < 3,
              f"(is {ls['Wood lamp']['dimming']['brightness']})")
    finally:
        proc.terminate()
        proc.wait(5)
        for d in before:
            bridge.put_light(d["id"], restore_body(d))
            time.sleep(0.15)
        print(f"lamps restored. daemon log: {tmp / 'daemon.log'}")
    print("PASS" if not failures else f"FAILED: {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
