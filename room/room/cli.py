"""The `room` command."""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

from . import __version__, color, config, context, hue, lamps, signals

LABELS = {
    "focus": "Focus", "everyday": "Everyday", "watching": "Watching", "cinema": "Cinema",
    "intermission": "Intermission", "game": "Game", "call": "Call", "away": "Away", "bedtime": "Bedtime",
}
ABOUT = {
    "focus": "coding, a terminal, GitHub or docs: bright and cool",
    "everyday": "anything else: soft and warm",
    "watching": "video playing in a window: lower",
    "cinema": "fullscreen video playing: ceiling off, a low amber glow",
    "intermission": "fullscreen video paused: the house lights come up a little",
    "game": "a fullscreen game: dim and warm",
    "call": "a Discord call: an even light on your face",
}
DURATIONS = ("15m", "30m", "1h", "2h", "3h", "morning")


def tty():
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def dot(hex_color):
    if not hex_color or not tty():
        return "-"
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"\x1b[38;2;{r};{g};{b}m●\x1b[0m"


def dim(text):
    return f"\x1b[2m{text}\x1b[0m" if tty() else text


def ago(ts, now=None):
    s = max(0, int((now or time.time()) - ts))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60} min"


def clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def describe_target(t):
    if not t:
        return "left as it is"
    if not t.get("on"):
        return "off"
    parts = [f"{round(t['bri'])} %"]
    if t.get("k"):
        parts.append(f"{t['k']} K")
    if t.get("xy"):
        parts.append("colour")
    return "  ".join(parts)


def read_status():
    try:
        data = json.loads(config.STATUS.read_text())
    except (OSError, ValueError):
        return None
    pid = data.get("pid")
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            pass
    data["running"] = alive and time.time() - data.get("updated", 0) < 30
    return data


def parse_duration(text, cfg):
    text = text.strip().lower()
    if text in ("morning", "tomorrow", "tonight"):
        end = lamps.minutes(cfg["night"]["end"])
        now = datetime.now()
        target = now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp() - now.timestamp()
    total = 0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms]?)", text):
        total += float(amount) * {"h": 3600, "m": 60, "s": 1, "": 60}[unit]
    if total <= 0:
        raise SystemExit(f"room: '{text}' is not a duration; try 30m, 1h, 2h30m or morning")
    return total


# commands

def cmd_status(args):
    data = read_status()
    if args.json:
        print(json.dumps(data or {"running": False}))
        return 0
    if not data:
        print("room has not run yet. `room setup` walks you through it.")
        return 1
    now = time.time()
    state = data.get("state")
    head = LABELS.get(state, "Starting")
    if not data["running"]:
        head += dim("  (the service is not running: systemctl --user start room)")
    elif data.get("paused_until", 0) > now:
        head += f"  paused until {clock(data['paused_until'])}, the lamps are yours"
    elif data.get("since"):
        head += f" since {clock(data['since'])}"
    print(head)
    if data.get("reason"):
        print(f"  {data['reason']}")
    if data.get("night"):
        print(f"  night warmth {round(data['night'] * 100)} %")
    if data.get("error"):
        print(f"  problem: {data['error']}")
    width = max((len(lamp["name"]) for lamp in data.get("lamps", [])), default=0)
    for lamp in data.get("lamps", []):
        line = f"  {dot(lamp.get('hex'))} {lamp['name']:<{width}}  {describe_target(lamp.get('target'))}"
        if lamp.get("held"):
            line += dim(f"  yours: {lamp['held_what']} {ago(lamp['held_since'], now)} ago")
        print(line)
    if not config.ONBOARDED.exists():
        print(dim("\nFirst time? `room setup` shows each scene on your lamps and asks a few things."))
    return 0


def cmd_why(args):
    cfg = config.load()
    snap = signals.Desktop().snapshot()
    state, reason = context.classify(snap, cfg["apps"])
    factor = lamps.night_factor(time.time(), cfg["night"])
    print(f"Right now: {LABELS[state]}")
    print(f"  because {reason}")
    print()
    title = snap.title if len(snap.title) <= 60 else snap.title[:57] + "..."
    print(f"  window   {snap.cls or 'none'}{'  fullscreen' if snap.fullscreen else ''}  {dim(title)}")
    if snap.players:
        for p in snap.players:
            kind = "music" if p.music else p.label
            print(f"  player   {p.name}  {p.status}  {dim(kind)}")
    else:
        print("  player   none")
    print(f"  call     {snap.call or 'none'}")
    print(f"  lock     {'locked' if snap.locked else 'unlocked'}")
    n = cfg["night"]
    print(f"  night    {round(factor * 100)} %  {dim('warming ' + n['start'] + ' to ' + n['full'] + ', day again at ' + n['end'])}")
    data = read_status()
    if data and data["running"]:
        print()
        print(f"The service shows {LABELS.get(data.get('state'), 'nothing yet')}", end="")
        if data.get("state") != state:
            print(dim(f"; a change waits {cfg['settle_s']:g} s, leaving cinema 6 s, so alt-tab never flickers"), end="")
        print()
        for lamp in data.get("lamps", []):
            if lamp.get("held"):
                print(f"  {lamp['name']} is yours: {lamp['held_what']} {ago(lamp['held_since'])} ago. "
                      f"room takes it back when you switch activity, after {cfg['hold']['manual_min']} min at least.")
    elif not data or not data["running"]:
        print(dim("\nThe service is not running, so nothing moves the lamps."))
    return 0


def cmd_pause(args):
    cfg = config.load()
    seconds = parse_duration(args.duration or "1h", cfg)
    until = time.time() + seconds
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.PAUSE.write_text(f"{until:.0f}\n")
    print(f"Paused until {clock(until)}. The lamps are yours; `room resume` gives them back.")
    return 0


def cmd_resume(args):
    config.PAUSE.unlink(missing_ok=True)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.CLEAR.touch()
    print("room has the lamps again, including any you had changed by hand.")
    return 0


def connect():
    host, key = config.bridge_credentials()
    if not host:
        raise SystemExit("room is not paired with a Hue bridge yet. Run `room setup`.")
    return hue.Bridge(host, key)


def chosen_lamps(bridge, cfg):
    return lamps.choose(bridge.lights(), bridge.rooms() if cfg.get("room") else [], cfg)[1]


def restore_body(d):
    """A PUT body that puts a lamp back the way a v2 GET found it."""
    out = {"on": {"on": d["on"]["on"]}}
    if d["on"]["on"] and d.get("dimming"):
        out["dimming"] = {"brightness": d["dimming"]["brightness"]}
        ct = d.get("color_temperature") or {}
        if ct.get("mirek_valid") and ct.get("mirek"):
            out["color_temperature"] = {"mirek": ct["mirek"]}
        elif (d.get("color") or {}).get("xy"):
            out["color"] = {"xy": d["color"]["xy"]}
    out["dynamics"] = {"duration": 800}
    return out


def play_scene(bridge, scene, cfg, chosen, seconds, fade=800, out=print):
    """Show a scene on the real lamps for a few seconds, then hand them back."""
    before = {d["id"]: d for d in bridge.lights()}
    status = read_status()
    running = bool(status and status["running"])
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    old_pause = config.PAUSE.read_text() if config.PAUSE.exists() else None
    config.PAUSE.write_text(f"{time.time() + seconds + 30:.0f}\n")
    try:
        factor = lamps.night_factor(time.time(), cfg["night"])
        for lamp in chosen:
            target = lamps.targets(scene, cfg, [lamp], factor).get(lamp.id)
            if target:
                bridge.put_light(lamp.id, lamps.body(target, lamp, fade))
                time.sleep(0.12)
        out(f"{LABELS[scene]} on the lamps for {seconds:g} s...")
        time.sleep(seconds)
    finally:
        if old_pause is not None:
            config.PAUSE.write_text(old_pause)
        else:
            config.PAUSE.unlink(missing_ok=True)
        if running and old_pause is None:
            config.CLEAR.touch()  # the service puts the right scene back on its next tick
        else:
            for lamp in chosen:
                if lamp.id in before:
                    bridge.put_light(lamp.id, restore_body(before[lamp.id]))
                    time.sleep(0.12)
    return running


def cmd_try(args):
    cfg = config.load()
    if args.scene not in cfg["scenes"]:
        raise SystemExit(f"room: no scene called {args.scene}. Scenes: {', '.join(cfg['scenes'])}")
    bridge = connect()
    running = play_scene(bridge, args.scene, cfg, chosen_lamps(bridge, cfg), args.seconds)
    print("Back to what room was doing." if running else "Back the way they were.")
    return 0


def cmd_scenes(args):
    cfg = config.load()
    try:
        bridge = connect()
        chosen = chosen_lamps(bridge, cfg)
    except (SystemExit, hue.BridgeError):
        chosen = []
    for name in cfg["scenes"]:
        swatches = ""
        if chosen:
            ts = lamps.targets(name, cfg, chosen)
            swatches = " ".join(dot(color.preview_hex(ts[lamp.id])) for lamp in chosen) + "  "
        print(f"{swatches}{name:<13} {dim(ABOUT.get(name, ''))}")
    return 0


def cmd_lamps(args):
    cfg = config.load()
    bridge = connect()
    raw = bridge.lights()
    chosen = {lamp.id for lamp in chosen_lamps(bridge, cfg)}
    rooms = {lid: name for name, ids in bridge.rooms() for lid in ids}
    for d in raw:
        lamp = lamps.Lamp.from_v2(d)
        on = d["on"]["on"]
        bri = (d.get("dimming") or {}).get("brightness", 0)
        kind = "colour" if lamp.has_color else ("white" if lamp.mirek_min else "dimmable")
        state = f"on {round(bri)} %" if on else "off"
        mark = "uses" if lamp.id in chosen else dim("skips")
        print(f"  {mark}  {lamp.name:<20} {kind:<8} {state:<9} {dim(rooms.get(lamp.id, ''))}")
    return 0


def cmd_log(args):
    try:
        lines = config.LOG.read_text().splitlines()[-args.n * 3:]
    except OSError:
        print("Nothing logged yet.")
        return 0
    rows = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = datetime.fromtimestamp(e["ts"]).strftime("%a %H:%M:%S")
        kind = e.get("kind")
        if kind == "state":
            rows.append(f"{t}  {LABELS.get(e['state'], e['state']):<13} {dim(e.get('reason', ''))}")
        elif kind == "hold":
            rows.append(f"{t}  {'yours':<13} {e['lamp']} {e['what']}")
        elif kind == "release":
            rows.append(f"{t}  {'back':<13} {e['lamp']} is room's again")
        elif kind == "error":
            rows.append(f"{t}  {'problem':<13} {e.get('error', '')}")
    print("\n".join(rows[-args.n:]) or "Nothing logged yet.")
    return 0


def cmd_check(args):
    snap = signals.Desktop().snapshot()
    out = {k: getattr(snap, k) for k in ("cls", "title", "fullscreen", "call", "locked")}
    out["players"] = [{"name": p.name, "status": p.status, "url": p.url, "music": p.music} for p in snap.players]
    print(json.dumps(out, indent=1))
    return 0


def cmd_setup(args):
    from . import onboarding
    return onboarding.main(page=args.page)


def cmd_daemon(args):
    from . import daemon
    daemon.main()
    return 0


def cmd_names(args):
    cfg = config.load()
    values = {"scenes": list(cfg["scenes"]), "durations": list(DURATIONS)}.get(args.what, [])
    print("\n".join(values))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="room", description="The Hue lights follow what the desktop is doing.")
    parser.add_argument("--version", action="version", version=f"room {__version__}")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("status", help="what the lights are doing and why")
    p.add_argument("--json", action="store_true", help="everything as JSON, for the launcher plugin")
    p.set_defaults(fn=cmd_status)
    sub.add_parser("why", help="every signal room reads right now, and what it makes of them").set_defaults(fn=cmd_why)
    p = sub.add_parser("pause", help="leave the lamps alone for a while")
    p.add_argument("duration", nargs="?", help="30m, 1h, 2h30m or morning (default 1h)")
    p.set_defaults(fn=cmd_pause)
    sub.add_parser("resume", help="take the lamps back, including ones changed by hand").set_defaults(fn=cmd_resume)
    p = sub.add_parser("try", help="show a scene on the lamps for a few seconds")
    p.add_argument("scene")
    p.add_argument("seconds", nargs="?", type=float, default=8.0)
    p.set_defaults(fn=cmd_try)
    sub.add_parser("scenes", help="the scenes and what each one is for").set_defaults(fn=cmd_scenes)
    sub.add_parser("lamps", help="every lamp on the bridge and which ones room uses").set_defaults(fn=cmd_lamps)
    p = sub.add_parser("log", help="recent changes")
    p.add_argument("n", nargs="?", type=int, default=20)
    p.set_defaults(fn=cmd_log)
    p = sub.add_parser("setup", help="the first-run walkthrough: bridge, lamps, scenes, night")
    p.add_argument("--page", type=int, default=1, help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_setup)
    sub.add_parser("check", help="the raw signals as JSON").set_defaults(fn=cmd_check)
    sub.add_parser("daemon", help="run the service (systemd starts this)").set_defaults(fn=cmd_daemon)
    p = sub.add_parser("names", help=argparse.SUPPRESS)
    p.add_argument("what", choices=["scenes", "durations"])
    p.set_defaults(fn=cmd_names)
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        args.json = False
        return cmd_status(args)
    try:
        return args.fn(args)
    except hue.BridgeError as exc:
        print(f"room: {exc}", file=sys.stderr)
        return 1
