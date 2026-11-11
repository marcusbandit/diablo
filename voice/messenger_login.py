"""Logging him in to read your chats, from the terminal, the way Beeper does it.

Telegram: the official user API. Missing app credentials are asked for, with
the page to get them from, then Telethon asks for your phone and the code.

Discord: the remote auth gateway. A QR code is drawn in the terminal, you scan
it in the Discord app on your phone and approve, and the token arrives here
encrypted to a key made for this one login. Nothing is copied from devtools.
Then every server you are in is listed, you search and pick, and the choice is
saved. Your DMs are always read.
"""

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

from . import messages

REMOTE_AUTH = "wss://remote-auth-gateway.discord.gg/?v=2"
LOGIN_URL = "https://discord.com/api/v9/users/@me/remote-auth/login"
TELEGRAM_APPS = "https://my.telegram.org/apps"


# ---- config writing ----------------------------------------------------------------

def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def save_config(cfg, path=None):
    """Writes the messengers file, private to him, sections in a stable order."""
    path = Path(path) if path else messages.CONFIG
    lines = ["# Diablo reads these, never writes to them. Keep this file private.", ""]
    for section in sorted(cfg):
        values = cfg[section] or {}
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {toml_value(value)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        handle.write("\n".join(lines))
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def ask(prompt, default=""):
    shown = f"{prompt} [{default}] " if default else f"{prompt} "
    value = input(shown).strip()
    return value or default


# ---- telegram -----------------------------------------------------------------------

def telegram(cfg):
    tg = dict(cfg.get("telegram") or {})
    if not tg.get("api_id") or not tg.get("api_hash"):
        print("Telegram needs an app of your own. Open this page, log in, create an app, and copy two values:")
        print("  " + TELEGRAM_APPS)
        tg["api_id"] = int(ask("App api_id:"))
        tg["api_hash"] = ask("App api_hash:")
        cfg["telegram"] = tg
        save_config(cfg)
        print("saved.")
    else:
        cfg["telegram"] = tg
    print("Now Telegram itself: your phone number with country code, then the code it sends you.")
    messages.telegram_login(cfg)
    return 0


# ---- discord: QR login ---------------------------------------------------------------

def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return key, base64.b64encode(spki).decode()


def _decrypt(key, payload_b64):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    return key.decrypt(
        base64.b64decode(payload_b64),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )


def _show_qr(url):
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(url)


def discord_qr_token(timeout_s=150, show=_show_qr, out=print):
    """Runs the remote auth dance. Returns (token, user_line) or raises RuntimeError."""
    from websockets.sync.client import connect

    key, public_b64 = _keypair()
    deadline = time.monotonic() + timeout_s
    with connect(REMOTE_AUTH, additional_headers={"Origin": "https://discord.com"}, open_timeout=20) as ws:
        heartbeat_every = 40.0
        next_beat = time.monotonic() + heartbeat_every
        user_line = ""
        while time.monotonic() < deadline:
            try:
                raw = ws.recv(timeout=max(0.5, min(5.0, next_beat - time.monotonic())))
            except TimeoutError:
                raw = None
            if time.monotonic() >= next_beat:
                ws.send(json.dumps({"op": "heartbeat"}))
                next_beat = time.monotonic() + heartbeat_every
            if raw is None:
                continue
            event = json.loads(raw)
            op = event.get("op")
            if op == "hello":
                heartbeat_every = max(10.0, float(event.get("heartbeat_interval", 41250)) / 1000.0 * 0.9)
                next_beat = time.monotonic() + heartbeat_every
                ws.send(json.dumps({"op": "init", "encoded_public_key": public_b64}))
            elif op == "nonce_proof":
                nonce = _decrypt(key, event["encrypted_nonce"])
                proof = base64.urlsafe_b64encode(hashlib.sha256(nonce).digest()).decode().rstrip("=")
                ws.send(json.dumps({"op": "nonce_proof", "proof": proof}))
            elif op == "pending_remote_init":
                out("Scan this in the Discord app on your phone: Settings, Scan QR Code. Then approve.")
                show(f"https://discord.com/ra/{event['fingerprint']}")
            elif op == "pending_ticket":
                payload = _decrypt(key, event["encrypted_user_payload"]).decode()
                parts = payload.split(":")
                user_line = parts[3] if len(parts) > 3 else payload
                out(f"phone says: {user_line}. Approve it there.")
            elif op == "pending_login":
                response = httpx.post(
                    LOGIN_URL,
                    json={"ticket": event["ticket"]},
                    headers={"User-Agent": messages.BROWSER_UA, "Origin": "https://discord.com"},
                    timeout=20,
                )
                response.raise_for_status()
                token = _decrypt(key, response.json()["encrypted_token"]).decode()
                return token, user_line
            elif op == "cancel":
                raise RuntimeError("you declined on the phone")
            elif op == "heartbeat_ack":
                continue
    raise RuntimeError("the QR code expired before it was scanned")


# ---- discord: choosing servers ---------------------------------------------------------

def filter_guilds(guilds, query):
    query = query.lower().strip()
    return [g for g in guilds if query in (g.get("name") or "").lower()]


def pick_guilds(guilds, chosen_ids, read=input, out=print):
    """Search and toggle until done. Returns the chosen ids in list order."""
    chosen = set(chosen_ids)
    shown = list(guilds)
    while True:
        out("")
        for index, guild in enumerate(shown, start=1):
            mark = "x" if guild["id"] in chosen else " "
            out(f"  [{mark}] {index:>2}  {guild.get('name')}")
        out(f"{len(chosen)} chosen. Type to search, a number to toggle, 'all' to show all, 'done' to finish.")
        answer = read("> ").strip()
        if answer.lower() in ("done", "d", ""):
            return [g["id"] for g in guilds if g["id"] in chosen]
        if answer.lower() == "all":
            shown = list(guilds)
            continue
        if answer.isdigit():
            index = int(answer) - 1
            if 0 <= index < len(shown):
                gid = shown[index]["id"]
                chosen.symmetric_difference_update({gid})
            continue
        shown = filter_guilds(guilds, answer) or shown


def discord(cfg):
    dc = dict(cfg.get("discord") or {})
    token = dc.get("token")
    if token:
        keep = ask("A Discord login is already saved. Keep it and just pick servers? (y/n)", "y").lower()
        if keep.startswith("n"):
            token = ""
    if not token:
        token, who = discord_qr_token()
        dc["token"] = token
        print(f"logged in as {who}.")
        cfg["discord"] = dc
        save_config(cfg)
    with httpx.Client(headers={"Authorization": token, "User-Agent": messages.BROWSER_UA}, timeout=15) as client:
        response = client.get(f"{messages.DISCORD_API}/users/@me/guilds")
        response.raise_for_status()
        guilds = sorted(response.json(), key=lambda g: (g.get("name") or "").lower())
    print(f"You are in {len(guilds)} servers. Your DMs are always read; pick the servers he may read too.")
    chosen = pick_guilds(guilds, [str(s) for s in dc.get("servers") or []])
    dc["servers"] = chosen
    dc["server_names"] = [g.get("name") for g in guilds if g["id"] in chosen]
    dc.setdefault("channels", [])
    cfg["discord"] = dc
    save_config(cfg)
    print("saved: " + (", ".join(dc["server_names"]) or "no servers, DMs only"))
    return 0


def main(argv):
    cfg = messages.load_config()
    which = (argv[0] if argv else "").lower()
    if which not in ("telegram", "discord"):
        print("  1  telegram   (api credentials once, then your phone and a code)")
        print("  2  discord    (scan a QR code in the Discord app, then pick servers)")
        which = {"1": "telegram", "2": "discord"}.get(ask("which?"), "")
    if which == "telegram":
        return telegram(cfg)
    if which == "discord":
        return discord(cfg)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
