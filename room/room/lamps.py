"""Lamps as room sees them, the state it wants for each, and the scene and night maths."""

import fnmatch
from dataclasses import dataclass
from datetime import datetime

from . import color


@dataclass(frozen=True)
class Lamp:
    id: str
    name: str
    archetype: str = ""
    has_color: bool = False
    mirek_min: int | None = None  # None: the lamp has no white temperature control
    mirek_max: int | None = None
    min_dim: float = 0.0

    @classmethod
    def from_v2(cls, d):
        ct = (d.get("color_temperature") or {}).get("mirek_schema") or {}
        return cls(
            id=d["id"],
            name=d.get("metadata", {}).get("name", d["id"]),
            archetype=d.get("metadata", {}).get("archetype", ""),
            has_color="color" in d,
            mirek_min=ct.get("mirek_minimum"),
            mirek_max=ct.get("mirek_maximum"),
            min_dim=(d.get("dimming") or {}).get("min_dim_level", 0.0),
        )

    def matches(self, patterns):
        return any(fnmatch.fnmatchcase(self.name, p) or fnmatch.fnmatchcase(self.archetype, p) for p in patterns)


@dataclass(frozen=True)
class Target:
    on: bool
    bri: float = 0.0
    mirek: int | None = None
    xy: tuple | None = None

    def key(self):
        """Rounded so tiny drifts (night warming by one mirek) don't each send a command."""
        if not self.on:
            return (False,)
        return (True, round(self.bri), self.mirek and round(self.mirek / 5) * 5,
                self.xy and (round(self.xy[0], 3), round(self.xy[1], 3)))


OFF = Target(on=False)


def clamp(target, lamp):
    """The target as this lamp can show it: brightness above its floor, white inside its range,
    no colour on a white-only lamp. This is also what the bridge echoes back."""
    if not target.on:
        return target
    xy = target.xy if lamp.has_color else None
    mirek = None
    if not xy and target.mirek and lamp.mirek_min:
        mirek = max(lamp.mirek_min, min(lamp.mirek_max, target.mirek))
    return Target(on=True, bri=round(max(lamp.min_dim, min(100.0, target.bri)), 2), mirek=mirek, xy=xy)


def body(target, lamp, duration_ms=None):
    """The v2 PUT body that puts `lamp` into `target`."""
    target = clamp(target, lamp)
    out = {"on": {"on": target.on}}
    if target.on:
        out["dimming"] = {"brightness": target.bri}
        if target.xy:
            out["color"] = {"xy": {"x": target.xy[0], "y": target.xy[1]}}
        elif target.mirek:
            out["color_temperature"] = {"mirek": target.mirek}
    if duration_ms is not None:
        out["dynamics"] = {"duration": int(duration_ms)}
    return out


def minutes(hhmm):
    h, _, m = str(hhmm).partition(":")
    return int(h) * 60 + int(m or 0)


def on_night_axis(m):
    """Minutes since noon, so an evening-to-morning window never wraps."""
    return (m - 720) % 1440


def night_factor(when, night):
    """0 by day, rising from `start` to 1 at `full`, 1 until `end`."""
    if isinstance(when, (int, float)):
        when = datetime.fromtimestamp(when)
    t = on_night_axis(when.hour * 60 + when.minute + when.second / 60)
    start, full, end = (on_night_axis(minutes(night[k])) for k in ("start", "full", "end"))
    if t < start or t >= end:
        return 0.0
    if t >= full or full <= start:
        return 1.0
    return (t - start) / (full - start)


def in_window(when, after, until):
    if isinstance(when, (int, float)):
        when = datetime.fromtimestamp(when)
    t = on_night_axis(when.hour * 60 + when.minute)
    return on_night_axis(minutes(after)) <= t < on_night_axis(minutes(until))


def warm(target, factor, night):
    """Night warmth on top of a scene: white goes toward warmest_k, everything is capped."""
    if not target.on or factor <= 0:
        return target
    cap = 100 - factor * (100 - night["cap"])
    bri = min(target.bri, cap)
    mirek = target.mirek
    if mirek:
        warmest = color.mirek(night["warmest_k"])
        if warmest > mirek:
            mirek = round(mirek + factor * (warmest - mirek))
    return Target(on=True, bri=bri, mirek=mirek, xy=target.xy)


def scene_target(scene, lamp):
    """One lamp's state for a scene table from the config, before night warmth."""
    own = (scene.get("lamps") or {}).get(lamp.name, {})
    spec = {k: v for k, v in scene.items() if k != "lamps"} | own
    if "k" in own and "color" not in own:
        spec.pop("color", None)  # a lamp given its own white takes white, not the scene's colour
        spec.pop("color_bri", None)
    if spec.get("on") is False:
        return OFF
    if "on" not in own and lamp.matches(scene.get("off", [])):
        return OFF  # a lamp's own `on = true` beats the scene's off list
    if spec.get("color") and lamp.has_color:
        return Target(on=True, bri=float(spec.get("color_bri", spec.get("bri", 50))), xy=color.hex_to_xy(spec["color"]))
    k = spec.get("k")
    return Target(on=True, bri=float(spec.get("bri", 50)), mirek=color.mirek(k) if k and lamp.mirek_min else None)


def targets(scene_name, cfg, lamps, factor=0.0):
    """{lamp id: Target} for a scene, or {} for states that leave the lamps alone."""
    if scene_name == "bedtime":
        return {lamp.id: OFF for lamp in lamps}
    scene = cfg["scenes"].get(scene_name)
    if scene is None:
        return {}
    return {lamp.id: warm(scene_target(scene, lamp), factor, cfg["night"]) for lamp in lamps}


def choose(raw_lights, rooms, cfg):
    """(every lamp, the lamps room drives) from the bridge's lights and [(room name, ids)].
    The daemon, the CLI and the walkthrough all go through here so they agree."""
    all_lamps = [Lamp.from_v2(d) for d in raw_lights]
    room_ids = None
    if cfg.get("room"):
        room_ids = next((ids for name, ids in rooms if name == cfg["room"]), set())
    return all_lamps, pick(all_lamps, cfg, room_ids)


def pick(all_lamps, cfg, room_lights=None):
    """The lamps room may touch: the named ones, else the configured room, else all."""
    names = cfg.get("lamps") or []
    if names:
        return [lamp for lamp in all_lamps if lamp.name in names]
    if room_lights is not None:
        return [lamp for lamp in all_lamps if lamp.id in room_lights]
    return list(all_lamps)
