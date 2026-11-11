use anyhow::{bail, Result};
use diablo_tui::{api, app::{App, Mode}};
use crossterm::event::Event;
use crossterm::terminal::{enable_raw_mode, EnterAlternateScreen};
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;
use std::path::PathBuf;

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let core = api::Core::new();
    // -R: full restart including the core (only for explicit /relaunch-core
    // style use). The relaunch tool uses -C: swap the TUI, keep the core
    // running, so a relaunch can never kill the turn that triggered it.
    if args.iter().any(|arg| arg == "-R") {
        core.restart_core().await?;
    }
    core.wait_or_spawn().await?;
    let cwd = std::env::current_dir()?.to_string_lossy().to_string();

    let start = match args.first().map(|arg| arg.as_str()) {
        Some("-r") => Start::Picker,
        Some("-c") | Some("-R") | Some("-C") => {
            let record = match core.list_sessions().await {
                Ok(mut sessions) => {
                    // a relaunch can come from a different directory; fall
                    // back to the newest session instead of dying
                    let here = sessions.iter().position(|s| s.cwd == cwd);
                    let index = here.unwrap_or(0);
                    if sessions.is_empty() { None } else { Some(sessions.remove(index)) }
                }
                Err(_) => None,
            };
            match record {
                Some(record) => Start::Resume(record),
                None => Start::Fresh,
            }
        }
        _ => Start::Fresh,
    };
    run(core, start).await
}

enum Start {
    Fresh,
    Resume(api::SessionInfo),
    Picker,
}

async fn run(core: api::Core, start: Start) -> Result<()> {
    enable_raw_mode()?;
    let mut stdout = std::io::stdout();
    crossterm::execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let cwd: PathBuf = std::env::current_dir()?;
    let mut app = App::new(core.clone(), cwd.clone());
    match start {
        Start::Picker => {
            let records = core.list_sessions().await?;
            app.mode = if records.is_empty() {
                Mode::Chat
            } else {
                Mode::Picker { records, pos: 0, query: String::new() }
            };
        }
        Start::Resume(record) => {
            match core.load_session(&record.id).await {
                Ok(record_json) if record_json.get("error").is_none() => {
                    app.start_resumed(record, &record_json);
                }
                _ => {
                    // session vanished between list and load; go fresh
                    let record = core.create_session(&cwd).await?;
                    app.start_fresh(record);
                }
            }
        }
        Start::Fresh => {
            let record = core.create_session(&cwd).await?;
            app.start_fresh(record);
        }
    }

    let mut events = ();
    loop {
        app.drain();
        terminal.draw(|frame| app.draw(frame))?;
        if crossterm::event::poll(std::time::Duration::from_millis(50))? {
            let event = crossterm::event::read()?;
            match event {
                Event::Key(key) => {
                    if key.kind == crossterm::event::KeyEventKind::Press {
                        app.handle_key(key);
                    }
                }
                Event::Paste(text) => app.paste(&text),
                Event::Resize(_, _) => app.invalidate(),
                _ => {}
            }
        }
        if let Some(pick) = app.pick_chosen.take() {
            let record_json = core.load_session(&pick).await?;
            if record_json.get("error").is_some() {
                app.emit_notice("that session is gone, fresh one instead");
                app.mode = Mode::Chat;
                let record = core.create_session(&cwd).await?;
                app.start_fresh(record);
            } else {
                let info = core
                    .list_sessions()
                    .await?
                    .into_iter()
                    .find(|session| session.id == pick);
                app.mode = Mode::Chat;
                if let Some(info) = info {
                    app.start_resumed(info, &record_json);
                } else {
                    app.start_fresh(core.create_session(&cwd).await?);
                }
            }
        }
        if app.quit {
            break;
        }
    }

    if app.busy {
        if let Some(record) = app.record.clone() {
            let _ = tokio::time::timeout(
                std::time::Duration::from_secs(2),
                core.cancel(&record.id),
            )
            .await;
        }
    }
    let _ = crossterm::terminal::disable_raw_mode();
    let _ = crossterm::execute!(std::io::stdout(), crossterm::terminal::LeaveAlternateScreen);
    Ok(())
}
