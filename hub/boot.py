"""Booting his desktop straight from the Pi: wake-on-LAN, then the tailnet says whether it came up.

The fleet viewer's power route runs on the desktop itself, so it can never wake it. The Pi sits on the
same LAN, sends the magic packet and pings the desktop over Tailscale until it answers. The host is the
boot.host setting or DIABLO_DESKTOP_HOST in local.env; the MAC is the fleet.<host>.mac setting or
DIABLO_WOL_MAC.
"""

import ipaddress
import subprocess
import time

from . import localenv, settings

DEFAULT_TIMEOUT_S = 120
POLL_S = 5


def host():
    return str(settings.get("boot.host") or localenv.get("DIABLO_DESKTOP_HOST") or "").strip()


def timeout_s():
    try:
        return max(10.0, float(settings.get("boot.timeout_s") or DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return float(DEFAULT_TIMEOUT_S)


def mac():
    name = host()
    return str((settings.get(f"fleet.{name}.mac") if name else "") or localenv.get("DIABLO_WOL_MAC") or "").strip()


def _run(argv, timeout):
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return done.returncode == 0, (done.stdout or "") + (done.stderr or "")


def is_up(name=None):
    """Whether the host answers a tailnet ping now. DERP counts: up is up, direct or not."""
    name = name or host()
    if not name:
        return False
    ok, out = _run(["tailscale", "ping", "-c", "1", "--until-direct=false", "--timeout", "3s", name], 8)
    return ok and "pong" in out


def send_wake():
    """Sends the magic packet, plain and to the home subnet's broadcast. True when one went out."""
    address = mac()
    if not address:
        return False
    attempts = [["wakeonlan", address]]
    subnet = str(settings.get("network.home_subnet") or "")
    try:
        attempts.append(["wakeonlan", "-i", str(ipaddress.ip_network(subnet, strict=False).broadcast_address), address])
    except ValueError:
        pass
    return any([_run(argv, 10)[0] for argv in attempts])


def wait_up(limit=None, poll=POLL_S, clock=time.monotonic, sleep=time.sleep):
    """Pings until the host answers or the limit passes. Seconds it took, or None when it never answered."""
    started = clock()
    limit = timeout_s() if limit is None else limit
    while True:
        if is_up():
            return clock() - started
        if clock() - started >= limit:
            return None
        sleep(poll)


def start():
    """One boot request: "up" when it already answers and nothing was sent, "sent", "no_packet", or
    "no_host" when this box does not know which machine the desktop is."""
    if not host():
        return "no_host"
    if is_up():
        return "up"
    return "sent" if send_wake() else "no_packet"


def outcome_line(seconds, limit=None):
    if seconds is None:
        minutes = round((timeout_s() if limit is None else limit) / 60, 1)
        span = f"{minutes:g} minute" + ("" if minutes == 1 else "s")
        return (f"Your PC did not come up within {span} of the wake packet, sir. "
                "Wake-on-LAN may be off, or it lost power.")
    return f"Your PC is up, sir. It answered after {round(seconds)} seconds."
