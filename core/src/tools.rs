use std::collections::HashSet;
use std::sync::Mutex;

use serde_json::{json, Value};
use std::path::{Path, PathBuf};

static READ_FILES: Mutex<Option<HashSet<PathBuf>>> = Mutex::new(None);

fn mark_read(path: &Path) {
    let mut guard = READ_FILES.lock().unwrap();
    guard.get_or_insert_with(HashSet::new).insert(path.to_path_buf());
}

fn was_read(path: &Path) -> bool {
    READ_FILES
        .lock()
        .unwrap()
        .as_ref()
        .map(|set| set.contains(path))
        .unwrap_or(false)
}

pub fn changed_dir(name: &str, args: &Value) -> Option<PathBuf> {
    if name != "run_shell" {
        return None;
    }
    let command = arg(args, "command");
    let rest = command.strip_prefix("cd ")?;
    let rest = rest.trim();
    if rest.starts_with('-') {
        return None;
    }
    let end = rest.find(" && ").or_else(|| rest.find(';'))?;
    let dir = rest[..end].trim().trim_matches('"').trim_matches('\'');
    if dir.is_empty() || dir.contains('$') || dir.contains('~') == false && !dir.starts_with('/') && !dir.starts_with('.') {
        return None;
    }
    let expanded = if let Some(stripped) = dir.strip_prefix("~/") {
        dirs::home_dir()?.join(stripped)
    } else {
        PathBuf::from(dir)
    };
    if expanded.is_dir() {
        Some(expanded)
    } else {
        None
    }
}

pub fn is_peek(name: &str, args: &Value) -> bool {
    match name {
        "read_file" => true,
        "run_shell" => {
            let command = arg(args, "command");
            let peekers = ["grep", "rg ", "ls", "cat", "head", "tail", "wc", "find", "sed -n", "awk", "which", "echo", "pwd", "git status", "git log", "git diff", "git show", "cargo test", "cargo build", "python3 -c"];
            peekers.iter().any(|p| command.starts_with(p))
                && !command.contains('>')
                && !command.contains("tee ")
        }
        _ => false,
    }
}

pub const FORBIDDEN: [&str; 6] = ["rm -rf /", "mkfs", "dd if=", "shutdown", "reboot", ":(){:|:&};:"];
const SHELL_TIMEOUT_SECS: u64 = 120;
const OUTPUT_LIMIT: usize = 8000;
const READ_LIMIT: usize = 40000;
const WRITE_LIMIT: usize = 200_000;

pub fn schema() -> Value {
    json!([
        {"type": "function", "function": {
            "name": "run_shell",
            "description": "Run a shell command on this machine and return its output",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
        }},
        {"type": "function", "function": {
            "name": "read_file",
            "description": "Read a file, truncated when enormous",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
        }},
        {"type": "function", "function": {
            "name": "write_file",
            "description": "Write a file whole, creating parent directories",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}
        }},
        {"type": "function", "function": {
            "name": "edit_file",
            "description": "Replace an exact older string with a new one in a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["path", "old", "new"]}
        }},
        {"type": "function", "function": {
            "name": "relaunch",
            "description": "Rebuild the TUI with the current code and relaunch it. The screen comes back in a fresh session after this turn",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }}
    ])
}

pub async fn run(name: &str, args: &Value, cwd: &Path) -> String {
    let in_cwd = |path: &str| -> PathBuf {
        let expanded = expand(path);
        if expanded.is_absolute() {
            expanded
        } else {
            cwd.join(expanded)
        }
    };
    match name {
        "run_shell" => run_shell(&arg(args, "command"), cwd).await,
        "read_file" => {
            let path = in_cwd(&arg(args, "path"));
            let out = read_file(&path);
            mark_read(&path);
            out
        }
        "write_file" => write_file(&in_cwd(&arg(args, "path")), &arg(args, "content")),
        "edit_file" => {
            let path = in_cwd(&arg(args, "path"));
            if !was_read(&path) {
                return "refused: read the file before editing it".to_string();
            }
            edit_file(&path, &arg(args, "old"), &arg(args, "new"))
        }
        "relaunch" => relaunch_tui(),
        other => format!("unknown tool: {other}"),
    }
}

fn arg(args: &Value, key: &str) -> String {
    args.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string()
}

fn expand(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = dirs::home_dir() {
            return home.join(rest);
        }
    }
    PathBuf::from(path)
}

async fn run_shell(command: &str, cwd: &Path) -> String {
    if command.trim().is_empty() {
        return "no command given".to_string();
    }
    let low = command.to_lowercase();
    if FORBIDDEN.iter().any(|item| low.contains(item)) {
        return "refused: destructive command".to_string();
    }
    let output = tokio::time::timeout(
        std::time::Duration::from_secs(SHELL_TIMEOUT_SECS),
        tokio::process::Command::new("sh")
            .arg("-c")
            .arg(command)
            .current_dir(cwd)
            .output(),
    )
    .await;
    match output {
        Err(_) => format!("timed out after {SHELL_TIMEOUT_SECS}s"),
        Ok(Err(error)) => format!("failed: {error}"),
        Ok(Ok(output)) => {
            let mut text = String::from_utf8_lossy(&output.stdout).to_string();
            text.push_str(&String::from_utf8_lossy(&output.stderr));
            let text = text.trim().to_string();
            if text.is_empty() {
                "(no output)".to_string()
            } else {
                text.chars().take(OUTPUT_LIMIT).collect()
            }
        }
    }
}

fn read_file(path: &Path) -> String {
    if path.as_os_str().is_empty() {
        return "no path given".to_string();
    }
    match std::fs::read_to_string(path) {
        Err(error) => format!("failed: {error}"),
        Ok(text) => {
            if text.len() > READ_LIMIT {
                let cut: String = text.chars().take(READ_LIMIT).collect();
                format!("{cut}\n[truncated]")
            } else if text.is_empty() {
                "(empty file)".to_string()
            } else {
                text
            }
        }
    }
}

fn write_file(path: &Path, content: &str) -> String {
    if path.as_os_str().is_empty() {
        return "no path given".to_string();
    }
    if content.len() > WRITE_LIMIT {
        return "refused: content over the size limit".to_string();
    }
    match std::fs::create_dir_all(parent_of(path)).and_then(|_| std::fs::write(path, content)) {
        Err(error) => format!("failed: {error}"),
        Ok(_) => format!("wrote {} bytes to {}", content.len(), path.display()),
    }
}

fn parent_of(path: &Path) -> &Path {
    path.parent().unwrap_or(Path::new("."))
}

fn relaunch_tui() -> String {
    let home = match dirs::home_dir() {
        Some(home) => home,
        None => return "failed: no home directory".to_string(),
    };
    let repo = home.join("Projects/diablo");
    if !repo.join("tui/Cargo.toml").is_file() {
        return format!("failed: no TUI checkout at {}", repo.display());
    }
    let build = std::process::Command::new("cargo")
        .args(["build", "--release", "-p", "diablo-tui"])
        .current_dir(&repo)
        .output();
    let out = match build {
        Ok(out) => out,
        Err(error) => return format!("failed: {error}"),
    };
    if !out.status.success() {
        let tail: String = String::from_utf8_lossy(&out.stderr)
            .lines()
            .rev()
            .take(6)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>()
            .join("\n");
        return format!("build failed:\n{tail}");
    }
    let built = repo.join("target/release/diablo-tui");
    let staged = home.join(".local/bin/.diablo-tui.new");
    let target = home.join(".local/bin/diablo-tui");
    if std::fs::copy(&built, &staged).is_err() {
        return "failed: could not stage the new binary".to_string();
    }
    if std::fs::rename(&staged, &target).is_err() {
        return "failed: could not move it into place".to_string();
    }
    "rebuilt and installed; the TUI relaunches when this turn ends".to_string()
}

fn edit_file(path: &Path, old: &str, new: &str) -> String {
    if path.as_os_str().is_empty() {
        return "no path given".to_string();
    }
    if old.is_empty() {
        return "refused: an empty older string would replace the whole file".to_string();
    }
    let text = match std::fs::read_to_string(path) {
        Err(error) => return format!("failed: {error}"),
        Ok(text) => text,
    };
    let count = text.matches(old).count();
    if count == 0 {
        return "the older string is not in the file".to_string();
    }
    if count > 1 {
        return format!("the older string appears {count} times, give a longer one");
    }
    let edited = text.replacen(old, new, 1);
    match std::fs::write(path, edited) {
        Err(error) => format!("failed: {error}"),
        Ok(_) => "edited".to_string(),
    }
}

pub fn args_of(call: &Value) -> Value {
    let raw = call
        .get("function")
        .and_then(|function| function.get("arguments"))
        .cloned()
        .unwrap_or(Value::String("{}".to_string()));
    match raw {
        Value::Object(map) => Value::Object(map),
        Value::String(text) => serde_json::from_str(&text).unwrap_or(json!({})),
        _ => json!({}),
    }
}

pub fn call_name(call: &Value) -> String {
    call.pointer("/function/name")
        .and_then(|name| name.as_str())
        .unwrap_or_default()
        .to_string()
}

pub fn call_id(call: &Value) -> String {
    call.get("id")
        .and_then(|id| id.as_str())
        .unwrap_or_default()
        .to_string()
}

pub fn describe(name: &str, args: &Value) -> String {
    match name {
        "run_shell" => format!("run: {}", truncate(&arg(args, "command"), 160)),
        "read_file" => format!("read: {}", arg(args, "path")),
        "write_file" => format!(
            "write: {} ({} bytes)",
            arg(args, "path"),
            arg(args, "content").len()
        ),
        "edit_file" => format!("edit: {}", arg(args, "path")),
        "relaunch" => "relaunch: rebuild and reload the TUI".to_string(),
        other => other.to_string(),
    }
}

fn truncate(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

pub fn shape(message: &Value) -> Value {
    let text = crate::brains::content_of(message);
    json!({
        "info": {"role": "assistant"},
        "parts": if text.is_empty() { vec![] } else { vec![json!({"type": "text", "text": text})] }
    })
}
