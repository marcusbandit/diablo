"""Sending through the hub's own Telegram bot.

The bot token and chat id are read from the hub's database at send time, the
same settings the hub's notify tool uses (telegram_bot_token, telegram_chat_id),
so a token change in the hub reaches the scheduler without a restart. The hub's
config.toml is the fallback for the token. Environment variables override both,
which is what the tests and a dry run use.
"""

import json
import os
import sqlite3
import time
import tomllib
import urllib.error
import urllib.request

HUB_DB = os.environ.get("DIABLO_HUB_DB", os.path.expanduser("~/diablo-hub/diablo.db"))
HUB_CONFIG = os.environ.get("DIABLO_HUB_CONFIG", os.path.expanduser("~/diablo-hub/config.toml"))
PLACEHOLDERS = {"", "CHANGE_ME"}


class SendError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _hub_settings(keys):
    try:
        conn = sqlite3.connect(f"file:{HUB_DB}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return {}
    try:
        marks = ",".join("?" for _ in keys)
        rows = conn.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", keys).fetchall()
        return {key: value for key, value in rows if value}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def credentials():
    found = _hub_settings(["telegram_bot_token", "telegram_chat_id", "telegram_owner"])
    token = os.environ.get("REMIND_TELEGRAM_TOKEN") or found.get("telegram_bot_token") or ""
    if token in PLACEHOLDERS:
        try:
            with open(HUB_CONFIG, "rb") as handle:
                token = str(tomllib.load(handle).get("telegram_bot_token") or "")
        except (OSError, ValueError):
            token = ""
    chat = os.environ.get("REMIND_TELEGRAM_CHAT") or found.get("telegram_chat_id") or found.get("telegram_owner") or ""
    if token in PLACEHOLDERS or not chat:
        raise SendError("telegram is not set up in the hub: no bot token or chat id")
    return token, chat


class TelegramSender:
    def __init__(self, timeout=20, record=True):
        self.timeout = timeout
        self.record = record

    def send(self, text, meta=None):
        """Send text to Marcus. Returns the Telegram message id or raises SendError."""
        token, chat = credentials()
        chat_id = int(chat) if str(chat).lstrip("-").isdigit() else str(chat)
        body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            detail = error.read()[:300].decode(errors="replace")
            retry_after = None
            try:
                retry_after = json.loads(detail).get("parameters", {}).get("retry_after")
            except ValueError:
                pass
            raise SendError(f"telegram refused with {error.code}: {detail}", retry_after) from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise SendError(f"telegram unreachable: {error}") from None
        if not reply.get("ok"):
            raise SendError(f"telegram said no: {str(reply)[:300]}")
        if self.record:
            record_in_hub(text, meta)
        return (reply.get("result") or {}).get("message_id")


class OutboxSender:
    """Appends each message to a file instead of Telegram. Set REMIND_OUTBOX to use it.

    For tests and for a dry run on the Pi; nothing reaches Marcus.
    """

    def __init__(self, path):
        self.path = path

    def send(self, text, meta=None):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"text": text, **(meta or {})}, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return None




class TaskSender:
    """Hands a scheduled task to the hub, which runs it as a brain turn and tells Marcus the result.

    The hub answers at once and works in the background, so a long task never holds up a reminder.
    The delivery id is the client id: a retry after a lost answer never runs the task twice.
    """

    def __init__(self, timeout=30):
        self.timeout = timeout

    def token(self):
        token = os.environ.get("REMIND_HUB_TOKEN") or ""
        if not token:
            try:
                with open(HUB_CONFIG, "rb") as handle:
                    token = str(tomllib.load(handle).get("hub_token") or "")
            except (OSError, ValueError):
                token = ""
        if not token:
            raise SendError(f"no hub_token in {HUB_CONFIG}")
        return token

    def send(self, text, meta=None):
        hub_url = os.environ.get("REMIND_HUB_URL", "").rstrip("/")
        if not hub_url:
            raise SendError("REMIND_HUB_URL is not set, so tasks cannot reach the hub")
        meta = meta or {}
        client_id = meta.get("client_id") or f"task-{meta.get('reminder')}-{meta.get('delivery')}"
        body = json.dumps({"text": text, "client_id": client_id}).encode()
        request = urllib.request.Request(
            f"{hub_url}/v1/scheduled", data=body, method="POST",
            headers={"Content-Type": "application/json", "X-Diablo-Token": self.token()},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            raise SendError(f"the hub refused the task with {error.code}: {error.read()[:200].decode(errors='replace')}") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise SendError(f"the hub is unreachable: {error}") from None
        if not reply.get("ok"):
            raise SendError(f"the hub said no: {str(reply)[:200]}")
        return None


class Router:
    """Reminders go to Telegram, tasks go to the hub."""

    def __init__(self, remind, task):
        self.senders = {"remind": remind, "task": task}

    def send(self, text, meta=None):
        return self.senders[(meta or {}).get("kind") or "remind"].send(text, meta)


def make_sender():
    outbox = os.environ.get("REMIND_OUTBOX")
    if outbox:
        return OutboxSender(outbox)
    return Router(TelegramSender(), TaskSender())


def record_in_hub(text, meta=None):
    """Add the pushed message to the hub's history, as the hub's notify tool does.

    Diablo then sees what it said when Marcus answers the reminder on Telegram.
    Best effort: the message already went out, so a failure here is only logged.
    """
    try:
        conn = sqlite3.connect(f"file:{HUB_DB}?mode=rw", uri=True, timeout=10)
        try:
            conn.execute(
                "INSERT INTO messages (ts, source, role, text, meta) VALUES (?, 'telegram', 'assistant', ?, ?)",
                (time.time(), text, json.dumps({"pushed": True, **(meta or {})})),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False
