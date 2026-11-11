use diablo_core::sessions::{self, Record, Store};
use serde_json::json;
use tempfile::tempdir;

fn record_with(id: &str, cwd: &str, updated: &str) -> Record {
    Record {
        id: id.to_string(),
        title: "new session".to_string(),
        cwd: cwd.to_string(),
        created: updated.to_string(),
        updated: updated.to_string(),
        messages: vec![json!({"role": "user", "content": "hello"})],
    }
}

#[test]
fn list_is_sorted_newest_first() {
    let dir = tempdir().unwrap();
    let store = Store::with_dir(dir.path().to_path_buf());
    store.save(&record_with("a", "/one", "2026-01-01T00:00:00+00:00")).unwrap();
    store.save(&record_with("b", "/two", "2026-01-02T00:00:00+00:00")).unwrap();
    let ids: Vec<String> = store.list().into_iter().map(|r| r.id).collect();
    assert_eq!(ids, vec!["b", "a"]);
}

#[test]
fn last_for_matches_the_directory() {
    let dir = tempdir().unwrap();
    let store = Store::with_dir(dir.path().to_path_buf());
    store.save(&record_with("a", "/one", "2026-01-01T00:00:00+00:00")).unwrap();
    store.save(&record_with("b", "/two", "2026-01-02T00:00:00+00:00")).unwrap();
    assert_eq!(store.last_for("/one").unwrap().id, "a");
    assert!(store.last_for("/nowhere").is_none());
}

#[test]
fn broken_files_are_skipped() {
    let dir = tempdir().unwrap();
    std::fs::write(dir.path().join("junk.json"), "{not json").unwrap();
    let store = Store::with_dir(dir.path().to_path_buf());
    assert!(store.list().is_empty());
}

#[test]
fn settle_title_takes_the_first_user_line() {
    let mut record = sessions::new_record(std::path::Path::new("/tmp/one"), "system");
    record.messages.push(json!({"role": "user", "content": "fix the   failing tests\nand run them"}));
    sessions::settle_title(&mut record);
    assert_eq!(record.title, "fix the failing tests and run them");
}

#[test]
fn settle_title_keeps_a_chosen_title() {
    let mut record = sessions::new_record(std::path::Path::new("/tmp/one"), "system");
    record.title = "the ledger work".to_string();
    sessions::settle_title(&mut record);
    assert_eq!(record.title, "the ledger work");
}

#[test]
fn timeago_buckets() {
    let now = chrono::Local::now().to_rfc3339();
    assert_eq!(sessions::timeago(&now), "now");
    let minutes = (chrono::Local::now() - chrono::Duration::minutes(5)).to_rfc3339();
    assert_eq!(sessions::timeago(&minutes), "5m");
    let hours = (chrono::Local::now() - chrono::Duration::hours(3)).to_rfc3339();
    assert_eq!(sessions::timeago(&hours), "3h");
    let days = (chrono::Local::now() - chrono::Duration::days(2)).to_rfc3339();
    assert_eq!(sessions::timeago(&days), "2d");
}

#[test]
fn brain_resolution_mirrors_the_python_rules() {
    use diablo_core::brains::{resolve, Brain};
    use std::collections::HashMap;

    let mut settings = HashMap::new();
    settings.insert("llm_base_url".to_string(), "http://default.test/v1/".to_string());
    settings.insert("llm_api_key".to_string(), "default-key".to_string());
    settings.insert("llm_model".to_string(), "default-model".to_string());
    settings.insert("rank.baron".to_string(), "fireworks|fw-model".to_string());
    settings.insert("provider.fireworks.base_url".to_string(), "http://fireworks.test/v1".to_string());
    settings.insert("provider.fireworks.api_key".to_string(), "fw-key".to_string());

    assert_eq!(
        resolve(&settings, "baron").unwrap(),
        Brain { base: "http://fireworks.test/v1".into(), key: "fw-key".into(), model: "fw-model".into() }
    );
    assert_eq!(
        resolve(&settings, "fireworks|any-model").unwrap(),
        Brain { base: "http://fireworks.test/v1".into(), key: "fw-key".into(), model: "any-model".into() }
    );
    assert_eq!(
        resolve(&settings, "plain-model").unwrap(),
        Brain { base: "http://default.test/v1".into(), key: "default-key".into(), model: "plain-model".into() }
    );
    assert_eq!(
        resolve(&settings, "").unwrap(),
        Brain { base: "http://default.test/v1".into(), key: "default-key".into(), model: "default-model".into() }
    );
    assert!(resolve(&settings, "ghost|any-model").is_err());
    assert!(resolve(&HashMap::new(), "plain-model").is_err());
}

#[test]
fn tool_loop_refuses_destructive_commands() {
    let runtime = tokio::runtime::Runtime::new().unwrap();
    let (refusal, unknown) = runtime.block_on(async {
        let refusal = diablo_core::tools::run(
            "run_shell", &json!({"command": "sudo rm -rf /"}), std::path::Path::new("/"),
        ).await;
        let unknown = diablo_core::tools::run("conjure", &json!({}), std::path::Path::new("/")).await;
        (refusal, unknown)
    });
    assert_eq!(refusal, "refused: destructive command");
    assert_eq!(unknown, "unknown tool: conjure");
}

#[test]
fn write_read_edit_roundtrip() {
    let dir = tempdir().unwrap();
    let target = dir.path().join("note.txt");
    let runtime = tokio::runtime::Runtime::new().unwrap();
    let cwd = dir.path();
    let out = runtime.block_on(async {
        let written = diablo_core::tools::run("write_file", &json!({"path": "note.txt", "content": "hello demon"}), cwd).await;
        let read = diablo_core::tools::run("read_file", &json!({"path": "note.txt"}), cwd).await;
        let edited = diablo_core::tools::run("edit_file", &json!({"path": "note.txt", "old": "hello", "new": "goodbye"}), cwd).await;
        let missed = diablo_core::tools::run("edit_file", &json!({"path": "note.txt", "old": "missing", "new": "x"}), cwd).await;
        let shown = diablo_core::tools::run("read_file", &json!({"path": "note.txt"}), cwd).await;
        (written, read, edited, missed, shown)
    });
    assert!(out.0.starts_with("wrote "));
    assert!(out.1.contains("hello demon"));
    assert_eq!(out.2, "edited");
    assert!(out.3.contains("not in the file"));
    assert!(out.4.contains("goodbye demon"));
    assert!(target.exists());
}

#[test]
fn relative_paths_resolve_against_the_session_cwd() {
    let dir = tempdir().unwrap();
    let runtime = tokio::runtime::Runtime::new().unwrap();
    let out = runtime.block_on(diablo_core::tools::run(
        "write_file",
        &json!({"path": "nested/thing.txt", "content": "in place"}),
        dir.path(),
    ));
    assert!(out.starts_with("wrote "));
    assert!(dir.path().join("nested/thing.txt").exists());
}
