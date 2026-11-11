"""Values that belong to the box, not to the repo.

The repo is public, so nothing about the people or machines that run it is written in code.
Each box keeps its own values in ~/.config/diablo/local.env (KEY=VALUE lines, the same file
the systemd units read as EnvironmentFile), written by scripts/setup-local.sh. What the box can
answer itself is discovered instead: the tailnet name from tailscale, the home subnet from the
routing table. A value set in the real environment always wins over the file.
"""

import ipaddress
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

PATH = Path(os.environ.get("DIABLO_LOCAL_ENV") or Path.home() / ".config" / "diablo" / "local.env")


def parse(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load(path=PATH):
    try:
        values = parse(Path(path).read_text())
    except OSError:
        return {}
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def get(key, default=""):
    return os.environ.get(key) or default


def _run(argv):
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


@lru_cache(maxsize=1)
def tailnet():
    """The MagicDNS suffix, such as tailXXXX.ts.net: DIABLO_TAILNET, else asked of tailscale."""
    if get("DIABLO_TAILNET"):
        return get("DIABLO_TAILNET").strip(".")
    try:
        status = json.loads(_run(["tailscale", "status", "--json"]) or "{}")
    except ValueError:
        return ""
    return str(status.get("MagicDNSSuffix") or (status.get("CurrentTailnet") or {}).get("MagicDNSSuffix") or "").strip(".")


def host(name):
    """A tailnet host's full name when the tailnet is known, else its short MagicDNS name."""
    suffix = tailnet()
    return f"{name}.{suffix}" if suffix else name


@lru_cache(maxsize=1)
def home_subnet():
    """The LAN this box sits on: DIABLO_HOME_SUBNET, else the private link route the kernel set
    up for the default interface. Empty when neither answers."""
    if get("DIABLO_HOME_SUBNET"):
        return get("DIABLO_HOME_SUBNET")
    default = _run(["ip", "-4", "route", "show", "default"]).split()
    dev = default[default.index("dev") + 1] if "dev" in default else ""
    for line in _run(["ip", "-4", "route", "show", "scope", "link"]).splitlines():
        parts = line.split()
        if not parts or (dev and f"dev {dev}" not in line):
            continue
        try:
            net = ipaddress.ip_network(parts[0], strict=False)
        except ValueError:
            continue
        if net.is_private and not net.subnet_of(ipaddress.ip_network("100.64.0.0/10")):
            return str(net)
    return ""


load()
