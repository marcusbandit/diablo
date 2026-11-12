import hashlib
import logging
import os
import re
import secrets
import socket
import time
import urllib.parse

from . import db
from .config import get_config

logger = logging.getLogger(__name__)

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL_S = 600
MAX_FAILURES = 20
GLOBAL_FAILURES = 200
TOUCH_EVERY_S = 60
DEFAULT_URL = "http://diablo-pi:8755"
PLACEHOLDERS = {"", "CHANGE_ME", "..."}

_failures = {}
_touched = {}


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalize(code):
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def pretty(code):
    return code[:4] + "-" + code[4:]


def public_url():
    url = os.environ.get("DIABLO_HUB_URL") or str(get_config().get("hub_url") or "") or DEFAULT_URL
    return url.rstrip("/")


def hub_name():
    return socket.gethostname()


def master_ok(token):
    master = str(get_config().get("hub_token") or "")
    return master not in PLACEHOLDERS and bool(token) and secrets.compare_digest(token.encode(), master.encode())


def device_for(token):
    if not token:
        return None
    device = db.device_by_token(_hash(token))
    if device:
        now = time.time()
        if now - _touched.get(device["id"], 0) > TOUCH_EVERY_S:
            _touched[device["id"]] = now
            db.touch_device(device["id"])
    return device


def authorized(token):
    """The caller behind a token: {"master": True} for hub_token, the device row for a paired device, else None."""
    token = token or ""
    if master_ok(token):
        return {"master": True}
    device = device_for(token)
    if device:
        return {"master": False, "device": device}
    return None


KINDS = {
    "desktop": "desktop app",
    "phone": "phone app",
    "web": "web app",
    # The ears speak the reply, so the brain should answer in a few plain spoken sentences.
    # The words are a Whisper transcript, so the brain must read them as heard speech.
    "voice": "spoken aloud at his desk on banditbox, transcribed by Whisper, the reply is spoken aloud",
}


SPOKEN_ON = "spoken aloud on {host}, transcribed by Whisper, the reply is spoken aloud"


def voice_kind(host=""):
    """The spoken kind for the box whose ears heard it; banditbox, or no box named, is his desk."""
    host = re.sub(r"[^\w.-]", "", str(host or "").strip().lower())[:40]
    return KINDS["voice"] if not host or host == "banditbox" else SPOKEN_ON.format(host=host)


def origin_for(caller, source, host=""):
    """Which client and which device a message came from, for the per-turn bracket.
    A spoken turn names the box whose ears heard it when that is not banditbox."""
    source = str(source or "").strip() or "fleet"
    kind = voice_kind(host) if source == "voice" else KINDS.get(source, f"{source} app")
    device = (caller or {}).get("device") or {}
    name = str(device.get("name") or "").strip()
    return f"{kind} on {name}" if name else kind


def mint(base=None, by=None):
    """A new pairing code; by is the device that asked, so revoking it also revokes what it paired."""
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    expires = time.time() + CODE_TTL_S
    db.add_pair_code(_hash(code), expires, by)
    base = (base or public_url()).rstrip("/")
    return {
        "code": pretty(code),
        "expires_at": expires,
        "ttl": CODE_TTL_S,
        "url": f"{base}/app/pair.html?code={code}",
        "link": "diablo://pair?" + urllib.parse.urlencode({"host": base, "code": code}),
    }


def status(code):
    row = db.pair_code(_hash(normalize(code)))
    if not row:
        return {"state": "unknown"}
    if row["used"]:
        return {"state": "paired", "device": row["device_name"]}
    if row["expires"] <= time.time():
        return {"state": "expired"}
    return {"state": "waiting", "expires_at": row["expires"]}


def _recent(client):
    cutoff = time.time() - CODE_TTL_S
    for key in list(_failures):
        _failures[key] = [t for t in _failures[key] if t > cutoff]
        if not _failures[key]:
            del _failures[key]
    return _failures.get(client, [])


def locked(client=""):
    return len(_recent(client)) >= MAX_FAILURES


def redeem(code, name, client=""):
    """Trades a live code for a new device token. Returns (token, device_id, name) or None."""
    if locked(client):
        return None
    code = normalize(code)
    name = " ".join(str(name or "").split())[:60] or "device"
    token = secrets.token_urlsafe(32)
    device_id = db.redeem_pair_code(_hash(code), name, _hash(token)) if len(code) == CODE_LENGTH else None
    if device_id is None:
        _failures.setdefault(client, []).append(time.time())
        if sum(len(v) for v in _failures.values()) >= GLOBAL_FAILURES:
            logger.warning("too many wrong pairing codes, burning every open code")
            db.burn_pair_codes()
            _failures.clear()
        return None
    logger.info("paired device %s (%s)", device_id, name)
    return token, device_id, name


def door_open(client, login):
    """True when a request came through Diablo's own tailnet name for one of his owners.

    Tailscale Serve on the `diablo` node adds Tailscale-User-Login to every request it
    proxies. Anyone on the tailnet could send that header straight to the hub port, so it
    only counts when the request arrives from a proxy address listed in door_proxies.
    """
    door = get_config().get("door") or {}
    proxies = {str(ip) for ip in door.get("proxies") or []}
    owners = {str(o).lower() for o in door.get("owners") or []}
    return bool(client) and client in proxies and bool(login) and login.lower() in owners


DOOR_PER_HOUR = 20
_admitted = []


def admit(name):
    """Mints a device token for a browser the tailnet door let in. Returns (token, device_id, name),
    or None past DOOR_PER_HOUR admissions, so a loop cannot fill the device list."""
    now = time.time()
    _admitted[:] = [t for t in _admitted if t > now - 3600]
    if len(_admitted) >= DOOR_PER_HOUR:
        logger.warning("tailnet door: %s admissions in the last hour, refusing more", len(_admitted))
        return None
    _admitted.append(now)
    name = " ".join(str(name or "").split())[:60] or "browser"
    token = secrets.token_urlsafe(32)
    device_id = db.create_device(name, _hash(token))
    logger.info("tailnet door admitted device %s (%s)", device_id, name)
    return token, device_id, name


def revoke(device_id):
    ids = db.revoke_device(device_id)
    for i in ids:
        _touched.pop(i, None)
    return ids


def weak_master():
    master = str(get_config().get("hub_token") or "")
    return len(master) < 32


def pair_text(minted):
    return (
        f"Pairing code {minted['code']}, good for {CODE_TTL_S // 60} minutes and one device.\n"
        "On the phone: open the Diablo app, go to the pairing step (or Edit, then Pair this phone) and type the code.\n"
        f"Or open this with Tailscale on: {minted['url']}"
    )


def devices_text():
    rows = db.list_devices()
    if not rows:
        return "No paired devices yet. /pair makes a code."
    lines = ["Paired devices:"]
    for row in rows:
        seen = time.strftime("%d %b %H:%M", time.localtime(row["last_seen"] or row["created"]))
        lines.append(f"{row['id']}. {row['name']}, last seen {seen}")
    lines.append("/devices revoke <number> locks one out.")
    return "\n".join(lines)


def revoked_text(ids):
    head, rest = ids[0], ids[1:]
    text = f"Device {head} is locked out."
    if rest:
        text += " So are the devices it paired: " + ", ".join(str(i) for i in rest) + "."
    return text
