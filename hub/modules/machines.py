"""Machines: how each box in the fleet is doing, read from the fleet frontend's state."""

import logging
import time

import httpx

from .. import fleetview, settings
from .common import Cache, notice, unavailable

log = logging.getLogger(__name__)

ID = "machines"
TITLE = "Machines"
TOOLS = []
WRITES = ()
TIMEOUT = 5
DISK_WARN = 85
GONE_AFTER = 86400
GB = 1024 ** 3

_cache = Cache(60, keep_none=True)
CACHES = (_cache,)


def read_state():
    url = settings.get("fleet.url").rstrip("/")
    try:
        response = httpx.get(f"{url}/api/state", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        log.warning("fleet state failed: %s", error)
        return None
    return data if isinstance(data, dict) else None


def state():
    return _cache.get(read_state)


def _gb(value):
    return round(value / GB, 1)


def _days(seconds):
    days = int(seconds // 86400)
    return f"{days} day" if days == 1 else f"{days} days"


def machine(host, at, every):
    system = host.get("system") if isinstance(host.get("system"), dict) else {}
    tried = (host.get("tried") or 0) / 1000
    reached = (host.get("reached") or 0) / 1000
    online = not host.get("error") and at - tried <= max(3 * every, 900)
    warnings = []
    if not online and reached and at - reached > GONE_AFTER:
        warnings.append(f"Unreachable for {_days(at - reached)}")
    disk_total, disk_used = system.get("disk_total"), system.get("disk_used")
    pct = round(disk_used * 100 / disk_total) if online and disk_total and disk_used is not None else None
    if pct is not None and pct >= DISK_WARN:
        warnings.append(f"Disk is {pct}% full")
    mem_total, mem_avail = system.get("mem_total"), system.get("mem_avail")
    known = online and mem_total and mem_avail is not None
    uptime = system.get("uptime")
    load = system.get("load")
    return {
        "name": str(host.get("name") or "unknown"),
        "online": online,
        "uptime": fleetview.fmt_age(uptime) if online and uptime is not None else "",
        "load": round(load, 2) if online and isinstance(load, (int, float)) else None,
        "cpus": system.get("cpus"),
        "mem_used": _gb(mem_total - mem_avail) if known else None,
        "mem_total": _gb(mem_total) if mem_total else None,
        "disk_used_pct": pct,
        "warnings": warnings,
    }


def machines(data):
    at = (data.get("at") or time.time() * 1000) / 1000
    every = (data.get("every") or 300000) / 1000
    rows = [machine(host, at, every) for host in data.get("hosts") or [] if isinstance(host, dict)]
    return sorted(rows, key=lambda row: (not row["online"], row["name"].lower()))


def panel(section):
    data = state()
    if data is None:
        return [unavailable("The machine overview did not answer, so I cannot say how the machines are doing.",
                            title="Machines", source="the machine overview")]
    rows = machines(data)
    if not rows:
        return [notice("No machines are listed yet.", title="Machines")]
    return [{"kind": "machines", "title": "Machines", "items": rows}]
