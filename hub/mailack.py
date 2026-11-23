"""Mail Marcus already heard about: the store that keeps Diablo from naming the same mail twice.

One sqlite file beside mail.db, shared by the hub and the diablo MCP server and kept across restarts. A mail
counts as known by its Message-ID and by a fingerprint of its sender and subject, so a service sending the
same notice again is known too. A mail gets in when Diablo names it in a turn, when a reminder ping went out
for it, or when Marcus says he has seen it (the mail_acknowledge tool). Nothing ever takes it out again.
"""

import os
import re
import sqlite3
import threading
import time
from pathlib import Path

STATE = Path.home() / ".local" / "state" / "diablo"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS acks (
    key TEXT PRIMARY KEY,
    how TEXT NOT NULL,
    at REAL NOT NULL,
    mail_id INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

PREFIX_RE = re.compile(r"^\s*((re|fwd?|sv|vs|aw|wg)\s*(\[\d+\])?\s*:\s*)+", re.I)
STOP = {"the", "and", "for", "from", "about", "mail", "mails", "email", "emails", "message", "messages", "that",
        "this", "those", "these", "his", "her", "its", "with", "seen", "saw", "read", "already", "one", "ones",
        "all", "have", "has", "was", "were", "you", "your", "dealt", "done", "know", "new"}

_lock = threading.Lock()
_conn = None


def db_path():
    chosen = os.environ.get("DIABLO_MAIL_ACKS")
    if chosen:
        return Path(chosen)
    mail_db = os.environ.get("DIABLO_MAIL_DB")
    return Path(mail_db).with_name("mail-acks.db") if mail_db else STATE / "mail-acks.db"


def conn():
    global _conn
    if _conn is None:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA_SQL)
    return _conn


def reset_for_tests():
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None


def message_key(message_id):
    value = re.sub(r"\s+", "", str(message_id or "")).strip("<>").lower()
    return f"id:{value}" if value else ""


def fingerprint(sender, subject):
    """The sender and the subject without Re: or Fwd:, case, punctuation or spacing, so a resent notice matches."""
    words = " ".join(re.findall(r"[^\W_]+", PREFIX_RE.sub("", str(subject or "")).lower()))
    who = str(sender or "").strip().lower()
    return f"fp:{who}|{words}" if who and re.search(r"[^\W\d_]", words) else ""


def keys(row):
    return [key for key in (message_key(row.get("message_id")), fingerprint(row.get("sender"), row.get("subject")))
            if key]


def known(row):
    found = keys(row)
    if not found:
        return False
    with _lock:
        marks = ",".join("?" * len(found))
        return conn().execute(f"SELECT 1 FROM acks WHERE key IN ({marks}) LIMIT 1", found).fetchone() is not None


def fresh(rows):
    return [row for row in rows if not known(row)]


def mark(rows, how, now=None):
    """Remember every row as known. how says why: mentioned, nudged, acknowledged or seeded."""
    now = now or time.time()
    with _lock:
        for row in rows:
            for key in keys(row):
                conn().execute("INSERT OR IGNORE INTO acks (key, how, at, mail_id) VALUES (?, ?, ?, ?)",
                               (key, how, now, row.get("mail_id") or row.get("id")))
        conn().commit()
    return len(rows)


def flag(name):
    with _lock:
        row = conn().execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
    return row[0] if row else ""


def set_flag(name, value):
    with _lock:
        conn().execute("INSERT OR REPLACE INTO meta (name, value) VALUES (?, ?)", (name, str(value)))
        conn().commit()


def words(text):
    return {word for word in re.findall(r"[^\W_]+", str(text or "").lower()) if len(word) > 2 and word not in STOP}


def matching(rows, description):
    """The rows a few words from Marcus point at: those sharing the most words with the description, at least half."""
    wanted = words(description)
    if not wanted:
        return []
    scored = []
    for row in rows:
        have = words(" ".join(str(row.get(field) or "") for field in ("sender", "sender_name", "subject", "summary")))
        scored.append((len(wanted & have), row))
    best = max((score for score, _ in scored), default=0)
    if best == 0 or best * 2 < len(wanted):
        return []
    return [row for score, row in scored if score == best]
