"""What Marcus is doing right now, from a snapshot of the desktop. Pure functions, no I/O."""

import fnmatch
from dataclasses import dataclass, field

BROWSER_PLAYERS = ("firefox", "zen", "librewolf", "chromium", "chrome", "brave", "vivaldi", "thorium")
MUSIC_PLAYERS = (
    "spotify", "spotifyd", "ncspot", "spotube", "cider", "tidal", "youtube-music", "ytmdesktop",
    "strawberry", "elisa", "rhythmbox", "lollypop", "amberol", "mpd", "cmus", "quodlibet",
    "audacious", "clementine", "feishin", "sublime-music", "termusic", "deadbeef", "g4music",
)
MUSIC_SITES = ("music.youtube.com", "open.spotify.com", "soundcloud.com", "bandcamp.com", "music.apple.com", "tidal.com")
AUDIO_EXT = (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".wav", ".aac", ".wma", ".alac")
SITE_NAMES = {
    "youtube.com": "YouTube", "youtu.be": "YouTube", "netflix.com": "Netflix", "crunchyroll.com": "Crunchyroll",
    "nebula.tv": "Nebula", "twitch.tv": "Twitch", "disneyplus.com": "Disney+", "primevideo.com": "Prime Video",
}


@dataclass(frozen=True)
class Player:
    name: str  # playerctl instance name, e.g. firefox.instance_1_194
    status: str  # Playing, Paused, Stopped
    url: str = ""
    title: str = ""

    @property
    def base(self):
        return self.name.split(".")[0].lower()

    @property
    def music(self):
        if any(self.base.startswith(p) for p in MUSIC_PLAYERS):
            return True
        low = self.url.lower()
        if any(site in low for site in MUSIC_SITES):
            return True
        return low.startswith("file://") and low.endswith(AUDIO_EXT)

    @property
    def label(self):
        for host, name in SITE_NAMES.items():
            if host in self.url:
                return name
        return {"firefox": "the browser", "anibeam": "AniBeam", "mpv": "mpv"}.get(self.base, self.base)


@dataclass(frozen=True)
class Snapshot:
    cls: str = ""
    title: str = ""
    fullscreen: bool = False
    players: tuple = field(default_factory=tuple)
    call: str | None = None  # name of the app holding a call microphone
    locked: bool = False


def matches(value, patterns):
    return bool(value) and any(fnmatch.fnmatchcase(value, p) for p in patterns)


def title_has(title, words):
    low = title.lower()
    return any(w.lower() in low for w in words)


def app_name(cls):
    """A readable name for a window class: com.marcusrosado.AniBeam becomes AniBeam."""
    if not cls:
        return "the desktop"
    tail = cls.rsplit(".", 1)[-1] if cls.count(".") >= 2 else cls
    return {"zen": "Zen", "vesktop": "Discord", "foot": "foot", "T3Code": "T3 Code"}.get(tail, tail)


def players_of(cls, players, apps):
    """The MPRIS players that belong to the focused window, as near as the names allow."""
    if matches(cls, apps["browsers"]):
        return [p for p in players if p.base in BROWSER_PLAYERS]
    low = cls.lower()
    return [p for p in players if p.base and p.base not in BROWSER_PLAYERS and p.base in low]


def classify(snap, apps):
    """(state, reason). States: away, cinema, intermission, game, call, watching, focus, everyday."""
    if snap.locked:
        return "away", "the screen is locked"

    app = app_name(snap.cls)
    is_video_app = matches(snap.cls, apps["video"])
    is_browser = matches(snap.cls, apps["browsers"])
    on_video_site = is_browser and title_has(snap.title, apps["video_sites"])
    is_work = matches(snap.cls, apps["work"]) or (is_browser and title_has(snap.title, apps["work_sites"]))
    is_chat = matches(snap.cls, apps["chat"])
    is_game = matches(snap.cls, apps["games"])

    own = [p for p in players_of(snap.cls, snap.players, apps) if not p.music]
    playing = [p for p in own if p.status == "Playing"]
    paused = [p for p in own if p.status == "Paused"]

    if snap.fullscreen:
        if (is_video_app or is_browser) and playing:
            return "cinema", f"{app} is fullscreen and {playing[0].label} is playing"
        if is_video_app and not own:
            return "cinema", f"{app} is fullscreen"  # a player without MPRIS: fullscreen is the best clue
        if (is_video_app or on_video_site) and paused:
            return "intermission", f"{paused[0].label} is paused while {app} is fullscreen"
        if is_game or not (is_browser or is_work or is_chat or is_video_app):
            return "game", f"{app} is fullscreen"
    elif is_game:
        return "game", f"{app} is focused"

    if snap.call:
        return "call", f"{snap.call} has a call going"
    if playing and (is_video_app or on_video_site):
        return "watching", f"{playing[0].label} is playing in {app}"
    if is_work:
        return "focus", f"{app} is focused"
    return "everyday", f"{app} is focused"


class Settler:
    """Holds a new state back until it has lasted a moment, so alt-tab never flickers the room.

    Leaving cinema waits longest: a quick look at Discord mid-episode keeps the dark.
    """

    def __init__(self, settle_s=3.0):
        self.settle_s = settle_s
        self.active = None
        self.reason = ""
        self.since = 0.0
        self.candidate = None
        self.candidate_since = 0.0

    def delay(self, to):
        frm = self.active
        if frm is None or to == "away":
            return 0.0
        if to == "cinema":
            return 0.5 if frm == "intermission" else 1.0
        if frm == "cinema":
            return 2.0 if to == "intermission" else 6.0
        return self.settle_s

    def feed(self, state, reason, now):
        """Returns True when the active state changes."""
        if state == self.active:
            self.candidate = None
            self.reason = reason
            return False
        if state != self.candidate:
            self.candidate, self.candidate_since = state, now
        if now - self.candidate_since >= self.delay(state):
            self.active, self.reason, self.since = state, reason, now
            self.candidate = None
            return True
        return False
