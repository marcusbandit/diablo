import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8790"
ACTIONS = ("wake", "shutdown", "reboot")
NO_REPORT = "no phone report yet, the phone has not checked in with the fleet viewer"
DESKTOP = "banditbox"


def fleet_url():
    return os.environ.get("DIABLO_FLEET_URL", DEFAULT_URL).rstrip("/")


def fmt_age(seconds):
    if seconds is None:
        return "unknown age"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 24:
        rest = minutes % 60
        return f"{hours} h {rest} min" if rest else f"{hours} h"
    days = hours // 24
    rest = hours % 24
    return f"{days} d {rest} h" if rest else f"{days} d"


def _epoch(value):
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp > 1e12:
        stamp /= 1000.0
    return stamp


def _gb(mb):
    try:
        return f"{float(mb) / 1024:.1f} GB"
    except (TypeError, ValueError):
        return None


def phone_summary(payload, now=None):
    if not isinstance(payload, dict) or not isinstance(payload.get("report"), dict):
        return NO_REPORT
    report = payload["report"]
    now = time.time() if now is None else now
    parts = []
    info = report.get("device_info") or {}
    device = report.get("device") or info.get("model") or "phone"
    age = payload.get("age")
    if age is None:
        stamp = _epoch(report.get("ts")) or _epoch(payload.get("received"))
        age = None if stamp is None else now - stamp
    parts.append(f"{device}, report {fmt_age(age)} old")
    battery = report.get("battery") or {}
    if battery.get("level") is not None:
        charging = ", charging" if battery.get("charging") else ""
        parts.append(f"battery {battery['level']}%{charging}")
    location = report.get("location") or {}
    if location.get("lat") is not None and location.get("lon") is not None:
        place = location.get("place") or "unknown place"
        where = f"{place} ({float(location['lat']):.5f}, {float(location['lon']):.5f}"
        if location.get("accuracy_m") is not None:
            where += f", ±{float(location['accuracy_m']):.0f} m"
        where += ")"
        stamp = _epoch(location.get("ts"))
        if stamp is not None:
            where += f", {fmt_age(now - stamp)} old"
        parts.append("location " + where)
    else:
        parts.append("no location shared")
    clock = report.get("time") or {}
    if clock.get("local"):
        zone = f" {clock['tz']}" if clock.get("tz") else ""
        parts.append(f"local time {clock['local']}{zone}")
    net = report.get("network") or {}
    if net.get("type"):
        ssid = f" ({net['ssid']})" if net.get("ssid") else ""
        parts.append(f"network {net['type']}{ssid}")
    if report.get("screen_on") is not None:
        parts.append("screen on" if report.get("screen_on") else "screen off")
    storage = report.get("storage") or {}
    free = _gb(storage.get("free_mb"))
    if free:
        total = _gb(storage.get("total_mb"))
        parts.append(f"storage {free} free" + (f" of {total}" if total else ""))
    return "; ".join(parts)


def where_summary(seat, state):
    seat = seat if isinstance(seat, dict) else {}
    state = state if isinstance(state, dict) else {}
    at = seat.get("at") or "unknown"
    mode = seat.get("mode") or "unknown"
    reason = seat.get("reason")
    if seat.get("at") or seat.get("mode"):
        line = f"Diablo attends at {at} ({mode}" + (f", {reason})" if reason else ")")
    else:
        line = "The fleet viewer does not report where Diablo attends"
    devices = [d for d in state.get("devices") or [] if isinstance(d, dict)]
    rtt = state.get("rtt") or {}
    me = state.get("self") if isinstance(state.get("self"), dict) else {}
    desktop = next((d for d in devices if d.get("name") == DESKTOP), None)
    if desktop is None and me.get("name") == DESKTOP:
        desktop = {"name": DESKTOP, "self": True}
    if desktop is None:
        line += f". {DESKTOP} is not in the fleet list"
    elif desktop.get("self"):
        line += f". {DESKTOP} is this very machine"
    elif desktop.get("online"):
        ms = rtt.get(DESKTOP)
        line += f". {DESKTOP} is reachable" + (f" ({float(ms):.0f} ms)" if ms is not None else "")
    else:
        line += f". {DESKTOP} is down"
    peers = [d for d in devices if d.get("name") and not d.get("self") and d.get("name") != DESKTOP]
    online = list(dict.fromkeys(d["name"] for d in peers if d.get("online")))
    offline = list(dict.fromkeys(d["name"] for d in peers if not d.get("online")))
    line += ". Online: " + (", ".join(online) if online else "nobody")
    if offline:
        line += "; offline: " + ", ".join(offline)
    return line + "."


def power_line(host, action, payload):
    payload = payload if isinstance(payload, dict) else {}
    detail = str(payload.get("detail") or "").strip()
    if payload.get("ok"):
        return f"{action} sent to {host}" + (f": {detail}" if detail else "")
    return f"{action} for {host} failed" + (f": {detail}" if detail else "")


def _request(path, body=None, timeout=8, parse_error=False):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(fleet_url() + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as error:
        if not parse_error:
            raise
        raw = error.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except ValueError:
            return {"ok": False, "detail": f"http {error.code}: {raw[:200]}"}


HUB_URL = os.environ.get("DIABLO_HUB_URL", "http://diablo-pi:8755").rstrip("/")


def _stored_phone():
    from . import settings

    raw = settings.get("phone.report")
    received = float(settings.get("phone.received") or 0)
    if not raw:
        return None, 0.0
    return json.loads(raw), received


def phone_status(fresh=False):
    """The phone posts its reports to the hub; fresh asks it over /api/events and waits a few seconds."""
    report, received = _stored_phone()
    if fresh:
        try:
            urllib.request.urlopen(urllib.request.Request(HUB_URL + "/api/phone/request", data=b""), timeout=5).read()
        except OSError:
            pass
        deadline = time.time() + 8
        while time.time() < deadline:
            time.sleep(0.5)
            newer, at = _stored_phone()
            if at > received:
                report, received = newer, at
                break
    if report is None:
        return "the phone has not reported to the hub yet"
    return phone_summary({"report": report, "received": received, "age": round(time.time() - received, 1)})


def fleet_power(host, action):
    host = str(host or "").strip()
    action = str(action or "").strip().lower()
    if not host:
        return "which host?"
    if action not in ACTIONS:
        return "action must be wake, shutdown or reboot"
    try:
        payload = _request("/api/power", {"host": host, "action": action}, timeout=30, parse_error=True)
    except (OSError, ValueError) as error:
        return f"the fleet viewer at {fleet_url()} did not answer: {error}"
    return power_line(host, action, payload)


def fleet_where():
    try:
        state = _request("/api/state")
        try:
            seat = _request("/api/seat")
        except urllib.error.HTTPError:
            seat = {}
    except (OSError, ValueError) as error:
        return f"the fleet viewer at {fleet_url()} did not answer: {error}"
    return where_summary(seat, state)
