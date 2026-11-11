use diablo_tui::app::{App, Kind, Mode};
use std::path::Path;

fn app() -> App {
    App::new(diablo_tui::api::Core::new(), Path::new("/tmp").to_path_buf())
}

fn key(code: crossterm::event::KeyCode, mods: crossterm::event::KeyModifiers) -> crossterm::event::KeyEvent {
    crossterm::event::KeyEvent::new(code, mods)
}

fn session(id: &str, title: &str, cwd: &str) -> diablo_tui::api::SessionInfo {
    diablo_tui::api::SessionInfo {
        id: id.to_string(),
        title: title.to_string(),
        cwd: cwd.to_string(),
        ago: "now".to_string(),
    }
}

#[test]
fn reply_deltas_grow_one_block_and_tools_close_it() {
    let mut app = app();
    let (tx, rx) = tokio::sync::mpsc::channel(16);
    app.events = Some(rx);
    tx.blocking_send(diablo_tui::app::AppEvent::ReplyDelta("first ".into())).unwrap();
    tx.blocking_send(diablo_tui::app::AppEvent::ReplyDelta("second".into())).unwrap();
    tx.blocking_send(diablo_tui::app::AppEvent::Done).unwrap();
    app.drain();
    let diablo_lines: Vec<&String> = app
        .lines
        .iter()
        .filter(|(kind, _)| *kind == Kind::Diablo)
        .map(|(_, text)| text)
        .collect();
    assert_eq!(diablo_lines, vec!["first second"], "deltas merge into one block");
    assert!(!app.streaming);

    let (tx, rx) = tokio::sync::mpsc::channel(16);
    app.events = Some(rx);
    tx.blocking_send(diablo_tui::app::AppEvent::Tool("run: ls".into())).unwrap();
    tx.blocking_send(diablo_tui::app::AppEvent::ReplyDelta("fresh".into())).unwrap();
    tx.blocking_send(diablo_tui::app::AppEvent::Done).unwrap();
    app.drain();
    let diablo_lines: Vec<&String> = app
        .lines
        .iter()
        .filter(|(kind, _)| *kind == Kind::Diablo)
        .map(|(_, text)| text)
        .collect();
    assert_eq!(diablo_lines, vec!["first second", "fresh"], "a tool call starts a new block");
}

#[test]
fn word_motion_and_kills() {
    let mut app = app();
    app.buffer = "one two three".to_string();
    app.cursor = 13;
    app.handle_key(key(crossterm::event::KeyCode::Left, crossterm::event::KeyModifiers::CONTROL));
    assert_eq!(app.cursor, 8, "ctrl-left lands on the word start");
    app.handle_key(key(crossterm::event::KeyCode::Right, crossterm::event::KeyModifiers::CONTROL));
    assert_eq!(app.cursor, 13, "ctrl-right lands past the last word");

    app.cursor = 7;
    app.handle_key(key(crossterm::event::KeyCode::Char('k'), crossterm::event::KeyModifiers::CONTROL));
    assert_eq!(app.buffer, "one two", "ctrl-k kills to the end");

    app.cursor = 7;
    app.handle_key(key(crossterm::event::KeyCode::Char('w'), crossterm::event::KeyModifiers::CONTROL));
    assert_eq!(app.buffer, "one ", "ctrl-w kills the word behind the cursor");

    app.buffer = "alpha beta".to_string();
    app.cursor = 5;
    app.handle_key(key(crossterm::event::KeyCode::Char('d'), crossterm::event::KeyModifiers::ALT));
    assert_eq!(app.buffer, "alpha", "alt-d kills the word ahead of the cursor");
}

#[test]
fn picker_query_filters_and_enter_picks_from_the_filtered_list() {
    let mut app = app();
    app.mode = Mode::Picker {
        records: vec![session("a", "cpu upgrade", "/home"), session("b", "voice loop", "/srv")],
        pos: 0,
        query: String::new(),
    };
    for char in "voice".chars() {
        app.handle_key(key(crossterm::event::KeyCode::Char(char), crossterm::event::KeyModifiers::NONE));
    }
    match &app.mode {
        Mode::Picker { query, pos, .. } => {
            assert_eq!(query, "voice");
            assert_eq!(*pos, 0);
        }
        _ => panic!("still in the picker"),
    }
    app.handle_key(key(crossterm::event::KeyCode::Enter, crossterm::event::KeyModifiers::NONE));
    assert_eq!(app.pick_chosen.as_deref(), Some("b"), "enter picks the filtered record");
}

#[test]
fn picker_backspace_undoes_the_filter() {
    let mut app = app();
    app.mode = Mode::Picker {
        records: vec![session("a", "cpu upgrade", "/home")],
        pos: 0,
        query: "cpu".to_string(),
    };
    app.handle_key(key(crossterm::event::KeyCode::Backspace, crossterm::event::KeyModifiers::NONE));
    match &app.mode {
        Mode::Picker { query, .. } => assert_eq!(query, "cp"),
        _ => panic!("still in the picker"),
    }
}
