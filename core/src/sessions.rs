use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Record {
    pub id: String,
    pub title: String,
    pub cwd: String,
    pub created: String,
    pub updated: String,
    pub messages: Vec<serde_json::Value>,
}

pub struct Store {
    dir: PathBuf,
}

pub fn sessions_dir() -> PathBuf {
    let override_dir = std::env::var("DIABLO_POOL_STATE_DIR").ok();
    let base = override_dir
        .map(PathBuf::from)
        .or_else(|| dirs::home_dir().map(|home| home.join(".local/state/diablo")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("sessions")
}

impl Store {
    pub fn new() -> Self {
        Store { dir: sessions_dir() }
    }

    pub fn with_dir(dir: PathBuf) -> Self {
        Store { dir }
    }

    pub fn save(&self, record: &Record) -> Result<PathBuf> {
        std::fs::create_dir_all(&self.dir)?;
        let path = self.dir.join(format!("{}.json", record.id));
        let tmp = self.dir.join(format!(".{}.tmp", record.id));
        std::fs::write(&tmp, serde_json::to_string(record)?)?;
        std::fs::rename(&tmp, &path)?;
        Ok(path)
    }

    pub fn list(&self) -> Vec<Record> {
        let mut records: Vec<Record> = std::fs::read_dir(&self.dir)
            .into_iter()
            .flatten()
            .filter_map(|entry| entry.ok())
            .filter(|entry| {
                entry
                    .path()
                    .extension()
                    .and_then(|ext| ext.to_str())
                    .map(|ext| ext == "json")
                    .unwrap_or(false)
            })
            .filter_map(|entry| {
                let text = std::fs::read_to_string(entry.path()).ok()?;
                serde_json::from_str(&text).ok()
            })
            .collect();
        records.sort_by(|a, b| b.updated.cmp(&a.updated));
        records
    }

    pub fn last_for(&self, cwd: &str) -> Option<Record> {
        self.list().into_iter().find(|record| record.cwd == cwd)
    }

    pub fn load(&self, id: &str) -> Option<Record> {
        let path = self.dir.join(format!("{id}.json"));
        let text = std::fs::read_to_string(path).ok()?;
        serde_json::from_str(&text).ok()
    }
}

pub fn new_record(cwd: &Path, system: &str) -> Record {
    let now = chrono::Local::now().to_rfc3339();
    Record {
        id: uuid::Uuid::new_v4().simple().to_string()[..12].to_string(),
        title: "new session".to_string(),
        cwd: cwd.display().to_string(),
        created: now.clone(),
        updated: now,
        messages: vec![serde_json::json!({"role": "system", "content": system})],
    }
}

pub fn settle_title(record: &mut Record) {
    if record.title != "new session" && !record.title.is_empty() {
        return;
    }
    for message in &record.messages {
        if message.get("role").and_then(|role| role.as_str()) == Some("user") {
            let content = message
                .get("content")
                .and_then(|content| content.as_str())
                .unwrap_or_default();
            let collapsed: String = content.split_whitespace().collect::<Vec<_>>().join(" ");
            if !collapsed.is_empty() {
                record.title = collapsed.chars().take(60).collect();
            }
            return;
        }
    }
}

pub fn timeago(updated: &str) -> String {
    let then = match chrono::DateTime::parse_from_rfc3339(updated) {
        Err(_) => return "?".to_string(),
        Ok(then) => then,
    };
    let seconds = chrono::Local::now().signed_duration_since(then).num_seconds().max(0);
    if seconds < 60 {
        "now".to_string()
    } else if seconds < 3600 {
        format!("{}m", seconds / 60)
    } else if seconds < 86_400 {
        format!("{}h", seconds / 3600)
    } else {
        format!("{}d", seconds / 86_400)
    }
}
