"""Lights: the Hue lamps in his room, shown and switched from the app."""

import re

from .. import hue
from .common import ActFailed, Cache, unavailable

ID = "lights"
TITLE = "Lights"
TOOLS = []
WRITES = ()
FAILED = ("no hue bridge", "the bridge did not answer", "unknown", "action must", "brightness must")
_cache = Cache(5)
_scenes_cache = Cache(60)
CACHES = (_cache, _scenes_cache)
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SCENE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
ALL_WORDS = ("all", "everything", "the room")
CANNOT = {"color": "cannot show colour, only warm to cool white", "ct": "cannot change its white"}
NOBODY = {"color": "no lamp here can show colour", "ct": "no lamp here can change its white"}
KELVIN_MIN = 1500
KELVIN_MAX = 9000
SCENES = {
    "off": ("off", "all", None),
    "on": ("on", "all", 100),
    "dim": ("on", "all", 25),
    "bright": ("on", "all", 100),
}


def lamps():
    return _cache.get(_read)


def _read():
    out = []
    for lamp in hue.lamp_states():
        bri = lamp.get("bri")
        level = round(int(bri) * 100 / 254) if isinstance(bri, (int, float)) else None
        out.append({
            "id": lamp["id"], "name": lamp["name"], "on": lamp["on"],
            "level": level if lamp["on"] else 0, "color": lamp.get("color"),
            "ct": lamp.get("ct"), "reachable": lamp.get("reachable", True), "caps": lamp.get("caps") or {"color": False, "ct": None},
        })
    return out


def scenes():
    return _scenes_cache.get(hue.scenes)


def panel(section):
    found = lamps()
    if not found:
        if not hue.configured():
            return [unavailable("No Hue bridge is set up on this hub yet.", title="Lamps", source="the Hue bridge",
                                setup=True, tone="warn", retry=False)]
        return [unavailable("The Hue bridge did not answer, so I cannot read or switch the lamps.",
                            title="Lamps", source="the Hue bridge")]
    blocks = [{"kind": "lamps", "title": "Lamps", "lamps": found}]
    listed = scenes()
    if listed:
        blocks.append({"kind": "scenes", "title": "Scenes", "items": [
            {key: value for key, value in item.items() if key != "group"} for item in listed]})
    return blocks + [
        {"kind": "actions", "items": [
            {"label": "All off", "icon": "off", "act": {"module": ID, "action": "scene", "args": {"name": "off"}}},
            {"label": "Dim", "icon": "dim", "act": {"module": ID, "action": "scene", "args": {"name": "dim"}}},
            {"label": "Bright", "icon": "bright", "act": {"module": ID, "action": "scene", "args": {"name": "bright"}}},
            {"label": "Make it cosy", "icon": "cosy", "say": "Make the lights cosy"},
        ]},
    ]


def _can(lamp, need):
    caps = lamp.get("caps") or {}
    return bool(caps.get("color")) if need == "color" else bool(caps.get("ct"))


def _targets(args, need):
    wanted = str(args.get("lamp") or "all")
    found = lamps()
    if wanted.lower().strip() in ALL_WORDS:
        capable = [lamp for lamp in found if _can(lamp, need)]
        if found and not capable:
            raise ActFailed(NOBODY[need])
        if len(capable) == len(found):
            return ["all"]
        return [lamp["id"] for lamp in capable]
    slot = hue.TARGETS.get(wanted.lower().strip(), wanted)
    for lamp in found:
        if lamp["id"] == slot and not _can(lamp, need):
            raise ActFailed(f"the {lamp['name']} {CANNOT[need]}")
    return [slot]


def act(action, args):
    result = _act(action, args)
    _cache.clear()
    text = str(result).lower()
    if ": refused" in text or any(text.startswith(word) for word in FAILED):
        raise ActFailed(result)
    return result


def _act(action, args):
    if action == "toggle":
        return hue.hue_light("toggle", str(args.get("lamp") or "all"))
    if action == "level":
        level = args.get("level")
        if isinstance(level, bool) or not isinstance(level, (int, float)):
            raise ValueError("level must be a number from 0 to 100")
        target = str(args.get("lamp") or "all")
        if level <= 0:
            return hue.hue_light("off", target)
        return hue.hue_light("on", target, min(100, level))
    if action == "color":
        value = args.get("hex")
        if not isinstance(value, str) or not HEX_RE.match(value):
            raise ValueError("hex must look like #rrggbb")
        return ", ".join(hue.hue_light("on", target, color=value) for target in _targets(args, "color"))
    if action == "temperature":
        kelvin = args.get("kelvin")
        if isinstance(kelvin, bool) or not isinstance(kelvin, (int, float)) or not KELVIN_MIN <= kelvin <= KELVIN_MAX:
            raise ValueError(f"kelvin must be a number from {KELVIN_MIN} to {KELVIN_MAX}")
        return ", ".join(hue.hue_light("on", target, temperature=kelvin) for target in _targets(args, "ct"))
    if action == "scene" and args.get("id") is not None:
        scene_id = args.get("id")
        if not isinstance(scene_id, str) or not SCENE_ID_RE.match(scene_id):
            raise ValueError("scene id must be the id the bridge gave")
        known = next((item for item in scenes() if item["id"] == scene_id), None)
        if known is None:
            return hue.recall_scene(scene_id)
        return hue.recall_scene(scene_id, known.get("group"), known.get("name"))
    if action == "scene":
        name = str(args.get("name") or "")
        if name not in SCENES:
            raise ValueError(f"no scene {name}")
        what, target, level = SCENES[name]
        return hue.hue_light(what, target, level)
    raise ValueError(f"lights has no action {action}")


def summary():
    found = lamps()
    if not found:
        return None
    lit = sum(1 for lamp in found if lamp["on"])
    return {"label": "Lamps on", "value": str(lit), "unit": f"/ {len(found)}", "icon": "lightbulb"}
