import json
import logging
import os

import httpx

log = logging.getLogger("diablo.hue")

CONFIG_PATH = os.path.expanduser("~/.config/hue-hands/config.json")

TARGETS = {
    "left": "left", "corner": "left", "corner lamp": "left", "the corner": "left",
    "up": "up", "panel": "up", "ceiling": "up", "surimu": "up", "room": "up",
    "right": "right", "wood": "right", "wood lamp": "right",
}


def _load():
    with open(CONFIG_PATH) as handle:
        return json.load(handle)


def hue_light(action="on", target="all", cfg=None):
    if cfg is not None:
        bridge = {
            "bridge_ip": cfg.get("hue_bridge_ip"),
            "app_key": cfg.get("hue_app_key"),
            "lamps": json.loads(cfg.get("hue_lamps") or "{}"),
        }
    else:
        bridge = _load()
    ip = bridge.get("bridge_ip")
    key = bridge.get("app_key")
    if not ip or not key:
        return "no hue bridge configured"
    lamps = bridge.get("lamps", {})
    names = bridge.get("lamp_names", bridge.get("lamps", {}))
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
    outcomes = []
    for light_id in ids:
        if action == "toggle":
            try:
                current = httpx.get(f"http://{ip}/api/{key}/lights/{light_id}", timeout=5).json()
                patch = {"on": not current.get("state", {}).get("on", False)}
            except (httpx.HTTPError, ValueError) as error:
                log.warning("hue read failed: %s", error)
                return "the bridge did not answer, sir"
        else:
            patch = {"on": action == "on"}
        try:
            response = httpx.put(
                f"http://{ip}/api/{key}/lights/{light_id}/state", json=patch, timeout=5
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            log.warning("hue write failed: %s", error)
            return "the bridge did not answer, sir"
        if any("error" in item for item in body if isinstance(item, dict)):
            outcomes.append(f"{label}: refused")
        else:
            outcomes.append(f"{label}: {action}")
    return f"{', '.join(outcomes)}"
