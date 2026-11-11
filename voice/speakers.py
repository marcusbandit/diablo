"""What the computer itself is playing, so the ears never answer the speakers.

The microphone hears the room, and the room includes the speakers. There is no echo
canceller on banditbox, so this listens to the default sink's monitor instead, the exact
audio going out, and offers three things:

- said_name(): a second wake detector runs on the speaker audio. When "Diablo" just came
  out of the speakers (a video, a Telegram voice note, his own voice from another app), a
  wake on the mic at the same moment is that echo, not Marcus.
- level(): how loud the computer is playing, so the setup can refuse a take with music
  under it.
- Hush: pauses every MPRIS player that is playing (Spotify, Firefox, AniBeam) and resumes
  exactly those afterwards, so a request is not drowned out and the transcript is only him.
- Hold: the same moment handled the way his dictation key handles it, by gvoice's rules in
  ~/.config/gvoice/settings.json (pause, dim or mute, per app). Falls back to Hush when
  gvoice is not on this machine. A single reference counted SoundHold sits under every
  Hold, so his listening turn and Diablo's spoken reply share one hold: overlapping ones
  give the sound back once, and a short gap keeps it held between back to back replies.
"""

import collections
import logging
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from . import wake

log = logging.getLogger("diablo.speakers")

# The speaker detector counts "Diablo" at a lower bar than the mic: refusing an echo
# costs nothing, missing one costs a loose firing.
ECHO_FACTOR = 0.5
REOPEN_S = 30.0


def default_sink():
    try:
        return subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class Speakers:
    def __init__(self, detect=True):
        self.detect = detect and wake.trained()
        self.detector = wake.Detector() if self.detect else None
        self.scores = collections.deque(maxlen=40)  # (time, score), about 3 s
        self.levels = collections.deque(maxlen=250)  # (time, rms), about 20 s, a whole setup take
        self.proc = None
        self.sink = ""
        self.opened = 0.0
        self.lock = threading.Lock()
        self.running = True
        self._open()
        threading.Thread(target=self._watch, daemon=True).start()

    def _open(self):
        self.sink = default_sink()
        self.opened = time.monotonic()
        if not self.sink:
            log.warning("no default sink, running without the speaker tap")
            return
        try:
            self.proc = self._spawn()
        except OSError as error:
            self.proc = None
            log.warning("speaker tap failed, running without it: %s", error)
            return
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        log.info("speaker tap on %s.monitor", self.sink)

    def _spawn(self):
        return subprocess.Popen(
            # parec, not pw-record: pw-record --target does not resolve a ".monitor" name and
            # quietly records the default source instead, which is the microphone.
            ["parec", f"--device={self.sink}.monitor", "--format=float32le", f"--rate={wake.RATE}",
             "--channels=1", "--raw", "--latency-msec=40"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def _watch(self):
        """Follows the default sink when it changes, such as headphones plugged in."""
        while self.running:
            time.sleep(REOPEN_S)
            if not self.running:
                return
            sink = default_sink()
            dead = self.proc is None or self.proc.poll() is not None
            # A new default sink, or parec gone with a PipeWire restart: open the tap again.
            if sink and (sink != self.sink or dead):
                if dead:
                    log.info("speaker tap closed, opening it again")
                self.close(stop=False)
                self._open()

    def _read(self, proc):
        size = wake.CHUNK * 4
        while self.running and proc.poll() is None:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            self.feed(np.frombuffer(raw, dtype="<f4"))

    def feed(self, block):
        now = time.monotonic()
        rms = float(np.sqrt(np.mean(block ** 2)))
        score = None
        if self.detector is not None:
            # Only this reader thread touches the detector, so inference runs outside the lock.
            self.detector.feed(wake.to_int16(block))
            score = self.detector.last
        with self.lock:
            self.levels.append((now, rms))
            if score is not None:
                self.scores.append((now, score))

    def said_name(self, at=None, seconds=1.5):
        """True when the speakers carried something that scored as the name around `at`,
        the mic block's own stamp, so a backed up mic queue still lines up with the tap."""
        if self.detector is None:
            return False
        at = time.monotonic() if at is None else at
        bar = self.detector.threshold * ECHO_FACTOR
        with self.lock:
            return any(score >= bar for when, score in self.scores if at - seconds <= when <= at + 0.5)

    def level(self, seconds=1.0, since=None, until=None):
        """Loudest 80 ms of output in a window: the last `seconds`, or since..until."""
        since = time.monotonic() - seconds if since is None else since
        until = float("inf") if until is None else until
        with self.lock:
            recent = [rms for when, rms in self.levels if since <= when <= until]
        return max(recent) if recent else 0.0

    def close(self, stop=True):
        if stop:
            self.running = False
        if self.proc is not None:
            self.proc.kill()
            self.proc = None


def _playerctl(*args):
    try:
        return subprocess.run(["playerctl", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class Hush:
    """Context manager: pauses the players that are playing, resumes only those."""

    def __init__(self):
        self.paused = []

    def __enter__(self):
        # One call for every player's status, so a slow player costs one timeout, not several.
        for line in _playerctl("-a", "-f", "{{playerName}} {{status}}", "status").splitlines():
            player, _, status = line.strip().rpartition(" ")
            if player and status == "Playing":
                _playerctl("-p", player, "pause")
                self.paused.append(player)
        if self.paused:
            log.info("paused %s", ", ".join(self.paused))
        return self

    def __exit__(self, *exc):
        for player in self.paused:
            _playerctl("-p", player, "play")
        self.paused = []


GVOICE_MEDIA = Path.home() / "Projects/voice/media.py"
# What a hold took, so ears that die mid-turn give the sound back on their next start.
HELD_FILE = Path.home() / ".local/state/diablo/media-held.json"
RECOVER_WITHIN = 600
_gvoice = None


def gvoice_media(path=GVOICE_MEDIA):
    """gvoice's media module, loaded once from its checkout, or None."""
    global _gvoice
    if _gvoice is None:
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("gvoice_media", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.log = lambda *parts: log.info(" ".join(str(p) for p in parts))
            _gvoice = module
        except Exception as error:
            log.info("gvoice media not loaded, pausing players instead: %s", error)
            _gvoice = False
    return _gvoice or None


SPEECH_MODES = ("gvoice", "dim", "pause", "off")
DEFAULT_GAP_S = 1.5
DEFAULT_MODE = "gvoice"


def _setting(cfg, key, default=None):
    value = (cfg or {}).get(key)
    return default if value is None or value == "" else value


def speech_mode(cfg):
    """The setting speech_hold: gvoice (his gvoice rules, the default), dim, pause or off."""
    mode = str(_setting(cfg, "speech_hold", DEFAULT_MODE)).strip().lower()
    return mode if mode in SPEECH_MODES else DEFAULT_MODE


def speech_dim_to(cfg):
    """The setting speech_hold_dim_to, 0 to 100, or None to use gvoice's own dim level."""
    try:
        return max(0, min(100, int(round(float(_setting(cfg, "speech_hold_dim_to"))))))
    except (TypeError, ValueError, OverflowError):
        return None


def speech_gap(cfg):
    """The setting speech_hold_gap_s: how long a released hold waits for the next taker."""
    try:
        return max(0.0, min(30.0, float(_setting(cfg, "speech_hold_gap_s", DEFAULT_GAP_S))))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_GAP_S


OWN = {"diablo", "pw-play"}


def own(source):
    """A sound Diablo plays himself: a chime or a reply. Holding it would dim his own voice."""
    return bool(OWN.intersection(str(n).lower() for n in source.get("names") or []))


def rules_for(media, mode, dim_to=None):
    """gvoice's media rules bent to a speech_hold mode. gvoice returns them unchanged.

    dim and pause keep an app he excluded ("nothing") excluded, and otherwise replace the
    per app rules, so the mode he picked is the one that happens.
    """
    if mode not in ("dim", "pause"):
        return media
    level = media["dim_to"] if dim_to is None else dim_to
    action = "dim" if mode == "dim" else "pause"
    apps = [rule if rule.get("action") == "nothing" else dict(rule, action=action, dim_to=level)
            for rule in media.get("apps", [])]
    return {**media, "players": action, "others": "dim", "dim_to": level, "apps": apps}


class SoundHold:
    """One hold on other sound, shared by everything that talks over it.

    acquire() and release() are reference counted. The first acquire takes the sound
    (dim, pause, mute, by the rules it was given) and writes the crash file; the last
    release waits `gap` seconds before giving it back, and an acquire inside that gap
    cancels the wait, so a second reply or his next turn never lets the sound pop back
    up between. The sound is given back once, to what it was before the first acquire,
    and only where nobody has touched it since. While held, the first acquire's rules win.
    """

    def __init__(self, media=None, gap=DEFAULT_GAP_S, timer=threading.Timer):
        self._media = media
        self.gap = gap
        self._timer_factory = timer
        self._lock = threading.RLock()
        self.users = 0
        self.ops = []
        self.hush = None
        self.taken = False
        self._timer = None

    @property
    def media(self):
        return self._media if self._media is not None else gvoice_media()

    @property
    def paused(self):
        if self.hush is not None:
            return self.hush.paused
        names = [op.get("name") for op in self.ops if op.get("op") == "pause"]
        return list(dict.fromkeys(names))

    def acquire(self, mode=DEFAULT_MODE, dim_to=None):
        with self._lock:
            self.users += 1
            self._cancel()
            if not self.taken:
                self._take(mode, dim_to)

    def release(self, gap=None):
        with self._lock:
            self.users = max(0, self.users - 1)
            if self.users or not self.taken:
                return
            wait = self.gap if gap is None else gap
            if wait <= 0:
                self._give_back()
                return
            self._cancel()
            timer = self._timer_factory(wait, lambda: self._expire(timer))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def release_all(self):
        """For shutdown: give the sound back now, whoever still holds it."""
        with self._lock:
            self.users = 0
            self._cancel()
            if self.taken:
                self._give_back()

    def _cancel(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _expire(self, timer=None):
        with self._lock:
            if timer is not None and timer is not self._timer:
                return
            self._timer = None
            if not self.users and self.taken:
                self._give_back()

    def _take(self, mode, dim_to):
        self.taken = True
        media = self.media
        if media is None:
            if mode != "dim":
                self.hush = Hush().__enter__()
            return
        try:
            rules = rules_for(media.load_settings()["media"], mode, dim_to)
            self.ops = media.plan([src for src in media.discover(names=False) if not own(src)], rules)
            if self.ops:
                _save_held(self.ops)
            media.apply(self.ops)
        except Exception as error:
            log.warning("gvoice hold failed: %s", error)
            taken, self.ops = self.ops, []
            try:
                media.undo(taken)
            except Exception:
                pass
            HELD_FILE.unlink(missing_ok=True)
            if mode != "dim":
                self.hush = Hush().__enter__()
            return
        if self.ops:
            log.info("held %s", ", ".join(f"{op['op']} {op.get('name')}" for op in self.ops))

    def _give_back(self):
        hush, ops = self.hush, self.ops
        self.hush, self.ops, self.taken = None, [], False
        if hush is not None:
            hush.__exit__(None, None, None)
        if ops:
            try:
                self.media.undo(ops)
            except Exception as error:
                log.warning("gvoice release failed: %s", error)
            log.info("gave back %d", len(ops))
        HELD_FILE.unlink(missing_ok=True)


_shared = SoundHold()


def shutdown():
    """Gives the shared hold back at once. Safe to call at exit or on a signal."""
    try:
        _shared.release_all()
    except Exception as error:
        log.warning("releasing the hold failed: %s", error)


class Hold:
    """Context manager: what plays while he talks to Diablo follows his dictation rules.

    gvoice decides per source, from the same settings file the Voice app edits: pause a
    player, dim it (Spotify to 35%), mute a game. Leaving gives back only what was taken,
    and only where he has not touched it since. Spoken replies enter the same shared hold
    with a speech_hold mode, see SoundHold.
    """

    def __init__(self, mode=DEFAULT_MODE, dim_to=None, gap=None, holder=None):
        self.holder = holder if holder is not None else _shared
        self.mode, self.dim_to, self.gap = mode, dim_to, gap
        self.entered = False

    @property
    def paused(self):
        return self.holder.paused

    @property
    def ops(self):
        return self.holder.ops

    def __enter__(self):
        self.holder.acquire(self.mode, self.dim_to)
        self.entered = True
        return self

    def __exit__(self, *exc):
        if self.entered:
            self.entered = False
            self.holder.release(self.gap)

    @staticmethod
    def recover(media=None):
        """Gives back what a hold left behind when the ears died inside a turn."""
        try:
            Hold._recover(media)
        except Exception as error:
            log.warning("gvoice recover failed: %s", error)

    @staticmethod
    def _recover(media=None):
        import json

        media = media if media is not None else gvoice_media()
        try:
            saved = json.loads(HELD_FILE.read_text())
            ops = saved["ops"] if isinstance(saved, dict) else saved
            age = time.time() - float(saved.get("at", 0)) if isinstance(saved, dict) else 1e9
        except (OSError, ValueError, KeyError, TypeError):
            return
        HELD_FILE.unlink(missing_ok=True)
        if age > RECOVER_WITHIN:
            ops = [op for op in ops if op.get("op") != "pause"]
        if media is not None and ops:
            log.info("giving back %d left over from the last run", len(ops))
            try:
                media.undo(ops)
            except Exception as error:
                log.warning("gvoice recover failed: %s", error)


def _save_held(ops):
    import json

    try:
        HELD_FILE.parent.mkdir(parents=True, exist_ok=True)
        HELD_FILE.write_text(json.dumps({"at": time.time(), "ops": ops}))
    except OSError:
        pass
