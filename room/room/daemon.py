"""The service: watch the desktop, pick a state, move the lamps, back off when a person takes over."""

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time

from . import color, config, context, hue, lamps, signals
from .holds import Holds

TICK_S = 1.0
LAMPS_EVERY_S = 600  # re-read the lamp list, for renames and new lamps
STATUS_EVERY_S = 5.0
NIGHT_FADE_MS = 20_000  # night warming moves slowly enough that nobody sees it happen
RETAKE_FADE_MS = 1500  # after a pause, a resume or a hold running out
GAP_S = 0.12  # between commands; the bridge takes about ten light commands a second
DEBUG = bool(os.environ.get("ROOM_DEBUG"))  # log every command sent


def fade_ms(frm, to):
    if to == "cinema":
        return 1500 if frm == "intermission" else 2500  # the house lights going down
    if frm == "cinema" and to == "intermission":
        return 1200
    if to == "bedtime":
        return 60_000
    if frm in (None, "away", "bedtime"):
        return 1500
    return 800


def log(kind, **fields):
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with config.LOG.open("a") as f:
        f.write(json.dumps({"ts": round(time.time(), 2), "kind": kind, **fields}) + "\n")


def read_float(path):
    try:
        return float(path.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0.0


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


class Daemon:
    def __init__(self):
        self.cfg = config.load()
        self.desktop = signals.Desktop()
        self.settler = context.Settler(self.cfg["settle_s"])
        self.holds = Holds()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.bridge = None
        self.lamps = []
        self.lamps_at = 0.0
        self.applied = {}  # lamp id -> Target.key() last sent
        self.targets = {}
        self.paused = False
        self.unfinished = 0  # fade of a scene change the bridge interrupted, finished next tick
        self.locked_since = None
        self.error = ""
        self.status_at = 0.0
        self.last_status = None

    # bridge and lamps

    def connect(self):
        host, key = config.bridge_credentials()
        if not host:
            self.error = "not paired with a Hue bridge yet: run `room setup`"
            return False
        self.bridge = hue.Bridge(host, key)
        return self.refresh_lamps()

    def refresh_lamps(self):
        try:
            rooms = self.bridge.rooms() if self.cfg.get("room") else []
            _, self.lamps = lamps.choose(self.bridge.lights(), rooms, self.cfg)
            self.lamps_at = time.time()
            self.error = "" if self.lamps else "no lamps match the config"
            return True
        except hue.BridgeError as exc:
            self.error = str(exc)
            return False

    def watch_bridge(self):
        """Event stream thread: every light update that room did not cause becomes a hold."""
        while not self.stop.is_set():
            bridge = self.bridge
            if bridge is None:
                self.stop.wait(5)
                continue
            try:
                for event in bridge.events(self.stop):
                    if event.get("type") != "update":
                        continue
                    for d in event.get("data", []):
                        if d.get("type") == "light":
                            self.on_light_update(d)
            except hue.BridgeError:
                pass
            # Reconnect at once: a hand change during a gap would never become a hold.
            self.stop.wait(0.2)

    def on_light_update(self, d):
        now = time.time()
        with self.lock:
            if self.paused or d["id"] not in {lamp.id for lamp in self.lamps}:
                return
            if DEBUG:
                log("update", lamp=self.name_of(d["id"]), data={k: d[k] for k in ("on", "dimming", "color", "color_temperature") if k in d})
            hold = self.holds.on_update(d["id"], d, now, self.settler.active)
            if hold:
                name = next((lamp.name for lamp in self.lamps if lamp.id == d["id"]), d["id"])
                log("hold", lamp=name, what=hold.what, state=self.settler.active)
                self.wake.set()

    # the loop

    def state_now(self, snap, now):
        state, reason = context.classify(snap, self.cfg["apps"])
        if state == "away":
            self.locked_since = self.locked_since or now
            bed = self.cfg["bedtime"]
            if (bed["enabled"] and lamps.in_window(now, bed["after"], bed["until"])
                    and now - self.locked_since >= bed["locked_min"] * 60):
                return "bedtime", "locked at bedtime, fading out"
        else:
            self.locked_since = None
        return state, reason

    def tick(self):
        now = time.time()
        if self.bridge is None or now - self.lamps_at > LAMPS_EVERY_S:
            if not (self.connect() if self.bridge is None else self.refresh_lamps()):
                self.write_status(now, force=True)
                return

        snap = self.desktop.snapshot(now)
        state, reason = self.state_now(snap, now)
        previous = self.settler.active
        changed = self.settler.feed(state, reason, now)
        if changed:
            log("state", state=self.settler.active, reason=self.settler.reason, was=previous)

        with self.lock:
            was_paused = self.paused
            self.paused = read_float(config.PAUSE) > now
            retake = False
            if config.CLEAR.exists() or (was_paused and not self.paused):
                config.CLEAR.unlink(missing_ok=True)
                self.holds.clear()
                self.applied.clear()
                retake = True
            for lid in self.holds.release(self.settler.active, now, self.cfg["hold"]["manual_min"]):
                self.applied.pop(lid, None)
                retake = True
                log("release", lamp=self.name_of(lid), state=self.settler.active)

        active = self.settler.active
        factor = lamps.night_factor(now, self.cfg["night"])
        self.targets = lamps.targets(active, self.cfg, self.lamps, factor)
        if not self.paused and self.targets:
            if changed:
                duration = fade_ms(previous, active)
            elif self.unfinished:
                duration = self.unfinished  # a scene change cut short by a bridge error keeps its fade
            else:
                duration = RETAKE_FADE_MS if retake else NIGHT_FADE_MS
            self.unfinished = 0 if self.apply(duration) else duration
        self.write_status(now, force=changed)

    def apply(self, duration):
        """Send each lamp its target. False when the bridge failed partway."""
        for lamp in self.lamps:
            target = self.targets.get(lamp.id)
            if target is None or self.applied.get(lamp.id) == target.key():
                continue
            with self.lock:
                # Checked per lamp under the lock: a hand change can land while earlier lamps are sent.
                if lamp.id in self.holds.held:
                    continue
                self.holds.note_sent(lamp.id, lamps.clamp(target, lamp), time.time())
            body = lamps.body(target, lamp, duration)
            if DEBUG:
                log("put", lamp=lamp.name, body=body)
            try:
                self.bridge.put_light(lamp.id, body)
                self.applied[lamp.id] = target.key()
                self.error = ""
            except hue.BridgeError as exc:
                self.error = str(exc)
                self.lamps_at = 0  # look again next tick
                return False
            time.sleep(GAP_S)
        return True

    def name_of(self, lid):
        return next((lamp.name for lamp in self.lamps if lamp.id == lid), lid)

    def write_status(self, now, force=False):
        holds = self.holds.held
        data = {
            "state": self.settler.active,
            "reason": self.settler.reason,
            "since": self.settler.since,
            "night": round(lamps.night_factor(now, self.cfg["night"]), 2),
            "paused_until": read_float(config.PAUSE) if self.paused else 0,
            "error": self.error,
            "lamps": [
                {
                    "name": lamp.name,
                    "id": lamp.id,
                    "target": target_json(self.targets.get(lamp.id)),
                    "hex": color.preview_hex(self.targets[lamp.id]) if lamp.id in self.targets else None,
                    "held": lamp.id in holds,
                    "held_what": holds[lamp.id].what if lamp.id in holds else "",
                    "held_since": holds[lamp.id].since if lamp.id in holds else 0,
                }
                for lamp in self.lamps
            ],
            "pid": os.getpid(),
        }
        if force or data != self.last_status or now - self.status_at >= STATUS_EVERY_S:
            self.last_status = data
            self.status_at = now
            write_json(config.STATUS, {**data, "updated": now})

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        signals.start_hyprland_watch(self.desktop, self.wake, self.stop)
        threading.Thread(target=self.watch_bridge, daemon=True, name="bridge").start()
        log("start", pid=os.getpid())
        welcome(self.desktop.env)
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # one bad tick must not take the room down
                self.error = f"{type(exc).__name__}: {exc}"
                log("error", error=self.error)
                self.write_status(time.time(), force=True)
            # A pending state settles on the next tick, so wake early only for events.
            self.wake.wait(TICK_S)
            self.wake.clear()
        log("stop")


def welcome(env):
    """Once, before the walkthrough has run: a notification saying room is on, which opens it.

    Goes over D-Bus with gdbus rather than notify-send, so a broken libnotify can't stop it.
    """
    if config.ONBOARDED.exists() or config.WELCOMED.exists() or not shutil.which("gdbus"):
        return
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.WELCOMED.touch()
    threading.Thread(target=_welcome, args=(env,), daemon=True, name="welcome").start()


def _welcome(env):
    dest = ["--session", "--dest", "org.freedesktop.Notifications"]
    try:
        monitor = subprocess.Popen(["gdbus", "monitor", *dest], stdout=subprocess.PIPE, text=True, env=env)
    except OSError:
        return
    threading.Timer(600, monitor.terminate).start()
    out = subprocess.run(
        ["gdbus", "call", *dest, "--object-path", "/org/freedesktop/Notifications",
         "--method", "org.freedesktop.Notifications.Notify", "room", "0", "room",
         "Your lights follow you now",
         "Fullscreen video dims the room, coding brightens it, and late nights go warm. "
         "The walkthrough shows each scene on your lamps.",
         "['setup', 'Open the walkthrough']", "{}", "30000"],
        capture_output=True, text=True, env=env).stdout
    match = re.search(r"uint32 (\d+)", out)
    if not match:
        monitor.terminate()
        return
    nid = match.group(1)
    for line in monitor.stdout:
        if "ActionInvoked" in line and f"(uint32 {nid}, 'setup')" in line:
            subprocess.Popen([shutil.which("room") or "room", "setup"], env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
        if "NotificationClosed" in line and f"(uint32 {nid}," in line:
            break
    monitor.terminate()


def target_json(target):
    if target is None:
        return None
    out = {"on": target.on}
    if target.on:
        out["bri"] = round(target.bri, 1)
        if target.mirek:
            out["k"] = round(color.kelvin(target.mirek) / 50) * 50  # mirek steps make 4300 K read 4292
        if target.xy:
            out["xy"] = list(target.xy)
    return out


def main():
    Daemon().run()
