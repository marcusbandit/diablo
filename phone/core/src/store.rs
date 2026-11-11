use crate::hosts::{choose_order, Host};
use rusqlite::{params, Connection, OptionalExtension, Row};
use std::collections::HashSet;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{SystemTime, UNIX_EPOCH};

/// The hubs a fresh install starts with. Baked in at build time from DIABLO_HUB_URL and
/// DIABLO_STANDBY_URL (build-android.sh reads them from ~/.config/diablo/local.env), else the
/// boxes' MagicDNS names, so no address of a real tailnet is ever written in the repo.
pub const PRIMARY_HUB: &str = match option_env!("DIABLO_HUB_URL") {
    Some(url) => url,
    None => "http://diablo-pi:8755",
};
pub const STANDBY_HUB: &str = match option_env!("DIABLO_STANDBY_URL") {
    Some(url) => url,
    None => "http://nemurenai:8755",
};

#[derive(uniffi::Record, Clone, Debug, PartialEq)]
pub struct Section {
    pub id: String,
    pub name: String,
    pub icon: String,
    pub accent: String,
    pub brief: String,
    pub position: i32,
    pub hidden: bool,
    pub deleted: bool,
    pub updated: f64,
}

#[derive(uniffi::Enum, Clone, Copy, Debug, PartialEq, Eq)]
pub enum MessageState {
    Sent,
    Pending,
    Failed,
}

#[derive(uniffi::Record, Clone, Debug, PartialEq)]
pub struct Message {
    pub local_id: String,
    pub remote_id: Option<i64>,
    pub section: String,
    pub role: String,
    pub text: String,
    pub ts: f64,
    pub state: MessageState,
    pub meta: Option<String>,
}

#[derive(uniffi::Error, thiserror::Error, Debug)]
#[uniffi(flat_error)]
pub enum CoreError {
    #[error("database error: {message}")]
    Db { message: String },
    #[error("invalid input: {message}")]
    Invalid { message: String },
}

impl From<rusqlite::Error> for CoreError {
    fn from(e: rusqlite::Error) -> Self {
        CoreError::Db { message: e.to_string() }
    }
}

fn invalid(message: &str) -> CoreError {
    CoreError::Invalid { message: message.to_string() }
}

pub(crate) fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS sections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    icon TEXT NOT NULL,
    accent TEXT NOT NULL,
    brief TEXT NOT NULL,
    position INTEGER NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    updated REAL NOT NULL DEFAULT 0,
    synced_updated REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    local_id TEXT PRIMARY KEY,
    remote_id INTEGER,
    section TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL,
    state TEXT NOT NULL,
    meta TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS messages_remote ON messages(remote_id);
CREATE INDEX IF NOT EXISTS messages_section_ts ON messages(section, ts);
CREATE TABLE IF NOT EXISTS hosts (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    priority INTEGER NOT NULL,
    last_ok REAL,
    last_ms INTEGER,
    failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
";

const SECTION_COLS: &str = "id, name, icon, accent, brief, position, hidden, deleted, updated";
const MESSAGE_COLS: &str = "local_id, remote_id, section, role, text, ts, state, meta";

fn section_row(r: &Row) -> rusqlite::Result<Section> {
    Ok(Section {
        id: r.get(0)?,
        name: r.get(1)?,
        icon: r.get(2)?,
        accent: r.get(3)?,
        brief: r.get(4)?,
        position: r.get(5)?,
        hidden: r.get::<_, i64>(6)? != 0,
        deleted: r.get::<_, i64>(7)? != 0,
        updated: r.get(8)?,
    })
}

fn message_row(r: &Row) -> rusqlite::Result<Message> {
    let state: String = r.get(6)?;
    Ok(Message {
        local_id: r.get(0)?,
        remote_id: r.get(1)?,
        section: r.get(2)?,
        role: r.get(3)?,
        text: r.get(4)?,
        ts: r.get(5)?,
        state: match state.as_str() {
            "pending" => MessageState::Pending,
            "failed" => MessageState::Failed,
            _ => MessageState::Sent,
        },
        meta: r.get(7)?,
    })
}

fn host_row(r: &Row) -> rusqlite::Result<Host> {
    Ok(Host {
        name: r.get(0)?,
        url: r.get(1)?,
        priority: r.get(2)?,
        last_ok: r.get(3)?,
        last_ms: r.get(4)?,
        failures: r.get(5)?,
    })
}

fn valid_id(id: &str) -> bool {
    (1..=32).contains(&id.len())
        && id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

fn valid_accent(a: &str) -> bool {
    a.len() == 7 && a.starts_with('#') && a[1..].bytes().all(|b| b.is_ascii_hexdigit())
}

fn upsert_section(c: &Connection, s: &Section, synced: f64) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO sections (id, name, icon, accent, brief, position, hidden, deleted, updated, synced_updated)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
         ON CONFLICT(id) DO UPDATE SET name = ?2, icon = ?3, accent = ?4, brief = ?5, position = ?6,
            hidden = ?7, deleted = ?8, updated = ?9, synced_updated = ?10",
        params![
            s.id, s.name, s.icon, s.accent, s.brief, s.position, s.hidden, s.deleted, s.updated, synced
        ],
    )?;
    Ok(())
}

fn find_section(c: &Connection, id: &str) -> rusqlite::Result<Option<(Section, f64)>> {
    c.query_row(
        &format!("SELECT {SECTION_COLS}, synced_updated FROM sections WHERE id = ?1"),
        params![id],
        |r| Ok((section_row(r)?, r.get::<_, f64>(9)?)),
    )
    .optional()
}

pub const BUILT_IN_SECTIONS: [&str; 3] = ["diablo", "workout", "projects"];

fn seed(c: &Connection) -> rusqlite::Result<()> {
    let sections: i64 = c.query_row("SELECT COUNT(*) FROM sections", [], |r| r.get(0))?;
    if sections == 0 {
        let seeds = [
            ("diablo", "Diablo", "flame", "#2d81ff", "", 0),
            (
                "workout",
                "Workout",
                "dumbbell",
                "#3fd6b8",
                "Training log: sets, reps, weights and how it felt. Suggest the next session from the history.",
                1,
            ),
            (
                "projects",
                "Projects",
                "folder",
                "#f2a516",
                "Code and projects across the machines. Hand real work to T3 Code threads.",
                2,
            ),
        ];
        for (id, name, icon, accent, brief, position) in seeds {
            let s = Section {
                id: id.into(),
                name: name.into(),
                icon: icon.into(),
                accent: accent.into(),
                brief: brief.into(),
                position,
                hidden: false,
                deleted: false,
                updated: 0.0,
            };
            upsert_section(c, &s, 0.0)?;
        }
    }
    let seeded: Option<String> = c
        .query_row("SELECT value FROM kv WHERE key = '_hosts_seeded'", [], |r| r.get(0))
        .optional()?;
    if seeded.is_none() {
        c.execute(
            "INSERT OR IGNORE INTO hosts (name, url, priority, failures) VALUES ('diablo-pi', ?1, 0, 0)",
            [PRIMARY_HUB],
        )?;
    }
    if seeded.as_deref() != Some("2") {
        c.execute(
            "INSERT OR IGNORE INTO hosts (name, url, priority, failures) VALUES ('nemurenai', ?1, 1, 0)",
            [STANDBY_HUB],
        )?;
        c.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('_hosts_seeded', '2')", [])?;
    }
    Ok(())
}

/// The phone's local SQLite store. All methods take `&self` and serialise on
/// one internal mutex, so a `Store` is safe to share between threads.
#[derive(uniffi::Object)]
pub struct Store {
    conn: Mutex<Connection>,
}

impl Store {
    fn db(&self) -> MutexGuard<'_, Connection> {
        self.conn.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn sections_where(c: &Connection, filter: &str) -> rusqlite::Result<Vec<Section>> {
        let mut st = c.prepare(&format!(
            "SELECT {SECTION_COLS} FROM sections {filter} ORDER BY position, name, id"
        ))?;
        let rows = st.query_map([], section_row)?.collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    fn query_messages(c: &Connection, sql: &str, args: impl rusqlite::Params) -> rusqlite::Result<Vec<Message>> {
        let mut st = c.prepare(sql)?;
        let rows = st.query_map(args, message_row)?.collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }
}

impl Store {
    /// Drops what a hub taught this store: sections beyond the built-in ones and their messages,
    /// every message the hub already holds, the per-section cursors, and every cached panel, panel
    /// map and usage report. Messages still waiting to be sent stay. All or nothing.
    pub fn forget_hub_cache(&self) -> rusqlite::Result<()> {
        let mut c = self.db();
        let tx = c.transaction()?;
        let ids = BUILT_IN_SECTIONS.iter().map(|id| format!("'{id}'")).collect::<Vec<_>>().join(", ");
        tx.execute(&format!("DELETE FROM messages WHERE section NOT IN ({ids}) OR remote_id IS NOT NULL"), [])?;
        tx.execute(&format!("DELETE FROM sections WHERE id NOT IN ({ids})"), [])?;
        tx.execute(
            "DELETE FROM kv WHERE key IN ('panels-map', 'usage') OR key LIKE 'panel.%' OR key LIKE 'cursor:%'",
            [],
        )?;
        tx.commit()
    }
}

#[uniffi::export]
impl Store {
    /// Opens or creates the database at `path`, enabling WAL and seeding the
    /// default sections and host on first use.
    #[uniffi::constructor]
    pub fn open(path: String) -> Result<Arc<Self>, CoreError> {
        if let Some(parent) = std::path::Path::new(&path).parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent).map_err(|e| CoreError::Db { message: e.to_string() })?;
            }
        }
        let conn = Connection::open(&path)?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        let _: String = conn.query_row("PRAGMA journal_mode = WAL", [], |r| r.get(0))?;
        conn.execute_batch("PRAGMA synchronous = NORMAL;")?;
        conn.execute_batch(SCHEMA)?;
        seed(&conn)?;
        Ok(Arc::new(Store { conn: Mutex::new(conn) }))
    }

    /// Sections that are not deleted, hidden ones included, by position then name.
    pub fn sections(&self) -> Vec<Section> {
        Self::sections_where(&self.db(), "WHERE deleted = 0").unwrap_or_default()
    }

    /// Every section including tombstones, for sync.
    pub fn all_sections(&self) -> Vec<Section> {
        Self::sections_where(&self.db(), "").unwrap_or_default()
    }

    pub fn save_section(&self, section: Section) -> Result<Section, CoreError> {
        if !valid_id(&section.id) {
            return Err(invalid("section id must be 1 to 32 characters of a-z, 0-9 and -"));
        }
        if !valid_accent(&section.accent) {
            return Err(invalid("accent must look like #RRGGBB"));
        }
        let name_len = section.name.trim().chars().count();
        if name_len == 0 || name_len > 40 {
            return Err(invalid("a name is 1 to 40 characters"));
        }
        if section.brief.chars().count() > 2000 {
            return Err(invalid("a brief is at most 2000 characters"));
        }
        if section.id == "diablo" && section.deleted {
            return Err(invalid("the diablo section cannot be removed"));
        }
        let mut saved = section;
        saved.updated = now();
        let c = self.db();
        let synced = find_section(&c, &saved.id)?.map(|(_, s)| s).unwrap_or(0.0);
        upsert_section(&c, &saved, synced)?;
        Ok(saved)
    }

    /// Marks a section deleted. The row stays as a tombstone so the removal syncs.
    pub fn remove_section(&self, id: String) -> Result<(), CoreError> {
        if id == "diablo" {
            return Err(invalid("the diablo section cannot be removed"));
        }
        self.db().execute(
            "UPDATE sections SET deleted = 1, updated = ?2 WHERE id = ?1",
            params![id, now()],
        )?;
        Ok(())
    }

    /// Assigns positions 0..n in the given order. Unknown and deleted ids are skipped.
    pub fn reorder(&self, ids: Vec<String>) -> Result<(), CoreError> {
        let mut seen = HashSet::new();
        if !ids.iter().all(|i| seen.insert(i.clone())) {
            return Err(invalid("duplicate id in order"));
        }
        let mut c = self.db();
        let tx = c.transaction()?;
        let stamp = now();
        let mut position = 0i32;
        for id in &ids {
            let current: Option<(i32, i64)> = tx
                .query_row(
                    "SELECT position, deleted FROM sections WHERE id = ?1",
                    params![id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?;
            match current {
                Some((p, 0)) => {
                    if p != position {
                        tx.execute(
                            "UPDATE sections SET position = ?2, updated = ?3 WHERE id = ?1",
                            params![id, position, stamp],
                        )?;
                    }
                    position += 1;
                }
                _ => {}
            }
        }
        tx.commit()?;
        Ok(())
    }

    /// Merges the hub's sections. Per id the newest `updated` wins; a tie keeps
    /// the local row. Remote winners count as acknowledged, local winners stay dirty.
    pub fn merge_sections(&self, remote: Vec<Section>) -> Vec<Section> {
        let mut c = self.db();
        let _ = (|| -> rusqlite::Result<()> {
            let tx = c.transaction()?;
            for mut r in remote {
                if !valid_id(&r.id) {
                    continue;
                }
                if r.id == "diablo" {
                    r.deleted = false;
                }
                match find_section(&tx, &r.id)? {
                    None => upsert_section(&tx, &r, r.updated)?,
                    Some((local, synced)) => {
                        if r.updated > local.updated {
                            upsert_section(&tx, &r, r.updated)?;
                        } else if r.updated == local.updated && synced < r.updated {
                            tx.execute(
                                "UPDATE sections SET synced_updated = ?2 WHERE id = ?1",
                                params![r.id, r.updated],
                            )?;
                        }
                    }
                }
            }
            tx.commit()
        })();
        Self::sections_where(&c, "WHERE deleted = 0").unwrap_or_default()
    }

    /// Sections edited locally since the hub last acknowledged them, tombstones included.
    pub fn dirty_sections(&self) -> Vec<Section> {
        Self::sections_where(&self.db(), "WHERE updated > synced_updated").unwrap_or_default()
    }

    /// Records that the hub has these versions. A section edited again since
    /// stays dirty because its `updated` is newer than the one passed in.
    pub fn mark_sections_synced(&self, sections: Vec<Section>) {
        let c = self.db();
        for s in sections {
            let _ = c.execute(
                "UPDATE sections SET synced_updated = MAX(synced_updated, ?2) WHERE id = ?1",
                params![s.id, s.updated],
            );
        }
    }

    /// The newest `limit` messages of a section, oldest first.
    pub fn messages(&self, section: String, limit: u32) -> Vec<Message> {
        let c = self.db();
        let mut rows = Self::query_messages(
            &c,
            &format!(
                "SELECT {MESSAGE_COLS} FROM messages WHERE section = ?1 ORDER BY ts DESC, rowid DESC LIMIT ?2"
            ),
            params![section, limit],
        )
        .unwrap_or_default();
        rows.reverse();
        rows
    }

    /// Queues a user message for sending.
    pub fn add_outgoing(&self, section: String, text: String) -> Message {
        let m = Message {
            local_id: uuid::Uuid::new_v4().to_string(),
            remote_id: None,
            section,
            role: "user".into(),
            text,
            ts: now(),
            state: MessageState::Pending,
            meta: None,
        };
        let _ = self.db().execute(
            "INSERT INTO messages (local_id, remote_id, section, role, text, ts, state, meta)
             VALUES (?1, NULL, ?2, ?3, ?4, ?5, 'pending', NULL)",
            params![m.local_id, m.section, m.role, m.text, m.ts],
        );
        m
    }

    /// Upserts hub messages by `remote_id` and returns how many were new.
    /// Messages without a `remote_id` are ignored.
    pub fn merge_remote(&self, section: String, remote: Vec<Message>) -> u32 {
        let mut c = self.db();
        let mut added = 0u32;
        let _ = (|| -> rusqlite::Result<()> {
            let tx = c.transaction()?;
            for m in remote {
                let Some(rid) = m.remote_id else { continue };
                let updated = tx.execute(
                    "UPDATE messages SET section = ?2, role = ?3, text = ?4, ts = ?5, state = 'sent', meta = ?6
                     WHERE remote_id = ?1",
                    params![rid, section, m.role, m.text, m.ts, m.meta],
                )?;
                if updated > 0 {
                    continue;
                }
                if !m.local_id.is_empty() {
                    let linked = tx.execute(
                        "UPDATE messages SET remote_id = ?2, section = ?3, role = ?4, text = ?5, ts = ?6, state = 'sent', meta = ?7
                         WHERE local_id = ?1 AND remote_id IS NULL",
                        params![m.local_id, rid, section, m.role, m.text, m.ts, m.meta],
                    )?;
                    if linked > 0 {
                        continue;
                    }
                }
                let taken: bool = !m.local_id.is_empty()
                    && tx
                        .query_row("SELECT 1 FROM messages WHERE local_id = ?1", params![m.local_id], |_| Ok(()))
                        .optional()?
                        .is_some();
                let local_id = if m.local_id.is_empty() || taken {
                    uuid::Uuid::new_v4().to_string()
                } else {
                    m.local_id
                };
                tx.execute(
                    "INSERT INTO messages (local_id, remote_id, section, role, text, ts, state, meta)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'sent', ?7)",
                    params![local_id, rid, section, m.role, m.text, m.ts, m.meta],
                )?;
                added += 1;
            }
            tx.commit()
        })();
        added
    }

    /// Pending and failed user messages, oldest first.
    pub fn outbox(&self) -> Vec<Message> {
        Self::query_messages(
            &self.db(),
            &format!(
                "SELECT {MESSAGE_COLS} FROM messages
                 WHERE role = 'user' AND state IN ('pending', 'failed') AND remote_id IS NULL
                 ORDER BY ts, rowid"
            ),
            [],
        )
        .unwrap_or_default()
    }

    /// Links a queued message to its hub id and marks it sent. If the hub copy
    /// was already merged, that duplicate row is dropped and the local one kept.
    pub fn mark_sent(&self, local_id: String, remote_id: i64) {
        let mut c = self.db();
        let _ = (|| -> rusqlite::Result<()> {
            let tx = c.transaction()?;
            tx.execute(
                "DELETE FROM messages WHERE remote_id = ?1 AND local_id != ?2",
                params![remote_id, local_id],
            )?;
            tx.execute(
                "UPDATE messages SET remote_id = ?2, state = 'sent' WHERE local_id = ?1",
                params![local_id, remote_id],
            )?;
            tx.commit()
        })();
    }

    pub fn mark_failed(&self, local_id: String) {
        let _ = self.db().execute(
            "UPDATE messages SET state = 'failed' WHERE local_id = ?1 AND remote_id IS NULL",
            params![local_id],
        );
    }

    /// Puts a failed message back in the queue.
    pub fn retry(&self, local_id: String) {
        let _ = self.db().execute(
            "UPDATE messages SET state = 'pending' WHERE local_id = ?1 AND state = 'failed' AND remote_id IS NULL",
            params![local_id],
        );
    }

    /// The highest hub id stored for a section, 0 when there is none.
    pub fn last_remote_id(&self, section: String) -> i64 {
        self.db()
            .query_row(
                "SELECT COALESCE(MAX(remote_id), 0) FROM messages WHERE section = ?1",
                params![section],
                |r| r.get(0),
            )
            .unwrap_or(0)
    }

    /// Hosts in the order `choose_order` gives for the current time.
    pub fn hosts(&self) -> Vec<Host> {
        let list = {
            let c = self.db();
            let rows = c
                .prepare("SELECT name, url, priority, last_ok, last_ms, failures FROM hosts ORDER BY name")
                .and_then(|mut st| st.query_map([], host_row)?.collect::<Result<Vec<_>, _>>());
            rows.unwrap_or_default()
        };
        choose_order(list, now())
    }

    /// Inserts or replaces a host by name.
    pub fn save_host(&self, host: Host) -> Result<(), CoreError> {
        if host.name.trim().is_empty() {
            return Err(invalid("host name is empty"));
        }
        if !(host.url.starts_with("http://") || host.url.starts_with("https://")) {
            return Err(invalid("host url must start with http:// or https://"));
        }
        self.db().execute(
            "INSERT INTO hosts (name, url, priority, last_ok, last_ms, failures) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
             ON CONFLICT(name) DO UPDATE SET url = ?2, priority = ?3, last_ok = ?4, last_ms = ?5, failures = ?6",
            params![host.name, host.url, host.priority, host.last_ok, host.last_ms, host.failures],
        )?;
        Ok(())
    }

    pub fn remove_host(&self, name: String) {
        let _ = self.db().execute("DELETE FROM hosts WHERE name = ?1", params![name]);
    }

    /// Records a health probe: success resets failures and stores latency,
    /// failure adds one to the failure count.
    pub fn record_probe(&self, name: String, ok: bool, ms: u32) {
        let c = self.db();
        let _ = if ok {
            c.execute(
                "UPDATE hosts SET last_ok = ?2, last_ms = ?3, failures = 0 WHERE name = ?1",
                params![name, now(), ms],
            )
        } else {
            c.execute("UPDATE hosts SET failures = failures + 1 WHERE name = ?1", params![name])
        };
    }

    pub fn get(&self, key: String) -> Option<String> {
        self.db()
            .query_row("SELECT value FROM kv WHERE key = ?1", params![key], |r| r.get(0))
            .optional()
            .ok()
            .flatten()
    }

    pub fn set(&self, key: String, value: String) {
        let _ = self.db().execute(
            "INSERT INTO kv (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = ?2",
            params![key, value],
        );
    }
}
