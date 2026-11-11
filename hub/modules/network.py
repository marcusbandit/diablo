"""Network: whether Marcus is home, and every device on the tailnet, read from tailscale on this box."""

import datetime as dt
import ipaddress
import json
import logging
import re
import subprocess
import time

from .. import settings
from .common import Cache, unavailable

log = logging.getLogger(__name__)

ID = "network"
TITLE = "Network"
TOOLS = []
WRITES = ()
TIMEOUT = 5
_cache = Cache(30, keep_none=True)
CACHES = (_cache,)
HOME_MEMORY = 4 * 3600
_home_seen = [0.0]
PHONES = ("android", "ios")
OS_NAMES = {
    "android": "Android", "ios": "iOS", "linux": "Linux", "macos": "macOS", "windows": "Windows",
    "freebsd": "FreeBSD", "tvos": "tvOS",
}
KINDS = {
    "banditbox": "desktop", "computer": "desktop", "coach": "desktop", "kangaeru": "laptop",
    "nemurenai": "server", "homehub": "server", "diablo-pi": "server", "diablo": "server",
}
FRACTION = re.compile(r"(\.\d{6})\d+")


def read_status():
    try:
        done = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        log.warning("tailscale status failed: %s", error)
        return None
    if done.returncode != 0:
        log.warning("tailscale status exited %s", done.returncode)
        return None
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def status():
    return _cache.get(read_status)


def seen_at(value):
    if not isinstance(value, str) or value.startswith("0001"):
        return None
    try:
        moment = dt.datetime.fromisoformat(FRACTION.sub(r"\1", value.replace("Z", "+00:00")))
    except ValueError:
        return None
    return moment.timestamp()


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    if seconds < 172800:
        return "yesterday"
    return f"{seconds // 86400} days ago"


def overrides():
    out = {}
    for part in settings.get("network.kinds").split(","):
        name, _, kind = part.partition("=")
        if name.strip() and kind.strip():
            out[name.strip().lower()] = kind.strip().lower()
    return out


def kind_of(name, os_name):
    name = str(name).lower()
    chosen = overrides().get(name)
    if chosen:
        return chosen
    os_name = str(os_name or "").lower()
    if os_name in PHONES:
        return "phone"
    if os_name in ("macos", "darwin"):
        return "laptop"
    return KINDS.get(name, "other")


def on_home_network(addr):
    text = str(addr or "")
    if not text:
        return False
    host = text[1:].partition("]")[0] if text.startswith("[") else text.rpartition(":")[0]
    try:
        return ipaddress.ip_address(host) in ipaddress.ip_network(settings.get("network.home_subnet"), strict=False)
    except ValueError:
        return False


def how(peer):
    if not peer.get("Online"):
        return ""
    if peer.get("CurAddr"):
        return "home network" if on_home_network(peer["CurAddr"]) else "direct"
    return "relayed" if peer.get("Active") else ""


def devices(data, now):
    rows = []
    mine = [data.get("Self")] if isinstance(data.get("Self"), dict) else []
    for index, peer in enumerate(mine + list((data.get("Peer") or {}).values())):
        if not isinstance(peer, dict):
            continue
        name = str(peer.get("HostName") or (peer.get("DNSName") or "").split(".")[0] or "unknown")
        online = bool(peer.get("Online"))
        stamp = seen_at(peer.get("LastSeen"))
        if online:
            last_seen = "now"
        elif stamp is None:
            last_seen = "not seen yet"
        else:
            last_seen = ago(now - stamp)
        os_name = str(peer.get("OS") or "")
        rows.append({
            "name": name, "kind": kind_of(name, os_name), "online": online, "never_seen": not online and stamp is None,
            "how": "" if index < len(mine) else how(peer), "last_seen": last_seen,
            "os": OS_NAMES.get(os_name.lower(), os_name),
        })
    return sorted(rows, key=lambda row: (not row["online"], row["name"].lower()))


def phone_peer(data):
    peers = [peer for peer in (data.get("Peer") or {}).values()
             if isinstance(peer, dict) and str(peer.get("OS") or "").lower() in PHONES]
    named = settings.get("network.phone").strip().lower()
    if named:
        peers = [peer for peer in peers if str(peer.get("HostName") or "").lower() == named]
    peers.sort(key=lambda peer: (not peer.get("Online"), -(seen_at(peer.get("LastSeen")) or 0)))
    return peers[0] if peers else None


def presence(data, now):
    phone = phone_peer(data)
    if phone is None:
        named = settings.get("network.phone").strip()
        caption = f"No phone named {named} on the tailnet" if named else "No phone on the tailnet"
        return {"label": "Marcus", "value": "Unknown", "icon": "home"}, caption
    if not phone.get("Online"):
        stamp = seen_at(phone.get("LastSeen"))
        seen = "Phone is offline" if stamp is None else f"Phone last seen {ago(now - stamp)}"
        return {"label": "Marcus", "value": "Unknown", "icon": "home"}, seen
    endpoints = [phone.get("CurAddr")] + list(phone.get("Addrs") or [])
    if any(on_home_network(addr) for addr in endpoints):
        _home_seen[0] = now
        return {"label": "Marcus", "value": "Home", "icon": "home"}, "Phone is on the home network"
    if phone.get("CurAddr"):
        return {"label": "Marcus", "value": "Away", "icon": "home"}, "Phone is online away from home"
    if now - _home_seen[0] <= HOME_MEMORY:
        return {"label": "Marcus", "value": "Home", "icon": "home"}, f"Phone was on the home network {ago(now - _home_seen[0])}"
    return {"label": "Marcus", "value": "Unknown", "icon": "home"}, "Phone is online but idle, so where it is is unclear"


def panel(section):
    data = status()
    if data is None:
        return [unavailable("Tailscale did not answer on this hub, so I cannot say where you are or which devices are up.",
                            title="Network", source="Tailscale")]
    now = time.time()
    item, caption = presence(data, now)
    return [
        {"kind": "stats", "title": "Network", "caption": caption, "items": [item]},
        {"kind": "devices", "title": "Devices", "items": devices(data, now)},
    ]
