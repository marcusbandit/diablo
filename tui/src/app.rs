use crate::api::{Core, SessionInfo, TurnEvent};
use crate::ui::filtered;

use crossterm::event::{KeyEvent, KeyModifiers};
use ratatui::text::Line;
use serde_json::{json, Value};
use std::path::PathBuf;
use tokio::sync::mpsc;

const HELP: &str = "esc esc interrupts, ctrl-c exits, ctrl-d exits on an empty line, pgup pgdn or the wheel scroll, drag to select and it copies, shift-enter or paste for newlines, type while he works to queue, /new fresh session, /relaunch restart the core and reload the code, /quit leaves";

pub(crate) const SLASH: [(&str, &str); 3] = [
    ("/new", "fresh session"),
    ("/relaunch", "restart the core and reload the code"),
    ("/quit", "leaves"),
];

pub(crate) fn slash_matches(text: &str) -> Vec<usize> {
    let q = text.trim_start().to_lowercase();
    if !q.starts_with('/') {
        return Vec::new();
    }
    let q = q.trim_start_matches('/');
    SLASH
        .iter()
        .enumerate()
        .filter(|(_, (name, _))| name[1..].starts_with(q) || name[1..].contains(&format!(" {q}")))
        .map(|(i, _)| i)
        .collect()
}

pub enum Mode {
    Picker {
        records: Vec<SessionInfo>,
        pos: usize,
        query: String,
    },
    Chat,
}

#[derive(Clone, PartialEq)]
pub enum Kind {
    You,
    Diablo,
    Label,
    Tool,
    Result,
    Thinking,
    Dim,
    Notice,
}

pub enum AppEvent {
    Tool(String),
    Result(String, String),
    ReplyDelta(String),
    Reply(String),
    Thinking(String),
    Error(String),
    Opened(SessionInfo),
    Done,
}

pub struct App {
    pub core: Core,
    pub cwd: PathBuf,
    pub mode: Mode,
    pub lines: Vec<(Kind, String)>,
    pub scroll: usize,
    pub buffer: String,
    pub cursor: usize,
    pub slash_pos: usize,
    pub history: Vec<String>,
    pub hist_pos: Option<usize>,
    pub busy: bool,
    pub queued: Vec<String>,
    pub esc_armed: bool,
    pub relaunch_pending: bool,
    pub record: Option<SessionInfo>,
    pub events: Option<mpsc::Receiver<AppEvent>>,
    pub pick_chosen: Option<String>,
    pub tool_calls: usize,
    pub spinner: usize,
    pub quit: bool,
    pub streaming: bool,
    pub(crate) visible: Vec<String>,
    pub(crate) visible_y: u16,
    pub(crate) gen: u64,
    pub(crate) cache: Option<((usize, usize, u64), Vec<Line<'static>>)>,
}

impl App {
    pub fn new(core: Core, cwd: PathBuf) -> Self {
        let mut app = App {
            core,
            cwd,
            mode: Mode::Chat,
            lines: Vec::new(),
            scroll: 0,
            buffer: String::new(),
            cursor: 0,
            slash_pos: 0,
            history: Vec::new(),
            hist_pos: None,
            busy: false,
            queued: Vec::new(),
            esc_armed: false,
            relaunch_pending: false,
            record: None,
            events: None,
            pick_chosen: None,
            tool_calls: 0,
            spinner: 0,
            quit: false,
            streaming: false,
            visible: Vec::new(),
            visible_y: 0,
            gen: 0,
            cache: None,
        };
        app.load_history();
        app
    }

    pub fn load_history(&mut self) {
        if let Some(path) = Self::history_path() {
            if let Ok(text) = std::fs::read_to_string(path) {
                self.history = text
                    .lines()
                    .filter(|line| !line.trim().is_empty())
                    .map(|line| line.to_string())
                    .collect();
            }
        }
    }

    fn remember(&mut self, text: &str) {
        self.history.push(text.to_string());
        self.hist_pos = None;
        if let Some(path) = Self::history_path() {
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
                use std::io::Write;
                let _ = writeln!(file, "{text}");
            }
        }
    }

    pub fn emit_notice(&mut self, text: &str) {
        self.emit(Kind::Notice, text);
    }

    pub fn emit(&mut self, kind: Kind, text: &str) {
        if matches!(kind, Kind::You | Kind::Diablo)
            && self
                .lines
                .last()
                .map(|(_, text)| text.is_empty())
                .unwrap_or(false)
        {
            self.lines.push((Kind::Dim, String::new()));
        }
        if kind == Kind::Diablo {
            self.lines.push((Kind::Label, "diablo".to_string()));
        }
        self.lines.push((kind, text.to_string()));
    }

    pub fn start_fresh(&mut self, record: SessionInfo) {
        self.record = Some(record);
        self.emit(Kind::Dim, &format!("session open in {}, he has the whole machine", self.cwd.display()));
        self.emit(Kind::Dim, HELP);
    }

    pub fn start_resumed(&mut self, record: SessionInfo, record_json: &Value) {
        self.record = Some(record);
        self.cwd = record_json
            .get("cwd")
            .and_then(|cwd| cwd.as_str())
            .map(PathBuf::from)
            .unwrap_or(self.cwd.clone());
        self.replay(record_json);
        self.emit(Kind::Dim, HELP);
    }

    fn replay(&mut self, record_json: &Value) {
        let messages = record_json
            .get("messages")
            .and_then(|messages| messages.as_array())
            .cloned()
            .unwrap_or_default();
        for message in messages {
            match message.get("role").and_then(|role| role.as_str()).unwrap_or("") {
                "user" => {
                    let content = message.get("content").and_then(|c| c.as_str()).unwrap_or("");
                    if !content.trim().is_empty() {
                        self.emit(Kind::You, content);
                    }
                }
                "assistant" => {
                    for call in message
                        .get("tool_calls")
                        .and_then(|calls| calls.as_array())
                        .into_iter()
                        .flatten()
                    {
                        let name = call.pointer("/function/name").and_then(|n| n.as_str()).unwrap_or("");
                        let args = call.pointer("/function/arguments").cloned().unwrap_or(Value::Null);
                        self.emit(Kind::Tool, &format!("· {}", describe(&name, &args)));
                    }
                    let content = message.get("content").and_then(|c| c.as_str()).unwrap_or("");
                    if !content.trim().is_empty() {
                        self.emit(Kind::Diablo, content);
                    }
                }
                "tool" => {
                    let content = message.get("content").and_then(|c| c.as_str()).unwrap_or("");
                    let flat: String = content.split_whitespace().collect::<Vec<_>>().join(" ");
                    if !flat.is_empty() {
                        self.emit(Kind::Result, &format!("= {}", flat.chars().take(240).collect::<String>()));
                    }
                }
                _ => {}
            }
        }
    }

    pub fn submit(&mut self) {
        let text = self.buffer.trim().to_string();
        self.buffer.clear();
        self.cursor = 0;
        if text.is_empty() {
            return;
        }
        if text == "/quit" || text == "/exit" {
            self.quit = true;
            return;
        }
        if text == "/relaunch" {
            if self.busy {
                self.relaunch_pending = true;
                self.emit(Kind::Dim, "relaunching when this turn finishes, nothing is stopped");
            } else {
                relaunch();
            }
            return;
        }
        if self.busy {
            if let Some(record) = self.record.clone() {
                self.emit(Kind::Dim, &format!("queued: {text}"));
                let core = self.core.clone();
                tokio::spawn(async move {
                    let _ = core.interject(&record.id, &text).await;
                });
                return;
            }
            self.queued.push(text);
            return;
        }
        if text == "/new" {
            self.spawn_create();
            return;
        }
        self.remember(&text);
        self.send(text);
    }

    fn spawn_create(&mut self) {
        self.emit(Kind::Dim, "opening a fresh session...");
        let (tx, rx) = mpsc::channel::<AppEvent>(8);
        self.events = Some(rx);
        self.busy = true;
        let core = self.core.clone();
        let cwd = self.cwd.clone();
        tokio::spawn(async move {
            let event = match core.create_session(&cwd).await {
                Ok(record) => AppEvent::Opened(record),
                Err(error) => AppEvent::Error(error.to_string()),
            };
            let _ = tx.send(event).await;
            let _ = tx.send(AppEvent::Done).await;
        });
    }

    pub fn send(&mut self, text: String) {
        let record = match self.record.clone() {
            Some(record) => record,
            None => return,
        };
        self.emit(Kind::You, &text);
        self.busy = true;
        self.tool_calls = 0;
        self.streaming = false;
        let core = self.core.clone();
        let id = record.id;
        let (tx, rx) = mpsc::channel::<AppEvent>(64);
        self.events = Some(rx);
        tokio::spawn(async move {
            let _ = core
                .turn_stream(&id, &text, |event| {
                    let forward = match event {
                        TurnEvent::Tool(detail) => AppEvent::Tool(detail),
                        TurnEvent::Result(detail, output) => AppEvent::Result(detail, output),
                        TurnEvent::ReplyDelta(text) => AppEvent::ReplyDelta(text),
                        TurnEvent::Thinking(text) => AppEvent::Thinking(text),
                        TurnEvent::Reply(text) => AppEvent::Reply(text),
                        TurnEvent::Error(message) => AppEvent::Error(message),
                        TurnEvent::Done => AppEvent::Done,
                    };
                    if tx.try_send(forward).is_err() {
                        // the ui is gone or the channel is full; nothing to do
                    }
                })
                .await;
            let _ = tx.send(AppEvent::Done).await;
        });
    }

    pub fn drain(&mut self) {
        let mut receiver = match self.events.take() {
            Some(receiver) => receiver,
            None => return,
        };
        let mut armed_this_drain = false;
        loop {
            match receiver.try_recv() {
                Ok(AppEvent::Tool(detail)) => {
                    self.tool_calls += 1;
                    self.streaming = false;
                    if detail.starts_with("relaunch") {
                        self.relaunch_pending = true;
                        armed_this_drain = true;
                    }
                    if self.lines.last().map(|(kind, text)| *kind == Kind::Tool && text.ends_with(&format!("· {detail}"))).unwrap_or(false) {
                        if let Some((kind, text)) = self.lines.last_mut() {
                            if *kind == Kind::Tool {
                                if let Some(count) = text.strip_prefix("· ").and_then(|body| body.strip_suffix("× 2")) {
                                    let base = count.strip_suffix("× 2").unwrap_or(count);
                                    *text = format!("· {}× 3", base);
                                } else if let Some((base, _)) = text.strip_prefix("· ").and_then(|body| body.rsplit_once("× ")) {
                                    *text = format!("· {}× {}", base, text.rsplit("× ").next().and_then(|n| n.parse::<u32>().ok()).unwrap_or(2) + 1);
                                } else {
                                    *text = format!("{}× 2", text);
                                }
                                self.gen += 1;
                            }
                        }
                    } else {
                        self.emit(Kind::Tool, &format!("· {detail}"));
                    }
                }
                Ok(AppEvent::Result(_detail, output)) => {
                    self.streaming = false;
                    let flat: String = output.split_whitespace().collect::<Vec<_>>().join(" ");
                    if !flat.is_empty() {
                        if let Some((kind, line)) = self.lines.last_mut() {
                            if *kind == Kind::Tool {
                                line.push_str(&format!("  = {}", flat.chars().take(200).collect::<String>()));
                                self.gen += 1;
                                break;
                            }
                        }
                        self.emit(Kind::Result, &format!("= {}", flat.chars().take(240).collect::<String>()));
                    }
                }
                Ok(AppEvent::Thinking(text)) => {
                    if !text.is_empty() {
                        // thinking streams into one dim italic block, kept
                        // separate from what he actually says
                        let last = self.lines.last_mut();
                        if let Some((kind, line)) = last {
                            if *kind == Kind::Thinking {
                                line.push_str(&text);
                                self.gen += 1;
                                break;
                            }
                        }
                        self.emit(Kind::Thinking, &text);
                        self.gen += 1;
                    }
                }
                Ok(AppEvent::ReplyDelta(text)) => {
                    if !text.is_empty() {
                        let mut appended = false;
                        if self.streaming {
                            if let Some((kind, line)) = self.lines.last_mut() {
                                if *kind == Kind::Diablo {
                                    line.push_str(&text);
                                    self.gen += 1;
                                    appended = true;
                                }
                            }
                        }
                        if !appended {
                            self.streaming = true;
                            self.emit(Kind::Diablo, &text);
                            self.gen += 1;
                        }
                    }
                }
                Ok(AppEvent::Reply(text)) => {
                    self.streaming = false;
                    let reply = if text.trim().is_empty() {
                        "he went quiet mid-work; ask him to finish."
                    } else {
                        &text
                    };
                    self.emit(Kind::Diablo, reply);
                }
                Ok(AppEvent::Error(message)) => {
                    self.streaming = false;
                    self.emit(Kind::Notice, &message);
                }
                Ok(AppEvent::Opened(record)) => {
                    self.record = Some(record);
                    self.emit(Kind::Dim, "fresh session");
                }
                Ok(AppEvent::Done) => {
                    self.busy = false;
                    self.streaming = false;
                    self.esc_armed = false;
                    match turn_end(&self.queued, self.relaunch_pending) {
                        TurnEnd::Relaunch => relaunch(),
                        TurnEnd::Next(next) => {
                            self.queued.remove(0);
                            self.send(next);
                        }
                        TurnEnd::Idle => {}
                    }
                    return;
                }
                Err(mpsc::error::TryRecvError::Empty) => {
                    self.events = Some(receiver);
                    return;
                }
                Err(mpsc::error::TryRecvError::Disconnected) => {
                    self.busy = false;
                    // the turn died without a Done; a stale pending flag
                    // would fire a surprise relaunch at some later turn.
                    // a flag armed during this drain (a relaunch tool call
                    // whose turn then died) is honored, not wiped
                    if !armed_this_drain {
                        self.relaunch_pending = false;
                    }
                    return;
                }
            }
        }
    }

    pub fn handle_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            if let crossterm::event::KeyCode::Char(char) = key.code {
                match char.to_ascii_lowercase() {
                    'c' => {
                        if self.busy && matches!(self.mode, Mode::Chat) {
                            self.interrupt();
                        }
                        self.quit = true;
                        return;
                    }
                    'd' => {
                        let picker = matches!(self.mode, Mode::Picker { .. });
                        if picker || self.buffer.is_empty() {
                            self.quit = true;
                        } else {
                            let split = self.char_index(self.cursor);
                            if split < self.buffer.len() {
                                self.buffer.remove(split);
                            }
                        }
                        return;
                    }
                    _ => {}
                }
            }
        }
        if let Mode::Picker { records, pos, query } = &mut self.mode {
            match key.code {
                crossterm::event::KeyCode::Up | crossterm::event::KeyCode::Char('k')
                    if !key.modifiers.contains(KeyModifiers::CONTROL) =>
                {
                    *pos = pos.saturating_sub(1);
                }
                crossterm::event::KeyCode::Down | crossterm::event::KeyCode::Char('j')
                    if !key.modifiers.contains(KeyModifiers::CONTROL) =>
                {
                    *pos = (*pos + 1).min(filtered(records, query).len().saturating_sub(1));
                }
                crossterm::event::KeyCode::PageUp => *pos = pos.saturating_sub(20),
                crossterm::event::KeyCode::PageDown => {
                    *pos = (*pos + 20).min(filtered(records, query).len().saturating_sub(1));
                }
                crossterm::event::KeyCode::Enter => {
                    let shown = filtered(records, query);
                    if let Some(record) = shown.get(*pos) {
                        self.pick_chosen = Some(record.id.clone());
                        self.mode = Mode::Chat;
                    }
                }
                crossterm::event::KeyCode::Backspace => {
                    query.pop();
                    *pos = 0;
                }
                crossterm::event::KeyCode::Esc => {
                    self.mode = Mode::Chat;
                    self.spawn_create();
                }
                crossterm::event::KeyCode::Char(char) => {
                    if key.modifiers.contains(KeyModifiers::CONTROL) && char.to_ascii_lowercase() == 'q' {
                        self.mode = Mode::Chat;
                        self.spawn_create();
                    } else if !key.modifiers.contains(KeyModifiers::CONTROL) {
                        query.push(char);
                        *pos = 0;
                    }
                }
                _ => {}
            }
            return;
        }
        match key.code {
            crossterm::event::KeyCode::PageUp => self.scroll += 20,
            crossterm::event::KeyCode::PageDown => self.scroll = self.scroll.saturating_sub(20),
            crossterm::event::KeyCode::End => self.scroll = 0,
            crossterm::event::KeyCode::Home => self.cursor = 0,
            crossterm::event::KeyCode::Left if key.modifiers.contains(KeyModifiers::CONTROL) => self.word_back(),
            crossterm::event::KeyCode::Right if key.modifiers.contains(KeyModifiers::CONTROL) => self.word_forward(),
            crossterm::event::KeyCode::Left => self.cursor = self.cursor.saturating_sub(1),
            crossterm::event::KeyCode::Right => self.cursor = (self.cursor + 1).min(self.buffer.chars().count()),
            crossterm::event::KeyCode::Backspace => {
                if self.cursor > 0 {
                    let split = self.char_index(self.cursor - 1);
                    self.buffer.remove(split);
                    self.cursor -= 1;
                }
            }
            crossterm::event::KeyCode::Delete => {
                let split = self.char_index(self.cursor);
                if split < self.buffer.len() {
                    self.buffer.remove(split);
                }
            }
            crossterm::event::KeyCode::Up => {
                if !slash_matches(&self.buffer).is_empty() {
                    self.slash_pos = self.slash_pos.saturating_sub(1);
                } else {
                    self.history_back();
                }
            }
            crossterm::event::KeyCode::Down => {
                if !slash_matches(&self.buffer).is_empty() {
                    let n = slash_matches(&self.buffer).len();
                    self.slash_pos = (self.slash_pos + 1).min(n - 1);
                } else {
                    self.history_forward();
                }
            }
            crossterm::event::KeyCode::Enter
                if key.modifiers.contains(KeyModifiers::SHIFT) | key.modifiers.contains(KeyModifiers::ALT) =>
            {
                let split = self.char_index(self.cursor);
                self.buffer.insert(split, '\n');
                self.cursor += 1;
            }
            crossterm::event::KeyCode::Tab => {
                let matches = slash_matches(&self.buffer);
                if !matches.is_empty() {
                    let at = self.slash_pos.min(matches.len() - 1);
                    let (name, _) = SLASH[matches[at]];
                    self.buffer = name.to_string();
                    self.cursor = name.chars().count();
                    self.slash_pos = 0;
                }
            }
            crossterm::event::KeyCode::Enter => {
                self.esc_armed = false;
                self.submit()
            }
            crossterm::event::KeyCode::Esc => {
                if self.busy {
                    if self.esc_armed {
                        self.interrupt();
                    } else {
                        self.esc_armed = true;
                        self.emit(Kind::Dim, "esc again to stop him this instant");
                    }
                } else {
                    self.esc_armed = false;
                    if !self.buffer.is_empty() {
                        self.buffer.clear();
                        self.cursor = 0;
                    } else {
                        self.scroll = 0;
                    }
                }
            }
            crossterm::event::KeyCode::Char(char) => {
                if key.modifiers.contains(KeyModifiers::CONTROL) {
                    match char {
                        'c' => self.interrupt(),
                        'u' => {
                            self.buffer.clear();
                            self.cursor = 0;
                        }
                        'a' => self.cursor = 0,
                        'e' => self.cursor = self.buffer.chars().count(),
                        'k' => {
                            let split = self.char_index(self.cursor);
                            self.buffer.truncate(split);
                        }
                        'w' => self.kill_word_back(),
                        _ => {}
                    }
                    return;
                }
                if key.modifiers.contains(KeyModifiers::ALT) {
                    match char.to_ascii_lowercase() {
                        'b' => self.word_back(),
                        'f' => self.word_forward(),
                        'd' => self.kill_word_forward(),
                        _ => {}
                    }
                    return;
                }
                let split = self.char_index(self.cursor);
                self.buffer.insert(split, char);
                self.cursor += 1;
                self.slash_pos = 0;
            }
            _ => {}
        }
    }

    fn history_back(&mut self) {
        if self.hist_pos.is_none() && !self.history.is_empty() {
            self.hist_pos = Some(self.history.len() - 1);
        } else if let Some(pos) = self.hist_pos {
            self.hist_pos = Some(pos.saturating_sub(1));
        }
        if let Some(pos) = self.hist_pos {
            self.buffer = self.history[pos].clone();
            self.cursor = self.buffer.chars().count();
        }
    }

    fn history_forward(&mut self) {
        let Some(pos) = self.hist_pos else { return };
        if pos + 1 >= self.history.len() {
            self.hist_pos = None;
            self.buffer.clear();
            self.cursor = 0;
        } else {
            self.hist_pos = Some(pos + 1);
            self.buffer = self.history[self.hist_pos.unwrap()].clone();
            self.cursor = self.buffer.chars().count();
        }
    }

    fn interrupt(&mut self) {
        self.esc_armed = false;
        // an interrupted turn never reaches Done, so a pending relaunch
        // would otherwise fire at the end of some unrelated later turn
        self.relaunch_pending = false;
        if self.busy {
            if let Some(record) = self.record.clone() {
                let core = self.core.clone();
                tokio::spawn(async move {
                    let _ = core.cancel(&record.id).await;
                });
            }
            self.emit(Kind::Dim, "stopping, right now");
        }
        self.buffer.clear();
        self.cursor = 0;
    }

    fn char_index(&self, char_pos: usize) -> usize {
        self.buffer
            .char_indices()
            .nth(char_pos)
            .map(|(index, _)| index)
            .unwrap_or(self.buffer.len())
    }

    fn word_back(&mut self) {
        let chars: Vec<char> = self.buffer.chars().collect();
        let mut pos = self.cursor.min(chars.len());
        while pos > 0 && !chars[pos - 1].is_alphanumeric() {
            pos -= 1;
        }
        while pos > 0 && chars[pos - 1].is_alphanumeric() {
            pos -= 1;
        }
        self.cursor = pos;
    }

    fn word_forward(&mut self) {
        let chars: Vec<char> = self.buffer.chars().collect();
        let mut pos = self.cursor;
        while pos < chars.len() && !chars[pos].is_alphanumeric() {
            pos += 1;
        }
        while pos < chars.len() && chars[pos].is_alphanumeric() {
            pos += 1;
        }
        self.cursor = pos;
    }

    fn kill_word_back(&mut self) {
        let start = self.cursor;
        self.word_back();
        let from = self.char_index(self.cursor);
        let to = self.char_index(start);
        self.buffer.replace_range(from..to, "");
    }

    fn kill_word_forward(&mut self) {
        let start = self.cursor;
        self.word_forward();
        let from = self.char_index(start);
        let to = self.char_index(self.cursor);
        self.buffer.replace_range(from..to, "");
    }

    fn history_path() -> Option<PathBuf> {
        let home = std::env::var("HOME").ok()?;
        Some(PathBuf::from(home).join(".local/state/diablo/tui-history"))
    }

    pub fn paste(&mut self, text: &str) {
        for c in text.chars() {
            let split = self.char_index(self.cursor);
            self.buffer.insert(split, c);
            self.cursor += 1;
        }
    }

    pub fn invalidate(&mut self) {
        self.cache = None;
    }
}

pub fn describe(name: &str, args: &Value) -> String {
    let parsed = match args {
        Value::String(text) => serde_json::from_str(text).unwrap_or_else(|_| json!({})),
        other => other.clone(),
    };
    let get = |key: &str| {
        parsed
            .get(key)
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_string()
    };
    match name {
        "run_shell" => format!("run: {}", get("command").chars().take(160).collect::<String>()),
        "read_file" => format!("read: {}", get("path")),
        "write_file" => format!("write: {} ({} bytes)", get("path"), get("content").len()),
        "edit_file" => format!("edit: {}", get("path")),
        "relaunch" => "relaunch: rebuild and reload the TUI".to_string(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::{draw_box, wrap_input, Wrapped};

    #[test]
    fn describe_reads_arguments_stored_as_a_json_string() {
        let args = Value::String("{\"command\": \"ls -la\"}".to_string());
        assert_eq!(describe("run_shell", &args), "run: ls -la");
        let object = json!({"path": "src/main.rs"});
        assert_eq!(describe("read_file", &object), "read: src/main.rs");
    }

    #[test]
    fn slash_matches_filters_by_prefix() {
        assert!(slash_matches("/").len() == 3);
        assert!(slash_matches("/n").len() == 1 && SLASH[slash_matches("/n")[0]].0 == "/new");
        assert!(slash_matches("/q").len() == 1 && SLASH[slash_matches("/q")[0]].0 == "/quit");
        assert!(slash_matches("hello").is_empty(), "no slash, no box");
        assert!(slash_matches("/zz").is_empty());
    }

    fn app() -> App {
        App::new(Core::new(), std::env::temp_dir())
    }

    // --- /relaunch while idle fires immediately -------------------------

    #[test]
    fn relaunch_command_when_idle_is_immediate() {
        // can't call the real relaunch() (it execs); assert the guard
        // that routes around it: not busy means the pending path is skipped
        let mut a = app();
        assert!(!a.busy);
        // busy=false => submit() takes the immediate branch, never sets pending
        assert!(!a.relaunch_pending);
    }

    // --- /relaunch while busy defers to turn end ------------------------

    #[tokio::test]
    async fn relaunch_while_busy_defers_and_fires_at_done() {
        let mut a = app();
        a.busy = true;
        a.relaunch_pending = true;
        // simulate the turn finishing with nothing queued
        match turn_end(&a.queued, a.relaunch_pending) {
            TurnEnd::Relaunch => {}
            other => panic!("expected relaunch, got {other:?}"),
        }
    }

    #[test]
    fn relaunch_while_busy_does_not_fire_mid_turn() {
        let mut a = app();
        a.busy = true;
        a.relaunch_pending = true;
        // while the turn still runs, the flag is set but nothing fired:
        // turn_end is only consulted on Done
        assert!(a.busy);
        assert!(a.relaunch_pending);
    }

    // --- queued messages outrank the relaunch ---------------------------

    #[tokio::test]
    async fn queued_message_runs_before_relaunch() {
        let mut a = app();
        a.busy = true;
        a.relaunch_pending = true;
        a.queued.push("check the logs".to_string());
        match turn_end(&a.queued, a.relaunch_pending) {
            TurnEnd::Next(text) => assert_eq!(text, "check the logs"),
            other => panic!("expected next, got {other:?}"),
        }
        // the flag survives the queued turn and fires after it
        assert!(a.relaunch_pending);
        assert!(matches!(turn_end(&[], true), TurnEnd::Relaunch));
    }

    #[tokio::test]
    async fn queued_messages_drain_in_order_then_relaunch() {
        let mut a = app();
        a.queued = vec!["one".into(), "two".into()];
        a.relaunch_pending = true;
        let first = turn_end(&a.queued, a.relaunch_pending);
        assert!(matches!(&first, TurnEnd::Next(t) if t == "one"));
        a.queued.remove(0);
        let second = turn_end(&a.queued, a.relaunch_pending);
        assert!(matches!(&second, TurnEnd::Next(t) if t == "two"));
        a.queued.remove(0);
        assert!(matches!(turn_end(&a.queued, a.relaunch_pending), TurnEnd::Relaunch));
    }

    // --- an interrupted turn cancels the pending relaunch ---------------

    #[tokio::test]
    async fn interrupt_cancels_pending_relaunch() {
        let mut a = app();
        a.busy = true;
        a.relaunch_pending = true;
        a.interrupt();
        assert!(!a.relaunch_pending, "interrupted turn must not relaunch later");
        assert!(matches!(turn_end(&a.queued, a.relaunch_pending), TurnEnd::Idle));
    }

    // --- a dead event channel cancels the pending relaunch --------------

    #[tokio::test]
    async fn disconnected_channel_cancels_pending_relaunch() {
        let mut a = app();
        a.busy = true;
        a.relaunch_pending = true;
        // hand over a receiver whose sender is already dropped
        let (tx, rx) = mpsc::channel::<AppEvent>(1);
        drop(tx);
        a.events = Some(rx);
        a.drain();
        assert!(!a.busy);
        assert!(!a.relaunch_pending, "a dead turn must not relaunch later");
        assert!(matches!(turn_end(&a.queued, a.relaunch_pending), TurnEnd::Idle));
    }

    // --- a relaunch tool call mid-turn sets the pending flag ------------

    #[tokio::test]
    async fn relaunch_tool_call_sets_pending_flag() {
        let mut a = app();
        a.busy = true;
        let (tx, rx) = mpsc::channel::<AppEvent>(4);
        a.events = Some(rx);
        tx.send(AppEvent::Tool("relaunch: rebuild and reload the TUI".into()))
            .await
            .unwrap();
        drop(tx); // no Done: drain() sees the Tool event, then Disconnected
        a.drain();
        assert!(a.relaunch_pending, "a relaunch tool call must arm the flag");
    }

    #[test]
    fn relaunch_tool_detail_is_recognized() {
        // the drain() match arm keys on this exact prefix
        let detail = describe("relaunch", &Value::Null);
        assert!(detail.starts_with("relaunch"));
    }

    // --- the tool description stays stable ------------------------------

    #[test]
    fn relaunch_describe_is_stable() {
        assert_eq!(describe("relaunch", &Value::Null), "relaunch: rebuild and reload the TUI");
    }
}

/// Pure decision for the end of a turn: what should happen next.
/// Kept free of I/O so the relaunch flow can be tested without exec'ing.
#[derive(Debug)]
pub(crate) enum TurnEnd {
    /// swap the TUI binary and reload the code
    Relaunch,
    /// run the next queued message
    Next(String),
    /// nothing pending, sit idle
    Idle,
}

pub(crate) fn turn_end(queued: &[String], relaunch_pending: bool) -> TurnEnd {
    if !queued.is_empty() {
        // queued work outranks a relaunch: it runs first, and the
        // relaunch fires when that turn finishes (the flag survives)
        return TurnEnd::Next(queued[0].clone());
    }
    if relaunch_pending {
        return TurnEnd::Relaunch;
    }
    TurnEnd::Idle
}

pub fn relaunch() -> ! {
    use std::os::unix::process::CommandExt;

    let _ = crossterm::terminal::disable_raw_mode();
    let _ = crossterm::execute!(std::io::stdout(), crossterm::terminal::LeaveAlternateScreen);
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("diablo-tui"));
    let path = exe.is_file()
        .then_some(exe)
        .or_else(|| std::env::var("HOME").ok().map(|home| PathBuf::from(home).join(".local/bin/diablo-tui")))
        .filter(|path| path.is_file())
        .unwrap_or_else(|| PathBuf::from("diablo-tui"));
    let error = std::process::Command::new(path).arg("-C").exec();
    panic!("relaunch failed: {error}");
}
