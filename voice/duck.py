"""Other sound goes quiet while Marcus talks with Diablo, and comes back after.

From the moment the ears listen until a few seconds after the exchange ends, every other
app's stream (a PipeWire sink input: a video in the browser, Spotify, a call) is turned down
to `level` percent of its own volume. His master volume is never touched. Streams that start
while the conversation is on are turned down within a fraction of a second, so a video he
starts mid-exchange never plays over him. Diablo's own voice and chimes are never touched:
a stream is his when it carries his name, or when its process id is the ears' own. The pid
is what holds on a box whose ALSA default goes through the PulseAudio plugin, where the voice
once showed up as "ALSA plug-in [python3.12]" and was turned down as he spoke (2026-11-16).
Children do not count: the ears start real apps, and those are turned down like any other.

When it ends, each stream gets the exact volume it had, but only where nobody moved it since:
a volume he changed while it was down stays where he put it.

WirePlumber remembers each app's last volume. A stream that closes while turned down leaves
its app saved at the low level, and the app's next stream would start quiet (pw-play was left
at 0.000064 that way on 2026-11-11). Such an app is an orphan here: the next stream of it that
starts at the low level gets the old volume back, which also mends the saved one.

What is turned down, and the orphans, are written to ~/.local/state/diablo/ducked.json, so a
crash gives everything back on the next start and a stop gives it back on the way out.

Settings, in ~/.config/diablo/duck.json, edited from the central card's Settings:
    {"on": true, "level": 20}

"Don't turn it down" keeps the sound up for the rest of the conversation: suspend().
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("diablo.duck")

CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "diablo" / "duck.json"
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "diablo" / "ducked.json"
DEFAULTS = {"on": True, "level": 20, "linger_s": 5.0}
LEVELS = (10, 15, 20, 30)
POLL_S = 0.25
IDLE_POLL_S = 2.0
SLACK = 700
DUCK_STEPS, DUCK_SECONDS = 3, 0.09
RESTORE_STEPS, RESTORE_SECONDS = 6, 0.3
SUSPEND_IDLE_S = 600.0
ORPHAN_DAYS = 14
OWN = ("diablo", "pw-play")
ALL = "all"


def settings(path=None):
    path = path or CONFIG_FILE
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    if isinstance(raw.get("on"), bool):
        out["on"] = raw["on"]
    try:
        out["level"] = max(0, min(100, int(round(float(raw.get("level", out["level"]))))))
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        out["linger_s"] = max(0.0, min(60.0, float(raw.get("linger_s", out["linger_s"]))))
    except (TypeError, ValueError, OverflowError):
        pass
    return out


def save_settings(changes, path=None):
    path = Path(path or CONFIG_FILE)
    current = settings(path)
    if isinstance(changes.get("on"), bool):
        current["on"] = changes["on"]
    if changes.get("level") is not None:
        current["level"] = changes["level"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current) + "\n")
    tmp.replace(path)
    return settings(path)


class Pactl:
    """The sink inputs, read and set through pactl."""

    @staticmethod
    def _run(cmd):
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def streams(self):
        """The sink inputs, or None when pactl did not answer: an empty list would read as every
        stream gone."""
        out = self._run(["pactl", "-f", "json", "list", "sink-inputs"])
        if out is None:
            return None
        try:
            raw = json.loads(out or "[]")
        except ValueError:
            return None
        out = []
        for item in raw if isinstance(raw, list) else []:
            props = item.get("properties") or {}
            out.append({
                "index": item.get("index"),
                "app": str(props.get("application.name") or ""),
                "binary": str(props.get("application.process.binary") or ""),
                "node": str(props.get("node.name") or ""),
                "pid": _pid(props.get("application.process.id")),
                "title": str(props.get("media.name") or ""),
                "volume": [int(ch.get("value", 0)) for ch in (item.get("volume") or {}).values()],
            })
        return out

    def set_volume(self, index, values):
        self._run(["pactl", "set-sink-input-volume", str(index), *[str(int(round(v))) for v in values]])


def _pid(value):
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def mine(stream, pid=None):
    """A stream the ears' own process plays."""
    return _pid(stream.get("pid")) == (os.getpid() if pid is None else pid)


def own(stream, pid=None):
    """Diablo's voice and chimes: application name Diablo (or a bare pw-play), his voice node,
    or a stream from the ears' own process. pw-play names every node it makes pw-play, so the
    node name only counts when it is his."""
    if str(stream.get("app") or "").lower().startswith(OWN) or str(stream.get("node") or "").lower().startswith("diablo"):
        return True
    return mine(stream, pid)


def app_key(stream):
    return (stream.get("app") or stream.get("binary") or "unknown").lower()


def near(a, b):
    return len(a) == len(b) and len(a) > 0 and all(abs(x - y) <= SLACK for x, y in zip(a, b))


def lowered(volume, level):
    return [int(round(v * level / 100)) for v in volume]


_SOUND = r"(?:the\s+)?(?:sound|volume|music|video|audio|song|stream)"
_ANY = rf"(?:it|that|this|{_SOUND})"
_NOT = r"(?:don'?t|do\s+not|stop|quit|no\s+more)"
KEEP_UP = re.compile(
    rf"\b{_NOT}\s+(?:turning|turn)\s+{_ANY}\s+down\b"
    rf"|\b{_NOT}\s+(?:turning|turn)\s+down\s+{_ANY}\b"
    rf"|\b{_NOT}\s+(?:ducking|duck)\b"
    rf"|\b{_NOT}\s+(?:dimming|dim|lowering|lower|muting|mute)\s+{_SOUND}\b"
    rf"|\bkeep\s+{_SOUND}\s+(?:up|loud|on)\b"
    rf"|\bleave\s+{_SOUND}\s+(?:alone|up|be|loud|as\s+it\s+is)\b",
    re.I,
)


FILLER = re.compile(r"\b(?:diablo|please|just|now|ok(?:ay)?|and|so|hey|for\s+now|this\s+time|while\s+we\s+talk|thanks?|thank\s+you)\b", re.I)


def wants_sound_up(text):
    """"Don't turn it down", "stop ducking", "keep the music up", "leave the sound alone"."""
    return bool(KEEP_UP.search(text or ""))


def only_sound_up(text):
    """True when asking for the sound to stay up is all the line says, so nothing else in it
    needs the brain."""
    rest = FILLER.sub(" ", KEEP_UP.sub(" ", text or ""))
    return not re.sub(r"[^\w]+", "", rest)


class Ducker:
    """One per ears process. update() follows the ears' state; the thread does the rest."""

    def __init__(self, backend=None, config_file=None, state_file=None, clock=time.monotonic, wall=time.time):
        self.backend = backend or Pactl()
        self.config_file = config_file or CONFIG_FILE
        self.state_file = Path(state_file or STATE_FILE)
        self.clock, self.wall = clock, wall
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.started = False
        self.running = False
        self.thread = None
        self.active = False
        self.until = 0.0
        self.engaged = False
        self.suspended = False
        self.suspended_at = 0.0
        self.held = {}
        self.apps = {}
        self.orphans = {}
        self.foreign = lambda: set()
        self.exempt = lambda: []
        self.first = None
        self.his = set()
        self.cfg = settings(self.config_file)

    def start(self):
        with self.lock:
            if self.started:
                return
            self.started = self.running = True
            self.recover()
        self.thread = threading.Thread(target=self._run, name="duck", daemon=True)
        self.thread.start()

    def stop(self):
        """Gives everything back now. Safe at exit and on a signal."""
        with self.lock:
            self.running = False
            self.active = False
            self.until = 0.0
            self._give_back(glide=False)
        self.wake.set()

    def update(self, active):
        """The ears' state writer calls this, so it never waits on the lock the thread holds
        while pactl runs."""
        active = bool(active)
        if active == self.active:
            return
        if not active:
            self.until = self.clock() + self.cfg["linger_s"]
        self.active = active
        self.wake.set()

    def suspend(self, on=True):
        """Keeps the sound up for the rest of this conversation, or the next one when none is on."""
        with self.lock:
            self.suspended = bool(on)
            self.suspended_at = self.clock()
            if on:
                self._give_back()
            elif self.started:
                self._tick()
        self.wake.set()

    def configure(self, changes):
        with self.lock:
            self.cfg = save_settings(changes, self.config_file)
            if self.started:
                if not self.cfg["on"]:
                    self._give_back()
                else:
                    self._relevel()
                    self._tick()
            return self.cfg

    def claim(self):
        """Called by the sound hold before it dims anything. Turns down what plays right now
        and returns the streams the hold must leave alone: those turned down here, or ALL while
        he asked for the sound to stay up."""
        with self.lock:
            if not self.started:
                return set()
            if self.suspended:
                return ALL
            if not self.cfg["on"]:
                return set()
            self.until = max(self.until, self.clock() + self.cfg["linger_s"])
            self._tick()
            return set(self.held)

    def status(self):
        cfg = self.cfg
        return {"on": cfg["on"], "level": cfg["level"], "levels": list(LEVELS),
                "active": self.engaged, "suspended": self.suspended, "held": len(self.held)}

    def _wanted(self):
        return self.active or self.clock() < self.until

    def _run(self):
        while True:
            with self.lock:
                if not self.running:
                    return
                try:
                    self._tick()
                except Exception:
                    log.exception("duck tick failed")
                fast = self.engaged or self.held or self._wanted()
                wait = POLL_S if fast else (IDLE_POLL_S if self.orphans else 30.0)
            self.wake.wait(wait)
            self.wake.clear()

    def _tick(self):
        wanted = self._wanted()
        if self.engaged and not wanted:
            self.engaged = False
            self.suspended = False
        if self.held and (not wanted or not self.cfg["on"] or self.suspended):
            self._give_back()
        if self.suspended and not self.engaged and self.clock() - self.suspended_at > SUSPEND_IDLE_S:
            self.suspended = False
        if wanted and not self.engaged:
            self.engaged = True
            self.first = None
            self.his = set()
            self.cfg = settings(self.config_file)
        if wanted and self.cfg["on"] and not self.suspended:
            self._sweep()
        elif self.orphans:
            self._heal()

    def _sweep(self):
        streams = self.backend.streams()
        if streams is None:
            return
        live = {s["index"]: s for s in streams}
        changed = False
        for index in [i for i in self.held if i not in live]:
            entry = self.held.pop(index)
            if not any(e["app"] == entry["app"] for e in self.held.values()):
                self.orphans[entry["app"]] = {"from": entry["from"], "to": entry["to"], "at": self.wall()}
            changed = True
        if self.first is None:
            self.first = max((s["index"] for s in streams), default=-1)
        foreign = self.foreign() or set()
        exempt = [m for m in self.exempt() or [] if m]
        moves = []
        for stream in streams:
            index, volume = stream["index"], stream["volume"]
            if own(stream):
                changed = self._mend(stream) or changed
                continue
            if index in self.held or index in self.his or index in foreign or not volume:
                continue
            names = (stream["app"].lower(), stream["binary"].lower())
            if any(m in n for m in exempt for n in names if n):
                continue
            key = app_key(stream)
            known = self.apps.get(key) or self.orphans.get(key)
            if index > self.first and known and near(volume, known["to"]) and len(known["from"]) == len(volume):
                start, low = known["from"], volume
            else:
                start, low = volume, lowered(volume, self.cfg["level"])
            self.held[index] = {"app": key, "name": stream["app"] or stream["binary"], "from": start, "to": low}
            self.apps.setdefault(key, {"from": start, "to": low})
            self.orphans.pop(key, None)
            if low != volume:
                moves.append((index, volume, low))
            changed = True
        if changed:
            self._save()
        if moves:
            self._ramp(moves, DUCK_STEPS, DUCK_SECONDS)
            log.info("turned down %s", ", ".join(self.held[i]["name"] or str(i) for i, _, _ in moves))

    def _relevel(self):
        """A new level while turned down: the streams move to it from their old volume, except
        one he moved meanwhile, which is his and left where he put it."""
        streams = self.backend.streams()
        if streams is None:
            return
        live = {s["index"]: s for s in streams}
        moves = []
        for index, entry in list(self.held.items()):
            stream = live.get(index)
            if stream is None:
                continue
            if not near(stream["volume"], entry["to"]):
                self.held.pop(index)
                self.his.add(index)
                continue
            low = lowered(entry["from"], self.cfg["level"])
            if low != entry["to"]:
                moves.append((index, stream["volume"], low))
                entry["to"] = low
        for key, known in self.apps.items():
            known["to"] = lowered(known["from"], self.cfg["level"])
        if moves:
            self._save()
            self._ramp(moves, DUCK_STEPS, DUCK_SECONDS)

    def _give_back(self, glide=True):
        if not self.held:
            self.apps.clear()
            return
        streams = self.backend.streams()
        if streams is None:
            return
        live = {s["index"]: s for s in streams}
        moves, given = [], []
        for index, entry in self.held.items():
            stream = live.get(index)
            if stream is None:
                if not any(e["app"] == entry["app"] and i in live for i, e in self.held.items()):
                    self.orphans[entry["app"]] = {"from": entry["from"], "to": entry["to"], "at": self.wall()}
                continue
            if near(stream["volume"], entry["to"]):
                moves.append((index, stream["volume"], entry["from"]))
                given.append(entry["name"] or str(index))
        self.held.clear()
        self.apps.clear()
        if moves:
            if glide:
                self._ramp(moves, RESTORE_STEPS, RESTORE_SECONDS)
            else:
                for index, _, end in moves:
                    self.backend.set_volume(index, end)
            log.info("gave back %s", ", ".join(given))
        self._save()

    def _heal(self):
        """An orphan app's next stream starts at the low level WirePlumber saved: lift it back."""
        cutoff = self.wall() - ORPHAN_DAYS * 86400
        stale = [k for k, o in self.orphans.items() if o.get("at", 0) < cutoff]
        for key in stale:
            self.orphans.pop(key)
        streams = self.backend.streams()
        if streams is None:
            if stale:
                self._save()
            return
        healed, mended = [], False
        for stream in streams:
            key = app_key(stream)
            orphan = self.orphans.get(key)
            if orphan is None:
                continue
            if own(stream):
                mended = self._mend(stream) or mended
                continue
            if len(stream["volume"]) != len(orphan["from"]):
                continue
            if near(stream["volume"], orphan["to"]):
                self.backend.set_volume(stream["index"], orphan["from"])
                healed.append(key)
            self.orphans.pop(key, None)
        if healed:
            log.info("gave back the saved volume of %s", ", ".join(healed))
        if healed or mended or stale:
            self._save()

    def _mend(self, stream):
        """His own stream starting at the low level an earlier run left saved for its name gets
        its volume back, and that orphan goes. An orphan his stream does not match may belong to
        another app of the same name, so it stays for that app."""
        key = app_key(stream)
        orphan = self.orphans.get(key)
        if orphan is None or len(stream["volume"]) != len(orphan["from"]) or not near(stream["volume"], orphan["to"]):
            return False
        self.orphans.pop(key)
        self.backend.set_volume(stream["index"], orphan["from"])
        log.info("gave back the volume of his own %s", stream["app"] or stream["binary"] or stream["index"])
        return True

    def _ramp(self, moves, steps, seconds):
        for step in range(1, steps + 1):
            t = step / steps
            for index, start, end in moves:
                self.backend.set_volume(index, [a + (b - a) * t for a, b in zip(start, end)])
            if step < steps and seconds:
                time.sleep(seconds / steps)

    def _save(self):
        try:
            if not self.held and not self.orphans:
                self.state_file.unlink(missing_ok=True)
                return
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "at": self.wall(),
                "held": [dict(entry, index=index) for index, entry in self.held.items()],
                "orphans": self.orphans,
            }))
            tmp.replace(self.state_file)
        except OSError as error:
            log.warning("ducked.json not written: %s", error)

    def recover(self):
        """What a run that died left turned down goes back up; what closed meanwhile is an orphan."""
        try:
            saved = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        held = {}
        for entry in saved.get("held") or []:
            try:
                held[int(entry["index"])] = {"app": str(entry["app"]), "name": str(entry.get("name") or ""),
                                             "from": [int(v) for v in entry["from"]], "to": [int(v) for v in entry["to"]]}
            except (KeyError, TypeError, ValueError):
                continue
        orphans = saved.get("orphans") if isinstance(saved.get("orphans"), dict) else {}
        self.orphans = {str(k): v for k, v in orphans.items() if isinstance(v, dict) and "from" in v and "to" in v}
        self.held = held
        if held:
            log.info("giving back %d left turned down by the last run", len(held))
        self._give_back(glide=False)
        if self.orphans:
            self._heal()
        self._save()


DUCK = Ducker()
