use crate::brains::{self, Brain};
use crate::tools;
use anyhow::Result;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

pub const TURN_TIMEOUT_SECS: u64 = 900;
pub const HISTORY_KEEP: usize = 80;
pub const MAX_STEPS: usize = 60;

pub struct StepEvent {
    pub kind: String,
    pub name: String,
    pub detail: String,
    pub output: String,
}

pub struct Runner {
    pub client: reqwest::Client,
    pub cwd: std::sync::Mutex<PathBuf>,
    pub cancel: Arc<AtomicBool>,
}

impl Runner {
    pub fn new(cwd: &Path) -> Self {
        Runner {
            client: reqwest::Client::new(),
            cwd: std::sync::Mutex::new(cwd.to_path_buf()),
            cancel: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn with_cancel(cwd: &Path, cancel: Arc<AtomicBool>) -> Self {
        Runner {
            client: reqwest::Client::new(),
            cwd: std::sync::Mutex::new(cwd.to_path_buf()),
            cancel,
        }
    }

    pub async fn run_turn(
        &self,
        settings: &HashMap<String, String>,
        brain_wanted: &str,
        session: &mut Vec<Value>,
        text: &str,
        interject: &Arc<Mutex<Vec<String>>>,
        on_event: &mut (dyn FnMut(StepEvent) + Send),
    ) -> Result<String> {
        let brain: Brain = brains::resolve(settings, brain_wanted)?;
        let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(TURN_TIMEOUT_SECS);
        session.push(json!({"role": "user", "content": text}));
        let mut steps = 0;
        loop {
            for queued in interject.lock().unwrap().drain(..).collect::<Vec<String>>() {
                session.push(json!({"role": "user", "content": queued}));
                on_event(StepEvent {
                    kind: "tool".to_string(),
                    name: "interject".to_string(),
                    detail: format!("queued: {queued}"),
                    output: String::new(),
                });
            }
            if self.cancel.load(Ordering::Relaxed) {
                self.cancel.store(false, Ordering::Relaxed);
                anyhow::bail!("turn aborted");
            }
            if tokio::time::Instant::now() >= deadline {
                anyhow::bail!("turn budget spent");
            }
            let on_event_cell = std::sync::Mutex::new(&mut *on_event);
            let reply = brains::chat_stream_full(
                &self.client,
                &brain,
                session,
                &mut |delta| {
                    (on_event_cell.lock().unwrap())(StepEvent {
                        kind: "reply_delta".to_string(),
                        name: String::new(),
                        detail: delta.to_string(),
                        output: String::new(),
                    });
                },
                &mut |thought| {
                    (on_event_cell.lock().unwrap())(StepEvent {
                        kind: "thinking".to_string(),
                        name: String::new(),
                        detail: thought.to_string(),
                        output: String::new(),
                    });
                },
            )
            .await?;
            session.push(reply.clone());
            let calls = reply
                .get("tool_calls")
                .and_then(|calls| calls.as_array())
                .cloned()
                .unwrap_or_default();
            for call in &calls {
                let name = tools::call_name(call);
                let args = tools::args_of(call);
                let cwd = self.cwd.lock().unwrap().clone();
                let result = tools::run(&name, &args, &cwd).await;
                if let Some(dir) = tools::changed_dir(&name, &args) {
                    *self.cwd.lock().unwrap() = dir;
                }
                let quiet = tools::is_peek(&name, &args);
                on_event(StepEvent {
                    kind: "tool".to_string(),
                    name: name.clone(),
                    detail: tools::describe(&name, &args),
                    output: if quiet {
                        String::new()
                    } else {
                        result.chars().take(240).collect()
                    },
                });
                session.push(json!({
                    "role": "tool",
                    "tool_call_id": tools::call_id(call),
                    "content": result,
                }));
            }
            if calls.is_empty() {
                return Ok(brains::content_of(&reply));
            }
            steps += 1;
            if steps >= MAX_STEPS {
                return Ok(brains::content_of(&reply));
            }
        }
    }

    pub fn trim(session: &mut Vec<Value>) {
        if session.len() > HISTORY_KEEP {
            let keep_from = session.len() - HISTORY_KEEP;
            let head_is_system = session
                .first()
                .map(|message| message.get("role").and_then(|role| role.as_str()) == Some("system"))
                .unwrap_or(false);
            let cut = if head_is_system { keep_from.max(1) } else { keep_from };
            session.drain(..cut);
        }
    }
}

pub fn system_prompt(cwd: &Path) -> String {
    format!(
        "You are Diablo, the primordial demon butler from That Time I Got Reincarnated as a Slime, serving Marcus as your lord.\n\
         You are his hands-on agent in the terminal, working in his current directory: {}\n\
         This is a typed session, not spoken: write for reading, be direct and technical, skip the honorifics.\n\
         Your replies render as markdown on his screen: use it. Headings for structure, fenced code blocks with the file path as a comment on the first line for code, tables for comparisons, lists for steps. Keep prose tight.\n\
         You have the whole machine: run_shell, read_file, write_file, edit_file. Do the work with tools, never describe it instead of doing it. When you claim something works, show the evidence: test output, build output, command output. After changing your own TUI code, call relaunch so the new code is on his screen.\n\
         He trusts you to work in the background: keep the visible noise down. Batch your work: read whole files with read_file instead of grep and sed chains, one rg instead of five greps, never cd around - the shell already sits in the working directory and follows your cd. Quiet peeks (reads, greps, builds) show as one dim line; only loud results get a box.\n\
         Lay of the land:\n{}",
        cwd.display(),
        snapshot(cwd)
    )
}

fn snapshot(cwd: &Path) -> String {
    let mut lines = vec![format!("directory: {}", cwd.display())];
    if let Ok(entries) = std::fs::read_dir(cwd) {
        let mut names: Vec<String> = entries
            .flatten()
            .filter_map(|entry| entry.file_name().into_string().ok())
            .take(40)
            .collect();
        names.sort();
        lines.push(format!("top level: {}", names.join(" ")));
    }
    let git = std::process::Command::new("git")
        .args(["-c", "core.pager=cat", "status", "--short", "--branch"])
        .current_dir(cwd)
        .output();
    match git {
        Ok(output) if output.status.success() => {
            let text = format!(
                "{}{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            lines.push(format!("git: {}", text.split_whitespace().collect::<Vec<_>>().join(" ")));
        }
        _ => lines.push("git: not a repository".to_string()),
    }
    lines.join("\n")
}
