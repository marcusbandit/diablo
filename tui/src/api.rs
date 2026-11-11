use anyhow::{anyhow, bail, Result};
use futures::StreamExt;
use serde_json::{json, Value};
use std::path::Path;

pub const PORT: u16 = 8766;

#[derive(Clone, Debug)]
pub struct SessionInfo {
    pub id: String,
    pub title: String,
    pub cwd: String,
    pub ago: String,
}

pub enum TurnEvent {
    Tool(String),
    Result(String, String),
    ReplyDelta(String),
    Reply(String),
    Thinking(String),
    Error(String),
    Done,
}

#[derive(Clone)]
pub struct Core {
    client: reqwest::Client,
}

impl Core {
    pub fn new() -> Self {
        Core { client: reqwest::Client::new() }
    }

    fn url(&self, path: &str) -> String {
        format!("http://127.0.0.1:{PORT}{path}")
    }

    pub async fn up(&self) -> bool {
        self.client
            .get(self.url("/sessions"))
            .timeout(std::time::Duration::from_secs(2))
            .send()
            .await
            .map(|response| response.status().is_success())
            .unwrap_or(false)
    }

    pub async fn wait_or_spawn(&self) -> Result<()> {
        if self.up().await {
            return Ok(());
        }
        spawn_detached_core().await?;
        let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(30);
        while tokio::time::Instant::now() < deadline {
            if self.up().await {
                return Ok(());
            }
            tokio::time::sleep(std::time::Duration::from_millis(300)).await;
        }
        bail!("diablo-core serve did not come up")
    }

    /// Stops the running core and starts the freshly installed binary.
    /// Session state lives on disk, so the new core resumes exactly where
    /// the old one left off; the agent never notices.
    pub async fn restart_core(&self) -> Result<()> {
        if let Ok(output) = std::process::Command::new("pgrep").args(["-x", "diablo-core"]).output() {
            for pid in std::str::from_utf8(&output.stdout).unwrap_or("").lines() {
                let _ = std::process::Command::new("kill").arg(pid.trim()).status();
            }
        }
        let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(10);
        while tokio::time::Instant::now() < deadline {
            if !self.up().await {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        }
        spawn_detached_core().await?;
        let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(30);
        while tokio::time::Instant::now() < deadline {
            if self.up().await {
                return Ok(());
            }
            tokio::time::sleep(std::time::Duration::from_millis(300)).await;
        }
        bail!("diablo-core serve did not come back up")
    }

    pub async fn list_sessions(&self) -> Result<Vec<SessionInfo>> {
        let body: Value = self
            .client
            .get(self.url("/sessions"))
            .timeout(std::time::Duration::from_secs(5))
            .send()
            .await?
            .error_for_ref()
            .await?
            .json()
            .await?;
        let items = body
            .get("sessions")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        Ok(items
            .iter()
            .filter_map(|item| {
                Some(SessionInfo {
                    id: item.get("id")?.as_str()?.to_string(),
                    title: item.get("title").and_then(|t| t.as_str()).unwrap_or("").to_string(),
                    cwd: item.get("cwd").and_then(|t| t.as_str()).unwrap_or("").to_string(),
                    ago: item.get("ago").and_then(|t| t.as_str()).unwrap_or("?").to_string(),
                })
            })
            .collect())
    }

    pub async fn load_session(&self, id: &str) -> Result<Value> {
        Ok(self
            .client
            .get(self.url(&format!("/sessions/{id}")))
            .timeout(std::time::Duration::from_secs(5))
            .send()
            .await?
            .error_for_ref()
            .await?
            .json()
            .await?)
    }

    pub async fn create_session(&self, cwd: &Path) -> Result<SessionInfo> {
        let body: Value = self
            .client
            .post(self.url("/sessions"))
            .json(&json!({"cwd": cwd.display().to_string()}))
            .timeout(std::time::Duration::from_secs(30))
            .send()
            .await?
            .error_for_ref()
            .await?
            .json()
            .await?;
        Ok(SessionInfo {
            id: body.get("id").and_then(|v| v.as_str()).unwrap_or_default().to_string(),
            title: body.get("title").and_then(|v| v.as_str()).unwrap_or("new session").to_string(),
            cwd: body.get("cwd").and_then(|v| v.as_str()).unwrap_or_default().to_string(),
            ago: "now".to_string(),
        })
    }

    pub async fn cancel(&self, id: &str) -> Result<()> {
        self.client
            .post(self.url(&format!("/sessions/{id}/cancel")))
            .timeout(std::time::Duration::from_secs(3))
            .send()
            .await?
            .error_for_ref()
            .await?;
        Ok(())
    }

    pub async fn interject(&self, id: &str, text: &str) -> Result<()> {
        self.client
            .post(self.url(&format!("/sessions/{id}/interject")))
            .json(&json!({"text": text}))
            .timeout(std::time::Duration::from_secs(3))
            .send()
            .await?
            .error_for_ref()
            .await?;
        Ok(())
    }

    pub async fn turn_stream(
        &self,
        id: &str,
        text: &str,
        mut on_event: impl FnMut(TurnEvent) + Send,
    ) -> Result<()> {
        let response = self
            .client
            .post(self.url(&format!("/sessions/{id}/turn/stream")))
            .json(&json!({"text": text}))
            .timeout(std::time::Duration::from_secs(1000))
            .send()
            .await?
            .error_for_ref()
            .await?;
        let mut stream = response.bytes_stream();
        let mut buffer = String::new();
        while let Some(chunk) = stream.next().await {
            let bytes = chunk?;
            buffer.push_str(&String::from_utf8_lossy(&bytes));
            while let Some(pos) = buffer.find('\n') {
                let line: String = buffer.drain(..=pos).collect();
                let line = line.trim();
                if line.is_empty() {
                    continue;
                }
                let value: Value = match serde_json::from_str(line) {
                    Ok(value) => value,
                    Err(_) => continue,
                };
                match value.get("type").and_then(|t| t.as_str()) {
                    Some("tool") => on_event(TurnEvent::Tool(
                        value.get("detail").and_then(|d| d.as_str()).unwrap_or("").to_string(),
                    )),
                    Some("result") => on_event(TurnEvent::Result(
                        value.get("detail").and_then(|d| d.as_str()).unwrap_or("").to_string(),
                        value.get("output").and_then(|d| d.as_str()).unwrap_or("").to_string(),
                    )),
                    Some("reply_delta") => on_event(TurnEvent::ReplyDelta(
                        value.get("text").and_then(|d| d.as_str()).unwrap_or("").to_string(),
                    )),
                    Some("thinking") => on_event(TurnEvent::Thinking(
                        value.get("text").and_then(|d| d.as_str()).unwrap_or("").to_string(),
                    )),
                    Some("reply") => on_event(TurnEvent::Reply(
                        value.get("text").and_then(|d| d.as_str()).unwrap_or("").to_string(),
                    )),
                    Some("error") => on_event(TurnEvent::Error(
                        value.get("message").and_then(|d| d.as_str()).unwrap_or("the turn failed").to_string(),
                    )),
                    Some("done") => {
                        on_event(TurnEvent::Done);
                        return Ok(());
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }
}

trait ErrorForRef {
    async fn error_for_ref(self) -> Result<reqwest::Response>;
}

impl ErrorForRef for reqwest::Response {
    async fn error_for_ref(self) -> Result<Self> {
        let status = self.status();
        if status.is_success() {
            Ok(self)
        } else {
            let text = self.text().await.unwrap_or_default();
            bail!("core returned {}: {}", status, text.chars().take(200).collect::<String>())
        }
    }
}

fn find_core_exe() -> Option<std::path::PathBuf> {
    if let Ok(exe) = std::env::current_exe() {
        let sibling = exe.parent()?.join("diablo-core");
        if sibling.exists() {
            return Some(sibling);
        }
    }
    for path in std::env::var("PATH").ok()?.split(':') {
        let candidate = std::path::PathBuf::from(path).join("diablo-core");
        if candidate.exists() {
            return Some(candidate);
        }
    }
    None
}

/// Spawns the core fully detached: its own session, own process group,
/// stdio cut. If the TUI dies, the core keeps running for every other
/// frontend.
async fn spawn_detached_core() -> Result<()> {
    let exe = find_core_exe().ok_or_else(|| anyhow!("diablo-core binary not found; build core/ first"))?;
    tokio::process::Command::new("setsid")
        .arg(&exe)
        .arg("serve")
        .process_group(0)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()?;
    Ok(())
}
