import json
import logging
import math

import httpx

from . import settings

log = logging.getLogger("diablo.hue")

TARGETS = {
    "left": "left", "corner": "left", "corner lamp": "left", "the corner": "left",
    "up": "up", "panel": "up", "ceiling": "up", "surimu": "up", "room": "up",
    "right": "right", "wood": "right", "wood lamp": "right",
}

COLOR_NAMES = {
    "red": (255, 0, 0), "orange": (255, 128, 0), "yellow": (255, 200, 0),
    "green": (0, 255, 0), "cyan": (0, 255, 255), "blue": (0, 80, 255),
    "purple": (128, 0, 255), "violet": (128, 0, 255), "pink": (255, 80, 160),
    "magenta": (255, 0, 255), "white": (255, 255, 255),
}

KELVIN_WORDS = {
    "warm": 2500, "warm white": 2700, "neutral": 4000,
    "cool": 5500, "cool white": 6000, "daylight": 5600,
}


def _xy_from_rgb(rgb):
    def gamma(channel):
        channel = channel / 255.0
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    r, g, b = (gamma(c) for c in rgb)
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047585
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    if x + y + z <= 0:
        return (0.3227, 0.3290)
    return (round(x / (x + y + z), 5), round(y / (x + y + z), 5))


def _resolve_color(value):
    text = str(value).strip().lower()
    if text in COLOR_NAMES:
        return _xy_from_rgb(COLOR_NAMES[text])
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 6:
        try:
            return _xy_from_rgb(tuple(int(text[i:i + 2], 16) for i in (0, 2, 4)))
        except ValueError:
            return None
    return None


def _mired(value, cap_min, cap_max):
    text = str(value).strip().lower()
    if text in KELVIN_WORDS:
        kelvin = float(KELVIN_WORDS[text])
    else:
        text = text.rstrip("k")
        number = float(text)
        if 150 <= number <= 500:
            return int(number)
        kelvin = number
    return max(cap_min, min(cap_max, round(1_000_000 / kelvin)))


def hue_light(action="on", target="all", brightness=None, color=None, temperature=None):
    ip = settings.get("hue_bridge_ip")
    key = settings.get("hue_app_key")
    lamps = json.loads(settings.get("hue_lamps") or "{}")
    if not ip or not key or not lamps:
        return "no hue bridge configured"
    names = {"left": "corner lamp", "up": "ceiling panel", "right": "wood lamp"}
    wanted = str(target or "all").lower().strip()
    if wanted in ("all", "everything", "the room"):
        ids = list(lamps.values())
        label = "all lights"
    else:
        slot = TARGETS.get(wanted)
        if slot is None or slot not in lamps:
            return f"unknown light {target}; known: corner lamp, ceiling panel, wood lamp"
        ids = [lamps[slot]]
        label = names.get(slot, slot)
    if action not in ("on", "off", "toggle"):
        return "action must be on, off or toggle"
    if brightness is not None:
        try:
            pct = max(1.0, min(100.0, float(brightness)))
        except (TypeError, ValueError):
            return "brightness must be a number, 1 to 100"
    else:
        pct = None
    xy = _resolve_color(color) if color else None
    if color and xy is None:
        return f"unknown color {color}; use a name like blue or a hex like #ff8800"
    outcomes = []
    for light_id in ids:
        try:
            info = httpx.get(f"http://{ip}/api/{key}/lights/{light_id}", timeout=5).json()
        except (httpx.HTTPError, ValueError) as error:
            log.warning("hue read failed: %s", error)
            return "the bridge did not answer, sir"
        state = info.get("state", {})
        control = info.get("capabilities", {}).get("control", {})
        patch = {}
        parts = []
        if action == "toggle":
            patch["on"] = not state.get("on", False)
            parts.append("on" if patch["on"] else "off")
        elif action == "on" or pct is not None or xy or temperature is not None:
            patch["on"] = True
            parts.append("on")
        else:
            patch["on"] = False
            parts.append("off")
        if pct is not None:
            patch["bri"] = max(1, min(254, round(pct * 254 / 100)))
            parts.append(f"{round(pct)}% brightness")
        if xy:
            if control.get("colorgamut"):
                patch["xy"] = list(xy)
                parts.append(f"color {color}")
            else:
                parts.append(f"no color support for {color}, skipped")
        if temperature is not None:
            ct_cap = control.get("ct") or {"min": 153, "max": 500}
            try:
                patch["ct"] = _mired(temperature, ct_cap["min"], ct_cap["max"])
                parts.append(f"temperature {temperature}")
            except (TypeError, ValueError):
                parts.append("bad temperature, skipped")
        try:
            response = httpx.put(
                f"http://{ip}/api/{key}/lights/{light_id}/state", json=patch, timeout=5
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            log.warning("hue write failed: %s", error)
            return "the bridge did not answer, sir"
        if any(isinstance(item, dict) and "error" in item for item in body):
            outcomes.append(f"{label}: refused")
        else:
            outcomes.append(f"{label}: {', '.join(parts)}")
    return f"{', '.join(outcomes)}"


def _hex_from_xy(xy):
    x, y = (xy + [0.3227, 0.3290])[:2]
    if y <= 0:
        return "#f2f1ee"
    y_n = 1.0
    x_n = (x / y) * y_n
    z_n = ((1.0 - x - y) / y) * y_n
    r, g, b = x_n * 1.4628062 - 0.1840623 - z_n * 0.2743606, -x_n * 0.5217933 + 1.4472381 + z_n * 0.0673228, x_n * 0.0349342 - 0.0968930 + z_n * 1.2884099
    def gamma(c):
        c = max(0.0, min(1.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    rgb = [round(gamma(c) * 255) for c in (r, g, b)]
    if max(rgb) - min(rgb) < 24:
        return "#f2f1ee"
    return "#" + "".join(f"{c:02x}" for c in rgb)


def _bridge():
    ip = settings.get("hue_bridge_ip")
    key = settings.get("hue_app_key")
    lamps = json.loads(settings.get("hue_lamps") or "{}")
    if not ip or not key or not lamps:
        return None
    return ip, key, lamps


def _kelvin(mired):
    try:
        return round(1_000_000 / float(mired))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _hex_from_kelvin(kelvin):
    temp = max(1000, min(40000, kelvin)) / 100
    if temp <= 66:
        r = 255
        g = 99.4708025861 * math.log(temp) - 161.1195681661
    else:
        r = 329.698727446 * (temp - 60) ** -0.1332047592
        g = 288.1221695283 * (temp - 60) ** -0.0755148492
    if temp >= 66:
        b = 255
    elif temp <= 19:
        b = 0
    else:
        b = 138.5177312231 * math.log(temp - 10) - 305.0447927307
    rgb = [round(max(0, min(255, c))) for c in (r, g, b)]
    return "#" + "".join(f"{c:02x}" for c in rgb)


def configured():
    return _bridge() is not None


def lamp_states():
    """The real state of every known lamp, for the ledger's lights object, from one read of the bridge."""
    found = _bridge()
    if found is None:
        return []
    ip, key, lamps = found
    try:
        every = httpx.get(f"http://{ip}/api/{key}/lights", timeout=5).json()
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hue read failed: %s", error)
        return []
    if not isinstance(every, dict):
        return []
    names = {"left": "corner lamp", "up": "ceiling panel", "right": "wood lamp"}
    out = []
    for slot in ("left", "up", "right"):
        info = every.get(str(lamps.get(slot)))
        if not isinstance(info, dict):
            continue
        state = info.get("state", {})
        control = info.get("capabilities", {}).get("control", {})
        span = control.get("ct") or {}
        low, high = _kelvin(span.get("max")), _kelvin(span.get("min"))
        out.append({
            "id": slot,
            "name": names.get(slot, slot),
            "on": bool(state.get("on")),
            "color": _hex_from_xy(state.get("xy") or []),
            "bri": state.get("bri"),
            "reachable": state.get("reachable") is not False,
            "ct": _kelvin(state.get("ct")) if state.get("ct") else None,
            "caps": {
                "color": bool(control.get("colorgamut")),
                "ct": [low, high] if low and high else None,
            },
        })
    return out


def _scene_colors(lightstates):
    colors = []
    for state in (lightstates or {}).values():
        if not isinstance(state, dict) or state.get("on") is False:
            continue
        if state.get("xy"):
            color = _hex_from_xy(list(state["xy"]))
        elif state.get("ct"):
            kelvin = _kelvin(state["ct"])
            color = _hex_from_kelvin(kelvin) if kelvin else None
        else:
            color = None
        if color and color not in colors:
            colors.append(color)
    return colors[:4]


def _read_scenes():
    found = _bridge()
    if found is None:
        return None
    ip, key, lamps = found
    try:
        every = httpx.get(f"http://{ip}/api/{key}/scenes", timeout=5).json()
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hue scenes read failed: %s", error)
        return None
    if not isinstance(every, dict):
        return None
    mine = {str(light) for light in lamps.values()}
    out = []
    for scene_id, info in every.items():
        if not isinstance(info, dict) or not info.get("name"):
            log.debug("hue scene %s skipped: no name", scene_id)
            continue
        if not mine & {str(light) for light in info.get("lights") or []}:
            log.debug("hue scene %s skipped: none of the room's lamps", scene_id)
            continue
        item = {"id": scene_id, "name": str(info["name"]), "group": str(info.get("group") or "0")}
        colors = _scene_colors(info.get("lightstates"))
        if colors:
            item["colors"] = colors
        out.append(item)
    return sorted(out, key=lambda item: (item["name"].lower(), item["id"]))


def scenes():
    """The room's scenes on the bridge, by name, with the group each is recalled through and swatch colours when listed."""
    return _read_scenes() or []


def recall_scene(scene_id, group=None, name=None):
    found = _bridge()
    if found is None:
        return "no hue bridge configured"
    ip, key, _ = found
    if group is None:
        listed = _read_scenes()
        if listed is None:
            return "the bridge did not answer, sir"
        scene = next((item for item in listed if item["id"] == scene_id), None)
        if scene is None:
            return f"unknown scene {scene_id}"
        group, name = scene["group"], scene["name"]
    name = name or scene_id
    try:
        response = httpx.put(f"http://{ip}/api/{key}/groups/{group}/action", json={"scene": scene_id}, timeout=5)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        log.warning("hue scene write failed: %s", error)
        return "the bridge did not answer, sir"
    if any(isinstance(item, dict) and "error" in item for item in body):
        return f"{name}: refused"
    return f"{name}: scene on"
