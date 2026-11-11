use diablo_core::{config, serve, sessions, turn};
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(|s| s.as_str()) {
        Some("serve") => tokio::runtime::Runtime::new().unwrap().block_on(serve_main(&args[1..])),
        Some("run") => tokio::runtime::Runtime::new().unwrap().block_on(run_main(&args[1..])),
        Some("sessions") if args.get(1).map(|s| s.as_str()) == Some("list") => sessions_list(),
        _ => {
            eprintln!("diablo core: the brain, database and agent loop in rust");
            eprintln!("  diablo-core serve [--port N]    the local api on 127.0.0.1, default port 8766");
            eprintln!("  diablo-core run \"task\" [--cwd path] [--brain rank]  one headless turn in a fresh session");
            eprintln!("  diablo-core sessions list       every stored session, newest first");
            std::process::exit(1);
        }
    };
    if let Err(error) = result {
        eprintln!("failed: {error}");
        std::process::exit(1);
    }
}

fn cwd_from(flag: Option<String>) -> PathBuf {
    flag.map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

async fn run_main(args: &[String]) -> anyhow::Result<()> {
    let mut text = String::new();
    let mut cwd = None;
    let mut brain = String::new();
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--cwd" => cwd = iter.next().cloned(),
            "--brain" => brain = iter.next().cloned().unwrap_or_default(),
            other => {
                if text.is_empty() {
                    text = other.to_string();
                }
            }
        }
    }
    if text.is_empty() {
        anyhow::bail!("give the task as the first argument");
    }
    let cwd = cwd_from(cwd);
    let local = config::load()?;
    let settings = config::settings(&local).await?;
    let brain_wanted = if brain.is_empty() {
        settings.get("llm_model").cloned().unwrap_or_default()
    } else {
        brain
    };
    let mut record = sessions::new_record(&cwd, &turn::system_prompt(&cwd));
    let runner = turn::Runner::new(&cwd);
    let mut session = record.messages.clone();
    let mut sink = |event: turn::StepEvent| {
        println!("{} {}", event.kind, event.detail);
        if !event.output.is_empty() {
            println!("  = {}", event.output.replace('\n', " "));
        }
    };
    let reply = runner
        .run_turn(
            &settings,
            &brain_wanted,
            &mut session,
            &text,
            &std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
            &mut sink,
        )
        .await?;
    turn::Runner::trim(&mut session);
    record.messages = session;
    sessions::settle_title(&mut record);
    record.updated = chrono::Local::now().to_rfc3339();
    sessions::Store::new().save(&record)?;
    println!();
    println!("{reply}");
    Ok(())
}

async fn serve_main(args: &[String]) -> anyhow::Result<()> {
    let mut port: u16 = 8766;
    let mut cwd = None;
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--port" => port = iter.next().and_then(|v| v.parse().ok()).unwrap_or(port),
            "--cwd" => cwd = iter.next().cloned(),
            other => anyhow::bail!("unknown argument: {other}"),
        }
    }
    let cwd = cwd_from(cwd);
    serve::serve(port, cwd).await
}

fn sessions_list() -> anyhow::Result<()> {
    for record in sessions::Store::new().list() {
        println!(
            "{}  {:>4}  {:38}  {}",
            record.id,
            sessions::timeago(&record.updated),
            record.title.chars().take(38).collect::<String>(),
            record.cwd,
        );
    }
    Ok(())
}
