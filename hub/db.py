import json
import os
import sqlite3
import threading
import time

_lock = threading.Lock()
_conn = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    source TEXT,
    role TEXT,
    text TEXT,
    meta TEXT
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    text TEXT,
    tags TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT,
    embedding TEXT,
    threshold REAL
);
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT,
    created REAL
);
CREATE TABLE IF NOT EXISTS ws_chats (
    chat_id TEXT PRIMARY KEY,
    workspace_id TEXT,
    title TEXT,
    state TEXT,
    last_touched REAL,
    created REAL
);
CREATE TABLE IF NOT EXISTS ws_todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT,
    text TEXT,
    state TEXT,
    created REAL
);
CREATE TABLE IF NOT EXISTS t3_watches (
    thread_id TEXT PRIMARY KEY,
    env TEXT,
    title TEXT,
    conv TEXT,
    state TEXT,
    created REAL,
    checked REAL
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    token_hash TEXT UNIQUE,
    created REAL,
    last_seen REAL,
    revoked REAL,
    parent INTEGER
);
CREATE TABLE IF NOT EXISTS usage_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    service TEXT,
    model TEXT,
    unit TEXT,
    units REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost REAL,
    origin TEXT,
    ok INTEGER
);
CREATE INDEX IF NOT EXISTS usage_calls_ts ON usage_calls (ts);
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT,
    descriptor TEXT,
    created REAL,
    last_active REAL,
    state TEXT DEFAULT 'open',
    merged_into TEXT,
    named INTEGER DEFAULT 0,
    described_upto INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT,
    kind TEXT,
    kind_source TEXT,
    machine TEXT,
    state TEXT,
    step TEXT,
    started_at REAL,
    finished_at REAL,
    seen_at REAL,
    updated_at REAL,
    links TEXT,
    result TEXT,
    source TEXT
);
CREATE INDEX IF NOT EXISTS tasks_updated ON tasks (updated_at);
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    conv TEXT,
    state TEXT,
    created REAL,
    updated REAL,
    data TEXT
);
CREATE INDEX IF NOT EXISTS questions_conv ON questions (conv, state);
CREATE TABLE IF NOT EXISTS pair_codes (
    code_hash TEXT PRIMARY KEY,
    created REAL,
    expires REAL,
    used REAL,
    device_id INTEGER,
    minted_by INTEGER
);
"""


_path = None
MAIN_SOURCES = ("telegram", "fleet", "phone")


def _open(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def init(db_path):
    global _conn, _path
    with _lock:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        _conn = _open(db_path)
        _path = db_path


def path():
    return _path


def snapshot_to(target):
    """A consistent copy of the whole database at target, which must not exist."""
    reader = sqlite3.connect(_path, timeout=15)
    try:
        reader.execute("VACUUM INTO ?", (target,))
    finally:
        reader.close()


def swap_in(snapshot):
    """Replaces the live database with the file at snapshot and reopens it."""
    global _conn
    with _lock:
        _conn.close()
        for suffix in ("-wal", "-shm"):
            try:
                os.unlink(_path + suffix)
            except FileNotFoundError:
                pass
        os.replace(snapshot, _path)
        _conn = _open(_path)


def max_ids():
    with _lock:
        message = _conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
        seq = _conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'messages'").fetchone()
        fact = _conn.execute("SELECT COALESCE(MAX(id), 0) FROM facts").fetchone()[0]
    return max(message, seq[0] if seq else 0), fact


def start_messages_at(first_id):
    """The next message gets first_id, as long as no stored message is higher."""
    with _lock:
        _conn.execute("DELETE FROM sqlite_sequence WHERE name = 'messages'")
        _conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('messages', ?)", (first_id - 1,))
        _conn.commit()


def count_since(first_message, first_fact):
    with _lock:
        messages = _conn.execute("SELECT COUNT(*) FROM messages WHERE id >= ?", (first_message,)).fetchone()[0]
        facts = _conn.execute("SELECT COUNT(*) FROM facts WHERE id >= ?", (first_fact,)).fetchone()[0]
    return messages, facts


def written_since(first_message, first_fact):
    with _lock:
        messages = _conn.execute(
            "SELECT id, ts, source, role, text, meta FROM messages WHERE id >= ? ORDER BY id",
            (first_message,),
        ).fetchall()
        facts = _conn.execute(
            "SELECT id, ts, text, tags FROM facts WHERE id >= ? ORDER BY id", (first_fact,)
        ).fetchall()
    messages = [dict(row) for row in messages]
    for message in messages:
        message["meta"] = json.loads(message["meta"]) if message["meta"] else None
    facts = [dict(row) for row in facts]
    for fact in facts:
        fact["tags"] = json.loads(fact["tags"]) if fact["tags"] else []
    return messages, facts


def _drop_unanswered(client_id, section, before):
    rows = _conn.execute(
        "SELECT id, role FROM messages WHERE id < ? AND json_extract(meta, '$.client_id') = ?"
        " AND json_extract(meta, '$.section') IS ?",
        (before, client_id, section),
    ).fetchall()
    if any(row["role"] == "assistant" for row in rows):
        return
    for row in rows:
        _conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))


def replay(messages, facts):
    """Stores rows another host wrote, keeping their ids and times. Rows already here are
    skipped, so a replay can be repeated. Returns the stored messages."""
    stored = []
    with _lock:
        for m in messages:
            meta = json.dumps(m["meta"]) if m.get("meta") is not None else None
            row = _conn.execute("SELECT role, text FROM messages WHERE id = ?", (m["id"],)).fetchone()
            if row is not None and (row["role"], row["text"]) == (m["role"], m["text"]):
                continue
            if row is not None and _conn.execute(
                "SELECT 1 FROM messages WHERE ts = ? AND role = ? AND text = ?", (m["ts"], m["role"], m["text"])
            ).fetchone():
                continue
            client_id = (m.get("meta") or {}).get("client_id")
            if m["role"] == "user" and client_id is not None:
                _drop_unanswered(str(client_id), (m.get("meta") or {}).get("section"), m["id"])
            cur = _conn.execute(
                "INSERT INTO messages (id, ts, source, role, text, meta) VALUES (?, ?, ?, ?, ?, ?)",
                (None if row is not None else m["id"], m["ts"], m["source"], m["role"], m["text"], meta),
            )
            stored.append({**m, "id": cur.lastrowid})
        for f in facts:
            tags = json.dumps(f.get("tags") or [])
            if _conn.execute("SELECT 1 FROM facts WHERE ts = ? AND text = ?", (f["ts"], f["text"])).fetchone():
                continue
            _conn.execute("INSERT INTO facts (ts, text, tags) VALUES (?, ?, ?)", (f["ts"], f["text"], tags))
        _conn.commit()
    for m in stored:
        for listener in list(listeners):
            try:
                listener(m["id"], m["source"], m.get("meta"))
            except Exception:
                pass
    return stored


listeners = []


def add_message(source, role, text, meta):
    with _lock:
        cur = _conn.execute(
            "INSERT INTO messages (ts, source, role, text, meta) VALUES (?, ?, ?, ?, ?)",
            (time.time(), source, role, text, json.dumps(meta) if meta is not None else None),
        )
        _conn.commit()
        message_id = cur.lastrowid
    for listener in list(listeners):
        try:
            listener(message_id, source, meta)
        except Exception:
            pass
    return message_id


def delete_message(message_id):
    with _lock:
        _conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        _conn.commit()


def supersede(message_ids, by):
    """Marks messages as replaced by message `by`: an edit withdrew them, or an answer again replaced a reply."""
    if not message_ids:
        return
    with _lock:
        _conn.executemany(
            "UPDATE messages SET meta = json_set(COALESCE(meta, '{}'), '$.superseded_by', ?) WHERE id = ?",
            [(by, message_id) for message_id in message_ids],
        )
        _conn.commit()


def by_client_id(client_id, section):
    """The stored user message and reply for a phone message id, or None for each."""
    with _lock:
        rows = _conn.execute(
            "SELECT id, ts, source, role, text, meta FROM messages"
            " WHERE json_extract(meta, '$.client_id') = ? AND json_extract(meta, '$.section') = ?"
            " ORDER BY id ASC",
            (str(client_id), section),
        ).fetchall()
    found = {"user": None, "assistant": None}
    for row in rows:
        message = dict(row)
        message["meta"] = json.loads(message["meta"]) if message["meta"] else None
        if found.get(message["role"], 1) is None:
            found[message["role"]] = message
    return found["user"], found["assistant"]


def get_messages(limit, source=None):
    with _lock:
        rows = _conn.execute(
            "SELECT id, ts, source, role, text, meta FROM messages"
            " WHERE (? IS NULL OR source = ?) ORDER BY id DESC LIMIT ?",
            (source, source, limit),
        ).fetchall()
    messages = [dict(row) for row in reversed(rows)]
    for message in messages:
        message["meta"] = json.loads(message["meta"]) if message["meta"] else None
    return messages


def add_fact(text, tags):
    with _lock:
        cur = _conn.execute(
            "INSERT INTO facts (ts, text, tags) VALUES (?, ?, ?)",
            (time.time(), text, json.dumps(tags)),
        )
        _conn.commit()
        return cur.lastrowid


def get_facts(q=None, limit=20):
    with _lock:
        if q:
            pattern = f"%{q}%"
            rows = _conn.execute(
                "SELECT id, ts, text, tags FROM facts"
                " WHERE text LIKE ? OR tags LIKE ? ORDER BY id DESC LIMIT ?",
                (pattern, pattern, limit),
            ).fetchall()
        else:
            rows = _conn.execute(
                "SELECT id, ts, text, tags FROM facts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    facts = [dict(row) for row in rows]
    for fact in facts:
        fact["tags"] = json.loads(fact["tags"]) if fact["tags"] else []
    return facts


def get_facts_tagged(tag, limit=8):
    with _lock:
        rows = _conn.execute(
            "SELECT id, ts, text, tags FROM facts WHERE EXISTS"
            " (SELECT 1 FROM json_each(facts.tags) WHERE json_each.value = ?)"
            " ORDER BY id DESC LIMIT ?",
            (tag, limit),
        ).fetchall()
    facts = [dict(row) for row in rows]
    for fact in facts:
        fact["tags"] = json.loads(fact["tags"]) if fact["tags"] else []
    return facts


def section_messages(section, after=0, limit=100, main_sources=(), before=0):
    marks = ",".join("?" for _ in main_sources)
    main = (
        f" OR (json_extract(meta, '$.section') IS NULL AND source IN ({marks}))"
        if section == "diablo" and main_sources else ""
    )
    with _lock:
        rows = _conn.execute(
            "SELECT id, ts, source, role, text, meta FROM messages"
            f" WHERE id > ? AND (? <= 0 OR id < ?) AND (json_extract(meta, '$.section') = ?{main})"
            f" ORDER BY id {'DESC' if after <= 0 else 'ASC'} LIMIT ?",
            (after, before, before, section, *(main_sources if main else ()), limit),
        ).fetchall()
    messages = [dict(row) for row in rows]
    if after <= 0:
        messages.reverse()
    for message in messages:
        message["meta"] = json.loads(message["meta"]) if message["meta"] else None
    return messages


def get_setting(key):
    with _lock:
        row = _conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def get_all_settings():
    with _lock:
        rows = _conn.execute("SELECT key, value FROM settings").fetchall()
    return [dict(row) for row in rows]


def set_setting(key, value):
    with _lock:
        _conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        _conn.commit()


def set_profile(name, embedding_json, threshold):
    with _lock:
        _conn.execute(
            "INSERT INTO profile (id, name, embedding, threshold) VALUES (1, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
            " embedding = excluded.embedding, threshold = excluded.threshold",
            (name, embedding_json, threshold),
        )
        _conn.commit()


def get_profile():
    with _lock:
        row = _conn.execute(
            "SELECT name, embedding, threshold FROM profile WHERE id = 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "name": row["name"],
        "embedding": json.loads(row["embedding"]) if row["embedding"] else [],
        "threshold": row["threshold"],
    }


def create_workspace(ws_id, name):
    with _lock:
        _conn.execute(
            "INSERT INTO workspaces (id, name, created) VALUES (?, ?, ?)"
            " ON CONFLICT(id) DO NOTHING",
            (ws_id, name, time.time()),
        )
        _conn.commit()


def upsert_chat(chat_id, workspace_id, title, state="idle"):
    now = time.time()
    with _lock:
        _conn.execute(
            "INSERT INTO ws_chats (chat_id, workspace_id, title, state, last_touched, created)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET"
            " workspace_id = excluded.workspace_id,"
            " state = excluded.state, last_touched = excluded.last_touched",
            (chat_id, workspace_id, title, state, now, now),
        )
        _conn.commit()


def set_chat_state(chat_id, state):
    with _lock:
        _conn.execute(
            "UPDATE ws_chats SET state = ?, last_touched = ? WHERE chat_id = ?",
            (state, time.time(), chat_id),
        )
        _conn.commit()


def list_workspaces(include_settled=True):
    with _lock:
        ws_rows = _conn.execute(
            "SELECT id, name, created FROM workspaces ORDER BY created"
        ).fetchall()
        chat_rows = _conn.execute(
            "SELECT chat_id, workspace_id, title, state, last_touched FROM ws_chats"
            " ORDER BY last_touched"
        ).fetchall()
    chats_by_ws = {}
    for row in chat_rows:
        chats_by_ws.setdefault(row["workspace_id"], []).append(
            {
                "chat_id": row["chat_id"],
                "title": row["title"],
                "state": row["state"],
                "last_touched": row["last_touched"],
            }
        )
    return [
        {"id": w["id"], "name": w["name"], "created": w["created"], "chats": chats_by_ws.get(w["id"], [])}
        for w in ws_rows
    ]


def set_chat_settled(chat_id, settled):
    with _lock:
        row = _conn.execute("SELECT state FROM ws_chats WHERE chat_id = ?", (chat_id,)).fetchone()
        if row is None:
            return False
        new_state = "settled" if settled else "idle"
        _conn.execute(
            "UPDATE ws_chats SET state = ? WHERE chat_id = ?",
            (new_state, chat_id),
        )
        _conn.commit()
        return True


def chat_messages(chat_id, limit=100):
    with _lock:
        rows = _conn.execute(
            "SELECT id, ts, source, role, text, meta FROM messages"
            " WHERE json_extract(meta, '$.chat') = ? ORDER BY id ASC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    messages = [dict(row) for row in rows]
    for message in messages:
        message["meta"] = json.loads(message["meta"]) if message["meta"] else None
    return messages


def add_todo(ws_id, text):
    with _lock:
        cur = _conn.execute(
            "INSERT INTO ws_todos (workspace_id, text, state, created) VALUES (?, ?, 'open', ?)",
            (ws_id, text, time.time()),
        )
        _conn.commit()
        return cur.lastrowid


def get_todos(ws_id):
    with _lock:
        rows = _conn.execute(
            "SELECT id, text, state, created FROM ws_todos"
            " WHERE workspace_id = ? ORDER BY state DESC, id ASC",
            (ws_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def set_todo(todo_id, state):
    with _lock:
        _conn.execute("UPDATE ws_todos SET state = ? WHERE id = ?", (state, todo_id))
        _conn.commit()


def remove_todo(todo_id):
    with _lock:
        _conn.execute("DELETE FROM ws_todos WHERE id = ?", (todo_id,))
        _conn.commit()


def add_watch(thread_id, env, title, conv):
    with _lock:
        _conn.execute(
            "INSERT INTO t3_watches (thread_id, env, title, conv, state, created, checked)"
            " VALUES (?, ?, ?, ?, 'open', ?, 0)"
            " ON CONFLICT(thread_id) DO UPDATE SET env = excluded.env, title = excluded.title,"
            " conv = excluded.conv, state = 'open', created = excluded.created",
            (thread_id, env, title, conv, time.time()),
        )
        _conn.commit()


def open_watches():
    with _lock:
        rows = _conn.execute(
            "SELECT thread_id, env, title, conv, state, created, checked FROM t3_watches"
            " WHERE state IN ('open', 'asking') ORDER BY created ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def recent_watches(since):
    with _lock:
        rows = _conn.execute(
            "SELECT thread_id, env, title, conv, state, created, checked FROM t3_watches"
            " WHERE created >= ? OR state IN ('open', 'asking') ORDER BY created DESC",
            (since,),
        ).fetchall()
    return [dict(row) for row in rows]


def set_watch(thread_id, state):
    with _lock:
        _conn.execute(
            "UPDATE t3_watches SET state = ?, checked = ? WHERE thread_id = ?",
            (state, time.time(), thread_id),
        )
        _conn.commit()


TASK_COLS = ("id", "title", "kind", "kind_source", "machine", "state", "step", "started_at", "finished_at",
             "seen_at", "updated_at", "links", "result", "source")


def _task_row(row):
    task = dict(row)
    try:
        task["links"] = json.loads(task["links"]) if task["links"] else {}
    except ValueError:
        task["links"] = {}
    return task


def get_task(task_id):
    with _lock:
        row = _conn.execute(f"SELECT {', '.join(TASK_COLS)} FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _task_row(row) if row else None


def put_task(task):
    row = dict(task)
    row["links"] = json.dumps(row.get("links") or {}, sort_keys=True)
    values = [row.get(col) for col in TASK_COLS]
    with _lock:
        _conn.execute(
            f"INSERT INTO tasks ({', '.join(TASK_COLS)}) VALUES ({', '.join('?' for _ in TASK_COLS)})"
            f" ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in TASK_COLS[1:])}",
            values,
        )
        _conn.commit()


def list_tasks(states=None, limit=200):
    query = f"SELECT {', '.join(TASK_COLS)} FROM tasks"
    args = []
    if states:
        query += f" WHERE state IN ({','.join('?' for _ in states)})"
        args.extend(states)
    query += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = _conn.execute(query, args).fetchall()
    return [_task_row(row) for row in rows]


def prune_tasks(before):
    with _lock:
        _conn.execute("DELETE FROM tasks WHERE state IN ('done', 'failed') AND COALESCE(finished_at, updated_at) < ?", (before,))
        _conn.commit()


def add_pair_code(code_hash, expires, minted_by=None):
    with _lock:
        _conn.execute("DELETE FROM pair_codes WHERE expires < ?", (time.time() - 86400,))
        _conn.execute(
            "INSERT INTO pair_codes (code_hash, created, expires, minted_by) VALUES (?, ?, ?, ?)",
            (code_hash, time.time(), expires, minted_by),
        )
        _conn.commit()


def pair_code(code_hash):
    with _lock:
        row = _conn.execute(
            "SELECT p.code_hash, p.expires, p.used, p.device_id, d.name AS device_name FROM pair_codes p"
            " LEFT JOIN devices d ON d.id = p.device_id WHERE p.code_hash = ?",
            (code_hash,),
        ).fetchone()
    return dict(row) if row else None


def redeem_pair_code(code_hash, name, token_hash):
    """Marks a live code used and creates its device in one step; None when the code is not live."""
    now = time.time()
    with _lock:
        cur = _conn.execute(
            "UPDATE pair_codes SET used = ? WHERE code_hash = ? AND used IS NULL AND expires > ?",
            (now, code_hash, now),
        )
        if cur.rowcount != 1:
            _conn.rollback()
            return None
        minted_by = _conn.execute("SELECT minted_by FROM pair_codes WHERE code_hash = ?", (code_hash,)).fetchone()[0]
        device = _conn.execute(
            "INSERT INTO devices (name, token_hash, created, last_seen, parent) VALUES (?, ?, ?, ?, ?)",
            (name, token_hash, now, now, minted_by),
        ).lastrowid
        _conn.execute("UPDATE pair_codes SET device_id = ? WHERE code_hash = ?", (device, code_hash))
        _conn.commit()
    return device


def create_device(name, token_hash):
    """Creates a device directly, for a caller the hub already trusts by other means."""
    now = time.time()
    with _lock:
        device = _conn.execute(
            "INSERT INTO devices (name, token_hash, created, last_seen) VALUES (?, ?, ?, ?)",
            (name, token_hash, now, now),
        ).lastrowid
        _conn.commit()
    return device


def burn_pair_codes():
    with _lock:
        _conn.execute("UPDATE pair_codes SET expires = ? WHERE used IS NULL AND expires > ?", (time.time(), time.time()))
        _conn.commit()


def device_by_token(token_hash):
    with _lock:
        row = _conn.execute(
            "SELECT id, name, created, last_seen FROM devices WHERE token_hash = ? AND revoked IS NULL",
            (token_hash,),
        ).fetchone()
    return dict(row) if row else None


def touch_device(device_id):
    with _lock:
        _conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (time.time(), device_id))
        _conn.commit()


def list_devices():
    with _lock:
        rows = _conn.execute(
            "SELECT id, name, created, last_seen FROM devices WHERE revoked IS NULL ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def revoke_device(device_id):
    """Revokes a device and every device paired with a code it made; returns the revoked ids."""
    with _lock:
        rows = _conn.execute(
            "WITH RECURSIVE tree(id) AS (SELECT id FROM devices WHERE id = ? AND revoked IS NULL"
            " UNION SELECT d.id FROM devices d JOIN tree t ON d.parent = t.id WHERE d.revoked IS NULL)"
            " SELECT id FROM tree",
            (device_id,),
        ).fetchall()
        ids = [row[0] for row in rows]
        _conn.executemany("UPDATE devices SET revoked = ? WHERE id = ?", [(time.time(), i) for i in ids])
        _conn.commit()
    return ids


def add_usage(ts, service, model, unit, units, tokens_in, tokens_out, cost, origin, ok):
    with _lock:
        _conn.execute(
            "INSERT INTO usage_calls (ts, service, model, unit, units, tokens_in, tokens_out, cost, origin, ok)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, service, model, unit, units, tokens_in, tokens_out, cost, origin, 1 if ok else 0),
        )
        _conn.commit()


def usage_totals(since, until=None):
    query = (
        "SELECT service, COUNT(*) AS calls, SUM(units) AS units, SUM(tokens_in) AS tokens_in,"
        " SUM(tokens_out) AS tokens_out, SUM(cost) AS cost, COUNT(cost) AS priced, SUM(1 - ok) AS failed"
        " FROM usage_calls WHERE ts >= ?"
    )
    args = [since]
    if until is not None:
        query += " AND ts < ?"
        args.append(until)
    with _lock:
        rows = _conn.execute(query + " GROUP BY service", args).fetchall()
    return {row["service"]: dict(row) for row in rows}


def usage_days(since):
    with _lock:
        rows = _conn.execute(
            "SELECT service, date(ts, 'unixepoch', 'localtime') AS day, COUNT(*) AS calls, SUM(units) AS units,"
            " SUM(tokens_in) + SUM(tokens_out) AS tokens, SUM(cost) AS cost"
            " FROM usage_calls WHERE ts >= ? GROUP BY service, day ORDER BY day",
            (since,),
        ).fetchall()
    return [dict(row) for row in rows]


def usage_models(since):
    with _lock:
        rows = _conn.execute(
            "SELECT service, model, COUNT(*) AS calls, SUM(units) AS units, SUM(tokens_in) + SUM(tokens_out) AS tokens,"
            " SUM(cost) AS cost FROM usage_calls WHERE ts >= ? GROUP BY service, model ORDER BY calls DESC",
            (since,),
        ).fetchall()
    return [dict(row) for row in rows]


def prune_usage(before):
    with _lock:
        _conn.execute("DELETE FROM usage_calls WHERE ts < ?", (before,))
        _conn.commit()
THREAD_COLS = "id, title, descriptor, created, last_active, state, merged_into, named, described_upto"
MAIN_MESSAGES = "(json_extract(meta, '$.section') = 'diablo' OR (json_extract(meta, '$.section') IS NULL AND source IN ({marks})))"
IN_THREAD = "EXISTS (SELECT 1 FROM json_each(messages.meta, '$.threads') WHERE json_each.value = ?)"


def _message_rows(rows):
    messages = [dict(row) for row in rows]
    for message in messages:
        message["meta"] = json.loads(message["meta"]) if message["meta"] else None
    return messages


def create_thread(thread_id, title, descriptor="", now=None, named=False):
    now = time.time() if now is None else now
    with _lock:
        _conn.execute(
            "INSERT INTO threads (id, title, descriptor, created, last_active, state, named)"
            " VALUES (?, ?, ?, ?, ?, 'open', ?) ON CONFLICT(id) DO NOTHING",
            (thread_id, title, descriptor, now, now, 1 if named else 0),
        )
        _conn.commit()


def get_thread(thread_id):
    with _lock:
        row = _conn.execute(f"SELECT {THREAD_COLS} FROM threads WHERE id = ?", (thread_id,)).fetchone()
    return dict(row) if row else None


def update_thread(thread_id, **fields):
    allowed = {"title", "descriptor", "last_active", "state", "merged_into", "named", "described_upto"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    with _lock:
        cur = _conn.execute(
            f"UPDATE threads SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
            (*fields.values(), thread_id),
        )
        _conn.commit()
    return cur.rowcount == 1


def list_threads(states=("open",), limit=200):
    marks = ",".join("?" for _ in states)
    with _lock:
        rows = _conn.execute(
            f"SELECT {THREAD_COLS} FROM threads WHERE state IN ({marks}) ORDER BY last_active DESC LIMIT ?",
            (*states, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def thread_counts():
    with _lock:
        rows = _conn.execute(
            "SELECT json_each.value AS thread, COUNT(*) AS n, MAX(messages.id) AS last FROM messages, json_each(messages.meta, '$.threads')"
            " WHERE json_valid(messages.meta) GROUP BY json_each.value"
        ).fetchall()
    return {row["thread"]: {"count": row["n"], "last": row["last"]} for row in rows}


def get_message(message_id):
    with _lock:
        row = _conn.execute("SELECT id, ts, source, role, text, meta FROM messages WHERE id = ?", (message_id,)).fetchone()
    return _message_rows([row])[0] if row else None


def set_message_threads(changes):
    """changes: {message_id: [thread ids]}. Writes meta.threads; an empty list removes the key."""
    with _lock:
        for message_id, ids in changes.items():
            if ids:
                _conn.execute(
                    "UPDATE messages SET meta = json_set(COALESCE(meta, '{}'), '$.threads', json(?)) WHERE id = ?",
                    (json.dumps(list(ids)), message_id),
                )
            else:
                _conn.execute(
                    "UPDATE messages SET meta = json_remove(meta, '$.threads') WHERE id = ? AND meta IS NOT NULL",
                    (message_id,),
                )
        _conn.commit()


def thread_messages(thread_id, before=0, limit=40):
    """One page of a thread, oldest first: the newest `limit` messages below `before` (0 means the newest)."""
    with _lock:
        rows = _conn.execute(
            "SELECT id, ts, source, role, text, meta FROM messages"
            f" WHERE json_valid(meta) AND {IN_THREAD} AND (? <= 0 OR id < ?) ORDER BY id DESC LIMIT ?",
            (thread_id, before, before, limit),
        ).fetchall()
    return list(reversed(_message_rows(rows)))


_folding = None


def _fold(text):
    return (text or "").casefold()


def find_messages(words, limit=200, main_sources=(), thread=None):
    """Main conversation messages holding every word, any case, newest first."""
    words = [w.casefold() for w in words if w.strip()]
    if not words:
        return []
    marks = ",".join("?" for _ in main_sources) or "''"
    where = MAIN_MESSAGES.format(marks=marks) + "".join(" AND instr(hub_fold(text), ?) > 0" for _ in words)
    args = [*main_sources, *words]
    if thread:
        where += f" AND json_valid(meta) AND {IN_THREAD}"
        args.append(thread)
    global _folding
    with _lock:
        if _folding is not _conn:
            _conn.create_function("hub_fold", 1, _fold, deterministic=True)
            _folding = _conn
        rows = _conn.execute(
            f"SELECT id, ts, source, role, text, meta FROM messages WHERE {where} ORDER BY id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
    return _message_rows(rows)


def main_messages(after=0, limit=40, main_sources=(), untagged=False, before=0):
    marks = ",".join("?" for _ in main_sources) or "''"
    where = MAIN_MESSAGES.format(marks=marks)
    if untagged:
        where += " AND (meta IS NULL OR json_extract(meta, '$.threads') IS NULL)"
    order = "ASC" if after > 0 or untagged else "DESC"
    with _lock:
        rows = _conn.execute(
            "SELECT id, ts, source, role, text, meta FROM messages"
            f" WHERE {where} AND id > ? AND (? <= 0 OR id < ?) ORDER BY id {order} LIMIT ?",
            (*main_sources, after, before, before, limit),
        ).fetchall()
    messages = _message_rows(rows)
    if order == "DESC":
        messages.reverse()
    return messages


def messages_between(low, high):
    with _lock:
        rows = _conn.execute(
            "SELECT id, ts, source, role, text, meta FROM messages WHERE id >= ? AND id <= ? ORDER BY id", (low, high)
        ).fetchall()
    return _message_rows(rows)


def untagged_count(after, main_sources=()):
    marks = ",".join("?" for _ in main_sources) or "''"
    with _lock:
        return _conn.execute(
            f"SELECT COUNT(*) FROM messages WHERE {MAIN_MESSAGES.format(marks=marks)} AND id > ?"
            " AND (meta IS NULL OR json_extract(meta, '$.threads') IS NULL)",
            (*main_sources, after),
        ).fetchone()[0]
