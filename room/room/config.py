"""Where room keeps its files, and the settings with their defaults.

The config file is optional. Anything it leaves out falls back to DEFAULTS, so a
file with one line changes one thing.
"""

import copy
import os
import tomllib
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("ROOM_CONFIG_DIR", HOME / ".config/room"))
STATE_DIR = Path(os.environ.get("ROOM_STATE_DIR", HOME / ".local/state/room"))
CONFIG = CONFIG_DIR / "config.toml"
BRIDGE = CONFIG_DIR / "bridge.toml"  # host and key from pairing, mode 600
ONBOARDED = CONFIG_DIR / "onboarded"
WELCOMED = STATE_DIR / "welcomed"  # the one "room is on" notification was sent
STATUS = STATE_DIR / "status.json"  # what the daemon is doing, for the CLI and the launcher
PAUSE = STATE_DIR / "paused_until"  # epoch seconds; the daemon leaves the lamps alone until then
CLEAR = STATE_DIR / "clear-holds"  # the CLI touches it, the daemon forgets every hold
LOG = STATE_DIR / "log.jsonl"
API_KEYS_ENV = Path(os.environ.get("ROOM_API_KEYS_ENV", HOME / ".config/marcusbandit-api-keys.env"))  # older setup

SCENES = ("focus", "everyday", "watching", "cinema", "intermission", "game", "call")
# away and bedtime are states, not scenes: away touches nothing, bedtime fades out.

DEFAULTS = {
    # Lamp names from the Hue app. Empty means every lamp in `room`, or every lamp on the bridge.
    "lamps": [],
    "room": "",
    "settle_s": 3.0,  # a new activity has to last this long before the lights move
    "night": {
        "start": "23:00",  # the white light starts warming and dimming
        "full": "02:00",  # fully warm from here
        "end": "07:00",  # back to day
        "warmest_k": 2200,
        "cap": 55,  # brightness ceiling at full night, percent
    },
    "bedtime": {
        "enabled": False,  # fade the lamps out when the screen locks late
        "after": "01:00",
        "until": "09:00",
        "locked_min": 5,
    },
    "hold": {"manual_min": 10},  # a lamp changed by hand stays yours at least this long
    "apps": {
        "video": [
            "com.marcusrosado.AniBeam", "mpv", "notvlc", "vlc", "org.videolan.VLC", "celluloid",
            "io.github.celluloid_player.Celluloid", "com.github.iwalton3.jellyfin-media-player",
            "Stremio", "kodi", "io.github.mpc-qt.mpc-qt",
        ],
        "browsers": [
            "zen", "zen-*", "firefox", "librewolf", "chromium", "google-chrome", "brave-browser",
            "vivaldi-stable", "thorium-browser",
        ],
        "video_sites": [
            "YouTube", "Netflix", "Crunchyroll", "Nebula", "Twitch", "Disney+", "Prime Video",
            "HBO Max", "Viaplay", "TV 2 Play", "DRTV", "Plex", "Jellyfin", "Vimeo", "HIDIVE",
        ],
        "work": [
            "com.t3tools.T3Code", "foot", "footclient", "kitty", "Alacritty", "org.wezfurlong.wezterm",
            "com.mitchellh.ghostty", "code", "Code", "code-oss", "codium", "VSCodium", "dev.zed.Zed",
            "jetbrains-*", "nvim", "neovide", "emacs", "obsidian", "Godot", "godot", "org.godotengine.*",
            "claude-*", "org.gnome.TextEditor", "kate", "sublime_text", "penpot",
        ],
        "work_sites": [
            "GitHub", "GitLab", "Claude", "T3 Chat", "Stack Overflow", "localhost", "MDN", "docs.",
            "Documentation", "crates.io", "PyPI", "Arch Wiki", "ArchWiki", "DTU Learn",
        ],
        "games": [
            "steam_app_*", "gamescope", "Celeste", "love", "Minecraft*", "minecraft*", "osu!",
            "factorio", "hollow_knight*", "RimWorld*", "Terraria*", "org.prismlauncher.*",
        ],
        "chat": ["vesktop", "discord", "Beeper", "org.telegram.desktop", "signal"],
    },
    # Each scene: bri (percent) and k (kelvin) for white light. Colour lamps take `color`
    # at `color_bri` when the scene has one. `off` lists lamp names or Hue archetypes
    # (globs) that go dark. [scenes.<name>.lamps."<lamp name>"] overrides one lamp.
    "scenes": {
        "focus": {"bri": 100, "k": 4300},
        "everyday": {"bri": 70, "k": 3000},
        "watching": {"bri": 40, "k": 2700},
        "cinema": {"bri": 4, "k": 2200, "color": "#ff7a2e", "color_bri": 9, "off": ["ceiling_*", "pendant_*"]},
        "intermission": {"bri": 30, "k": 2600},
        "game": {"bri": 25, "k": 2700},
        "call": {"bri": 75, "k": 3500},
    },
}


def merge(base, over):
    """Deep merge: tables merge key by key, everything else is replaced."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load(path=None):
    path = Path(path or CONFIG)
    try:
        user = tomllib.loads(path.read_text())
    except FileNotFoundError:
        user = {}
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"room: {path} is not valid TOML: {exc}") from exc
    return merge(DEFAULTS, user)


def read_env_file(path):
    out = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        out[key.strip()] = value.strip().strip("'\"")
    return out


def bridge_credentials():
    """(host, key) for the Hue bridge, or (None, None) when room has never been paired.

    Order: bridge.toml from pairing, then HUE_BRIDGE/HUE_TOKEN in the environment,
    then the same two names in ~/.config/marcusbandit-api-keys.env.
    """
    try:
        data = tomllib.loads(BRIDGE.read_text())
        if data.get("host") and data.get("key"):
            return data["host"], data["key"]
    except (OSError, tomllib.TOMLDecodeError):
        pass
    host, key = os.environ.get("HUE_BRIDGE"), os.environ.get("HUE_TOKEN")
    if host and key:
        return host, key
    env = read_env_file(API_KEYS_ENV)
    if env.get("HUE_BRIDGE") and env.get("HUE_TOKEN"):
        return env["HUE_BRIDGE"], env["HUE_TOKEN"]
    return None, None


def save_bridge(host, key):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BRIDGE.touch(mode=0o600, exist_ok=True)
    BRIDGE.chmod(0o600)
    BRIDGE.write_text(f'host = "{host}"\nkey = "{key}"\n')


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    raise TypeError(f"no TOML for {value!r}")


def save_choices(choices, path=None):
    """Write the few settings onboarding asks about, keeping anything else in the file.

    `choices` is a flat dict of dotted keys, {"lamps": [...], "bedtime.enabled": True}.
    The file is rewritten as plain TOML; comments in an edited file do not survive.
    """
    path = Path(path or CONFIG)
    try:
        current = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        current = {}
    for dotted, value in choices.items():
        node = current
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# room settings. Defaults live in room/config.py; this file only holds changes.\n"
                    + dump(current))


def dump(data, prefix=""):
    plain = [(k, v) for k, v in data.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, dict)]
    lines = [f"{quote_key(k)} = {toml_value(v)}" for k, v in plain]
    for key, value in tables:
        name = f"{prefix}.{quote_key(key)}" if prefix else quote_key(key)
        body = dump(value, name)
        lines.append(f"\n[{name}]\n{body}" if any(not isinstance(v, dict) for v in value.values()) else body)
    return "\n".join(line for line in lines if line) + "\n"


def quote_key(key):
    return key if key.replace("_", "").replace("-", "").isalnum() else toml_value(key)
