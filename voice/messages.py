"""Reading his own messages: Telegram through its user API, Discord through his own token.

Read only, on request, never a word sent. Telegram reports what its own unread
counts say. Discord has no unread state on the REST side, so he remembers the
last message id he saw per chat and reports what came after it.

Config lives at ~/.config/diablo/messengers.toml:

    [telegram]
    api_id = 12345
    api_hash = "..."
    phone = "+45..."

    [discord]
    token = "..."
    servers = ["French with friends", "Another server"]   # names or ids
    channels = []                                          # optional channel ids

Run `diablo messages login` once for Telegram; it asks for the code in the terminal.
"""

import asyncio
import json
import logging
import os
import sys
import time
import tomllib
from pathlib import Path

import httpx

log = logging.getLogger("diablo.messages")

CONFIG = Path.home() / ".config/diablo/messengers.toml"
SEEN = Path.home() / ".local/state/diablo/messages-seen.json"
SESSION = Path.home() / ".local/share/diablo/telegram"

DISCORD_API = "https://discord.com/api/v10"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
PER_CHAT = 5


def load_config():
    try:
        with open(CONFIG, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        log.warning("messengers config unreadable: %s", error)
        return {}


def load_seen():
    try:
        return json.loads(SEEN.read_text())
    except (OSError, ValueError):
        return {}


def save_seen(seen):
    try:
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEEN.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen))
        os.replace(tmp, SEEN)
    except OSError as error:
        log.warning("could not save seen state: %s", error)


def row(source, chat, sender, text, when, mine=False):
    return {
        "source": source,
        "chat": chat,
        "from": sender,
        "text": " ".join(str(text or "").split())[:240],
        "when": float(when or 0),
        "mine": bool(mine),
    }


# ---- telegram ---------------------------------------------------------------

def _telegram_client(cfg):
    from telethon import TelegramClient

    tg = cfg.get("telegram") or {}
    if not tg.get("api_id") or not tg.get("api_hash"):
        raise RuntimeError("telegram needs api_id and api_hash in messengers.toml")
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(SESSION), int(tg["api_id"]), str(tg["api_hash"]))


def _sender_name(message):
    sender = getattr(message, "sender", None)
    if sender is None:
        return "someone"
    for attr in ("first_name", "title", "username"):
        value = getattr(sender, attr, None)
        if value:
            return str(value)
    return "someone"


async def _telegram_new(cfg, dialogs_limit=40):
    rows = []
    client = _telegram_client(cfg)
    async with client:
        if not await client.is_user_authorized():
            raise RuntimeError("telegram not logged in: run diablo messages login")
        async for dialog in client.iter_dialogs(limit=dialogs_limit):
            unread = int(getattr(dialog, "unread_count", 0) or 0)
            if unread <= 0:
                continue
            async for message in client.iter_messages(dialog.entity, limit=min(unread, PER_CHAT)):
                if not getattr(message, "text", None):
                    continue
                rows.append(row("telegram", dialog.name, _sender_name(message), message.text,
                                message.date.timestamp() if message.date else 0, message.out))
    return rows


async def _telegram_read(cfg, query, limit=PER_CHAT):
    rows = []
    wanted = query.lower().strip()
    client = _telegram_client(cfg)
    async with client:
        if not await client.is_user_authorized():
            raise RuntimeError("telegram not logged in: run diablo messages login")
        async for dialog in client.iter_dialogs(limit=100):
            if wanted in (dialog.name or "").lower():
                async for message in client.iter_messages(dialog.entity, limit=limit):
                    if getattr(message, "text", None):
                        rows.append(row("telegram", dialog.name, _sender_name(message), message.text,
                                        message.date.timestamp() if message.date else 0, message.out))
                break
    return rows


def telegram_login(cfg):
    """Interactive: Telethon asks for the code (and a password if set) on stdin."""
    client = _telegram_client(cfg)
    phone = (cfg.get("telegram") or {}).get("phone")
    with client:
        client.start(phone=phone)
        me = client.get_me()
        print(f"logged in as {me.first_name} ({me.phone}); session kept at {SESSION}.session")


# ---- discord ------------------------------------------------------------------

def _discord_headers(cfg):
    token = str((cfg.get("discord") or {}).get("token") or "").strip()
    if not token:
        raise RuntimeError("discord needs a token in messengers.toml")
    return {"Authorization": token, "User-Agent": BROWSER_UA}


def _get(client, path, **params):
    response = client.get(f"{DISCORD_API}{path}", params=params or None)
    if response.status_code == 429:
        wait = float(response.headers.get("Retry-After", "1"))
        time.sleep(min(wait, 5.0))
        response = client.get(f"{DISCORD_API}{path}", params=params or None)
    response.raise_for_status()
    return response.json()


def dm_name(channel):
    names = [
        r.get("global_name") or r.get("username") or "?"
        for r in channel.get("recipients") or []
    ]
    return channel.get("name") or ", ".join(names) or "dm"


def newest_after(messages, seen_id):
    """Messages newer than the seen snowflake, oldest first."""
    fresh = [m for m in messages if int(m.get("id", 0)) > int(seen_id or 0)]
    return sorted(fresh, key=lambda m: int(m.get("id", 0)))


def _discord_message_rows(messages, chat, me_id):
    rows = []
    for m in messages:
        author = m.get("author") or {}
        text = m.get("content") or ""
        if not text and m.get("attachments"):
            text = "(attachment)"
        if not text:
            continue
        stamp = m.get("timestamp") or ""
        try:
            from datetime import datetime

            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            when = 0.0
        rows.append(row("discord", chat, author.get("global_name") or author.get("username") or "?",
                        text, when, author.get("id") == me_id))
    return rows


def _wanted_guilds(cfg, guilds):
    wanted = [str(s).lower() for s in (cfg.get("discord") or {}).get("servers") or []]
    if not wanted:
        return []
    return [g for g in guilds if g.get("id") in wanted or (g.get("name") or "").lower() in wanted]


def discord_new(cfg, seen, first_run_quiet=True):
    """New messages in his DMs and chosen servers since he last looked."""
    rows = []
    state = seen.setdefault("discord", {})
    with httpx.Client(headers=_discord_headers(cfg), timeout=15) as client:
        me = _get(client, "/users/@me")
        me_id = me.get("id")
        channels = []
        for channel in _get(client, "/users/@me/channels"):
            channels.append((channel.get("id"), dm_name(channel), channel.get("last_message_id")))
        chosen_channels = {str(c) for c in (cfg.get("discord") or {}).get("channels") or []}
        for guild in _wanted_guilds(cfg, _get(client, "/users/@me/guilds")):
            for channel in _get(client, f"/guilds/{guild['id']}/channels"):
                if channel.get("type") != 0:
                    continue
                if chosen_channels and channel.get("id") not in chosen_channels:
                    continue
                label = f"{guild.get('name')} #{channel.get('name')}"
                channels.append((channel.get("id"), label, channel.get("last_message_id")))
        for channel_id, label, last_id in channels:
            if not channel_id or not last_id:
                continue
            seen_id = state.get(channel_id)
            if seen_id is None:
                # First look at this chat: remember where it stands, report nothing.
                state[channel_id] = str(last_id)
                if first_run_quiet:
                    continue
                seen_id = "0"
            if int(last_id) <= int(seen_id):
                continue
            try:
                messages = _get(client, f"/channels/{channel_id}/messages", after=seen_id, limit=PER_CHAT)
            except httpx.HTTPError as error:
                log.warning("discord channel %s unreadable: %s", label, error)
                continue
            fresh = newest_after(messages, seen_id)
            rows.extend(r for r in _discord_message_rows(fresh, label, me_id) if not r["mine"])
            state[channel_id] = str(last_id)
    return rows


def discord_read(cfg, query, limit=PER_CHAT):
    wanted = query.lower().strip()
    with httpx.Client(headers=_discord_headers(cfg), timeout=15) as client:
        me_id = _get(client, "/users/@me").get("id")
        for channel in _get(client, "/users/@me/channels"):
            label = dm_name(channel)
            if wanted in label.lower():
                messages = _get(client, f"/channels/{channel['id']}/messages", limit=limit)
                return _discord_message_rows(sorted(messages, key=lambda m: int(m.get("id", 0))), label, me_id)
        for guild in _wanted_guilds(cfg, _get(client, "/users/@me/guilds")):
            for channel in _get(client, f"/guilds/{guild['id']}/channels"):
                if channel.get("type") != 0:
                    continue
                label = f"{guild.get('name')} #{channel.get('name')}"
                if wanted in label.lower():
                    messages = _get(client, f"/channels/{channel['id']}/messages", limit=limit)
                    return _discord_message_rows(sorted(messages, key=lambda m: int(m.get("id", 0))), label, me_id)
    return []


# ---- the two things he can do ---------------------------------------------------

def check(source="all"):
    """New messages, as rows, plus a list of what failed."""
    cfg = load_config()
    rows, problems = [], []
    if source in ("all", "telegram") and cfg.get("telegram"):
        try:
            rows.extend(asyncio.run(_telegram_new(cfg)))
        except Exception as error:
            problems.append(f"telegram: {error}")
    if source in ("all", "discord") and cfg.get("discord"):
        seen = load_seen()
        try:
            rows.extend(discord_new(cfg, seen))
        except Exception as error:
            problems.append(f"discord: {error}")
        save_seen(seen)
    if not cfg:
        problems.append("no messengers configured")
    rows.sort(key=lambda r: r["when"])
    return rows, problems


def read(query, source="all", limit=PER_CHAT):
    cfg = load_config()
    rows, problems = [], []
    if source in ("all", "telegram") and cfg.get("telegram"):
        try:
            rows.extend(asyncio.run(_telegram_read(cfg, query, limit)))
        except Exception as error:
            problems.append(f"telegram: {error}")
    if not rows and source in ("all", "discord") and cfg.get("discord"):
        try:
            rows.extend(discord_read(cfg, query, limit))
        except Exception as error:
            problems.append(f"discord: {error}")
    return rows, problems


def summarize(rows, problems=(), per_chat=2):
    """One spoken paragraph: who wrote, where, what, newest chats first."""
    if not rows and not problems:
        return "nothing new"
    by_chat = {}
    for r in rows:
        by_chat.setdefault((r["source"], r["chat"]), []).append(r)
    parts = []
    for (source, chat), items in sorted(by_chat.items(), key=lambda kv: -max(i["when"] for i in kv[1])):
        items = sorted(items, key=lambda i: i["when"])
        shown = items[-per_chat:]
        quoted = "; ".join(f"{i['from']}: {i['text'][:120]}" for i in shown)
        more = len(items) - len(shown)
        tail = f" and {more} more" if more > 0 else ""
        parts.append(f"{source} {chat}, {len(items)} new{tail}: {quoted}")
    if problems:
        parts.append("could not read " + "; ".join(problems))
    return ". ".join(parts)


def main(argv):
    cfg = load_config()
    if argv[:1] == ["login"]:
        from . import messenger_login

        return messenger_login.main(argv[1:])
    if argv[:1] == ["check"]:
        rows, problems = check(argv[1] if len(argv) > 1 else "all")
        print(summarize(rows, problems, per_chat=PER_CHAT))
        return 0
    if argv[:1] == ["read"] and len(argv) > 1:
        rows, problems = read(" ".join(argv[1:]))
        print(summarize(rows, problems, per_chat=PER_CHAT))
        return 0
    print("usage: python -m voice.messages login [telegram|discord] | check [telegram|discord] | read <chat>")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
