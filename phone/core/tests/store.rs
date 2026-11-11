use diablo_phone::{CoreError, Host, Message, MessageState, Section, Store};
use std::sync::Arc;

fn open() -> (tempfile::TempDir, Arc<Store>) {
    let dir = tempfile::tempdir().unwrap();
    let store = Store::open(dir.path().join("phone.db").to_string_lossy().into_owned()).unwrap();
    (dir, store)
}

fn section(id: &str, updated: f64) -> Section {
    Section {
        id: id.into(),
        name: id.to_uppercase(),
        icon: "star".into(),
        accent: "#112233".into(),
        brief: String::new(),
        position: 9,
        hidden: false,
        deleted: false,
        updated,
    }
}

fn remote(rid: i64, text: &str, ts: f64) -> Message {
    Message {
        local_id: String::new(),
        remote_id: Some(rid),
        section: "diablo".into(),
        role: "assistant".into(),
        text: text.into(),
        ts,
        state: MessageState::Sent,
        meta: None,
    }
}

fn ids(s: &[Section]) -> Vec<&str> {
    s.iter().map(|x| x.id.as_str()).collect()
}

#[test]
fn seeds_on_first_open() {
    let (_d, s) = open();
    let secs = s.sections();
    assert_eq!(ids(&secs), vec!["diablo", "workout", "projects"]);
    assert_eq!(secs[0].name, "Diablo");
    assert_eq!(secs[0].icon, "flame");
    assert_eq!(secs[0].accent, "#2d81ff");
    assert_eq!(secs[0].brief, "");
    assert_eq!(secs[1].icon, "dumbbell");
    assert_eq!(secs[1].accent, "#3fd6b8");
    assert_eq!(
        secs[1].brief,
        "Training log: sets, reps, weights and how it felt. Suggest the next session from the history."
    );
    assert_eq!(secs[2].icon, "folder");
    assert_eq!(secs[2].accent, "#f2a516");
    assert_eq!(
        secs[2].brief,
        "Code and projects across the machines. Hand real work to T3 Code threads."
    );
    let hosts = s.hosts();
    assert_eq!(hosts.len(), 2);
    assert_eq!(hosts[0].name, "diablo-pi");
    assert_eq!(hosts[0].url, diablo_phone::PRIMARY_HUB);
    assert_eq!(hosts[0].priority, 0);
    assert_eq!(hosts[1].name, "nemurenai");
    assert_eq!(hosts[1].url, diablo_phone::STANDBY_HUB);
    assert_eq!(hosts[1].priority, 1);
    assert!(s.dirty_sections().is_empty());
}

#[test]
fn reopening_does_not_reseed_or_duplicate() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("a.db").to_string_lossy().into_owned();
    {
        let s = Store::open(path.clone()).unwrap();
        s.remove_host("diablo-pi".into());
        s.remove_host("nemurenai".into());
        s.remove_section("workout".into()).unwrap();
    }
    let s = Store::open(path).unwrap();
    assert_eq!(ids(&s.sections()), vec!["diablo", "projects"]);
    assert!(s.hosts().is_empty());
    assert_eq!(s.all_sections().len(), 3);
}

#[test]
fn a_store_seeded_before_the_standby_gains_nemurenai_once() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("a.db").to_string_lossy().into_owned();
    {
        let s = Store::open(path.clone()).unwrap();
        s.remove_host("nemurenai".into());
        s.set("_hosts_seeded".into(), "1".into());
    }
    {
        let s = Store::open(path.clone()).unwrap();
        let names: Vec<_> = s.hosts().into_iter().map(|h| h.name).collect();
        assert_eq!(names, vec!["diablo-pi", "nemurenai"]);
        s.remove_host("nemurenai".into());
    }
    let s = Store::open(path).unwrap();
    let names: Vec<_> = s.hosts().into_iter().map(|h| h.name).collect();
    assert_eq!(names, vec!["diablo-pi"]);
}

#[test]
fn creates_missing_parent_directory() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("deep/er/phone.db");
    Store::open(path.to_string_lossy().into_owned()).unwrap();
    assert!(path.exists());
}

#[test]
fn save_section_validates() {
    let (_d, s) = open();
    for bad in ["", "UPPER", "has space", "under_score", &"a".repeat(33), "é"] {
        let r = s.save_section(section(bad, 0.0));
        assert!(matches!(r, Err(CoreError::Invalid { .. })), "{bad}");
    }
    for bad in ["112233", "#12345", "#1234567", "#gg0000", "red", ""] {
        let mut sec = section("ok", 0.0);
        sec.accent = bad.into();
        assert!(matches!(s.save_section(sec), Err(CoreError::Invalid { .. })), "{bad}");
    }
    let mut dead = section("diablo", 0.0);
    dead.deleted = true;
    assert!(matches!(s.save_section(dead), Err(CoreError::Invalid { .. })));
    assert!(s.save_section(section(&"a".repeat(32), 0.0)).is_ok());
    assert!(s.save_section(section("a-1", 0.0)).is_ok());
}

#[test]
fn save_section_inserts_updates_and_stamps_time() {
    let (_d, s) = open();
    let saved = s.save_section(section("sleep", 0.0)).unwrap();
    assert!(saved.updated > 1.0e9);
    assert_eq!(ids(&s.sections()), vec!["diablo", "workout", "projects", "sleep"]);
    let mut again = saved.clone();
    again.name = "Sleep".into();
    again.hidden = true;
    again.position = 0;
    let saved2 = s.save_section(again).unwrap();
    assert!(saved2.updated >= saved.updated);
    let all = s.sections();
    let sleep = all.iter().find(|x| x.id == "sleep").unwrap();
    assert_eq!(sleep.name, "Sleep");
    assert!(sleep.hidden);
    assert_eq!(all.iter().filter(|x| x.id == "sleep").count(), 1);
    assert_eq!(all[0].name, "Diablo");
    assert_eq!(all[1].id, "sleep");
}

#[test]
fn ordering_is_position_then_name() {
    let (_d, s) = open();
    let mut a = section("zeta", 0.0);
    a.position = 1;
    a.name = "Aaa".into();
    let mut b = section("alpha", 0.0);
    b.position = 1;
    b.name = "Zzz".into();
    s.save_section(a).unwrap();
    s.save_section(b).unwrap();
    assert_eq!(ids(&s.sections()), vec!["diablo", "zeta", "workout", "alpha", "projects"]);
}

#[test]
fn remove_leaves_tombstone() {
    let (_d, s) = open();
    s.remove_section("workout".into()).unwrap();
    assert_eq!(ids(&s.sections()), vec!["diablo", "projects"]);
    let all = s.all_sections();
    assert_eq!(all.len(), 3);
    let w = all.iter().find(|x| x.id == "workout").unwrap();
    assert!(w.deleted);
    assert!(w.updated > 1.0e9);
    assert!(s.dirty_sections().iter().any(|x| x.id == "workout" && x.deleted));
}

#[test]
fn diablo_cannot_be_removed() {
    let (_d, s) = open();
    assert!(matches!(s.remove_section("diablo".into()), Err(CoreError::Invalid { .. })));
    assert_eq!(s.sections().len(), 3);
}

#[test]
fn reorder_sets_positions_and_dirties_changed_only() {
    let (_d, s) = open();
    s.reorder(vec!["projects".into(), "diablo".into(), "workout".into()]).unwrap();
    assert_eq!(ids(&s.sections()), vec!["projects", "diablo", "workout"]);
    let positions: Vec<_> = s.sections().iter().map(|x| x.position).collect();
    assert_eq!(positions, vec![0, 1, 2]);
    let dirty = s.dirty_sections();
    assert_eq!(dirty.len(), 3);
    s.mark_sections_synced(dirty);
    s.reorder(vec!["projects".into(), "diablo".into(), "workout".into()]).unwrap();
    assert!(s.dirty_sections().is_empty());
    s.reorder(vec!["projects".into(), "workout".into(), "diablo".into()]).unwrap();
    let d = s.dirty_sections();
    assert_eq!(ids(&d).len(), 2);
}

#[test]
fn reorder_rejects_duplicates_and_skips_unknown() {
    let (_d, s) = open();
    assert!(s.reorder(vec!["diablo".into(), "diablo".into()]).is_err());
    s.reorder(vec!["ghost".into(), "workout".into(), "diablo".into()]).unwrap();
    assert_eq!(s.sections()[0].id, "workout");
}

#[test]
fn merge_remote_newer_wins_and_is_acknowledged() {
    let (_d, s) = open();
    let local = s.save_section(section("sleep", 0.0)).unwrap();
    let mut r = section("sleep", local.updated + 100.0);
    r.name = "From hub".into();
    let out = s.merge_sections(vec![r]);
    let sleep = out.iter().find(|x| x.id == "sleep").unwrap();
    assert_eq!(sleep.name, "From hub");
    assert_eq!(sleep.updated, local.updated + 100.0);
    assert!(s.dirty_sections().is_empty());
}

#[test]
fn merge_local_newer_wins_and_stays_dirty() {
    let (_d, s) = open();
    let local = s.save_section(section("sleep", 0.0)).unwrap();
    let mut r = section("sleep", local.updated - 100.0);
    r.name = "Stale".into();
    let out = s.merge_sections(vec![r]);
    assert_eq!(out.iter().find(|x| x.id == "sleep").unwrap().name, "SLEEP");
    assert_eq!(ids(&s.dirty_sections()), vec!["sleep"]);
}

#[test]
fn merge_inserts_unknown_remote_sections_and_tombstones() {
    let (_d, s) = open();
    let mut gone = section("workout", 5000.0);
    gone.deleted = true;
    let out = s.merge_sections(vec![section("new", 4000.0), gone]);
    assert_eq!(ids(&out), vec!["diablo", "projects", "new"]);
    assert!(s.all_sections().iter().any(|x| x.id == "workout" && x.deleted));
    assert!(s.dirty_sections().is_empty());
}

#[test]
fn merge_ignores_invalid_ids_and_protects_diablo() {
    let (_d, s) = open();
    let mut d = section("diablo", 9000.0);
    d.deleted = true;
    let out = s.merge_sections(vec![section("BAD ID", 1.0), d]);
    assert!(out.iter().any(|x| x.id == "diablo" && !x.deleted));
    assert!(!s.all_sections().iter().any(|x| x.id == "BAD ID"));
}

#[test]
fn merge_equal_timestamps_keep_local() {
    let (_d, s) = open();
    let local = s.save_section(section("sleep", 0.0)).unwrap();
    let mut r = section("sleep", local.updated);
    r.name = "Other".into();
    let out = s.merge_sections(vec![r]);
    assert_eq!(out.iter().find(|x| x.id == "sleep").unwrap().name, "SLEEP");
    assert!(s.dirty_sections().is_empty());
}

#[test]
fn both_directions_round_trip() {
    let (_d, s) = open();
    let a = s.save_section(section("aa", 0.0)).unwrap();
    let b = s.save_section(section("bb", 0.0)).unwrap();
    let mut ra = section("aa", a.updated + 10.0);
    ra.name = "Hub A".into();
    let mut rb = section("bb", b.updated - 10.0);
    rb.name = "Hub B".into();
    let merged = s.merge_sections(vec![ra, rb]);
    assert_eq!(merged.iter().find(|x| x.id == "aa").unwrap().name, "Hub A");
    assert_eq!(merged.iter().find(|x| x.id == "bb").unwrap().name, "BB");
    let dirty = s.dirty_sections();
    assert_eq!(ids(&dirty), vec!["bb"]);
    s.mark_sections_synced(dirty);
    assert!(s.dirty_sections().is_empty());
}

#[test]
fn edit_after_snapshot_stays_dirty() {
    let (_d, s) = open();
    s.save_section(section("sleep", 0.0)).unwrap();
    let snapshot = s.dirty_sections();
    std::thread::sleep(std::time::Duration::from_millis(5));
    let mut e = snapshot[0].clone();
    e.name = "Edited".into();
    s.save_section(e).unwrap();
    s.mark_sections_synced(snapshot);
    assert_eq!(s.dirty_sections().len(), 1);
}

#[test]
fn resaving_a_synced_section_makes_it_dirty_again() {
    let (_d, s) = open();
    let mut r = section("sleep", 100.0);
    r.name = "Hub".into();
    s.merge_sections(vec![r]);
    assert!(s.dirty_sections().is_empty());
    let mut edit = s.sections().into_iter().find(|x| x.id == "sleep").unwrap();
    edit.name = "Mine".into();
    s.save_section(edit).unwrap();
    assert_eq!(ids(&s.dirty_sections()), vec!["sleep"]);
}

#[test]
fn outgoing_message_lifecycle() {
    let (_d, s) = open();
    let m = s.add_outgoing("diablo".into(), "hello".into());
    assert_eq!(m.role, "user");
    assert_eq!(m.state, MessageState::Pending);
    assert_eq!(m.remote_id, None);
    assert_eq!(m.local_id.len(), 36);
    assert_eq!(s.outbox(), vec![m.clone()]);
    assert_eq!(s.messages("diablo".into(), 10), vec![m.clone()]);

    s.mark_failed(m.local_id.clone());
    let ob = s.outbox();
    assert_eq!(ob.len(), 1);
    assert_eq!(ob[0].state, MessageState::Failed);

    s.mark_sent(m.local_id.clone(), 42);
    assert!(s.outbox().is_empty());
    let got = s.messages("diablo".into(), 10);
    assert_eq!(got[0].state, MessageState::Sent);
    assert_eq!(got[0].remote_id, Some(42));
    assert_eq!(s.last_remote_id("diablo".into()), 42);
}

#[test]
fn outbox_is_oldest_first() {
    let (_d, s) = open();
    let a = s.add_outgoing("diablo".into(), "1".into());
    let b = s.add_outgoing("workout".into(), "2".into());
    let c = s.add_outgoing("diablo".into(), "3".into());
    s.mark_failed(b.local_id.clone());
    let ob: Vec<_> = s.outbox().into_iter().map(|m| m.text).collect();
    assert_eq!(ob, vec!["1", "2", "3"]);
    s.mark_sent(a.local_id, 1);
    s.mark_sent(c.local_id, 2);
    assert_eq!(s.outbox().len(), 1);
}

#[test]
fn merge_remote_does_not_duplicate_after_mark_sent() {
    let (_d, s) = open();
    let m = s.add_outgoing("diablo".into(), "hi".into());
    s.mark_sent(m.local_id.clone(), 7);
    let mut echo = remote(7, "hi", m.ts + 1.0);
    echo.role = "user".into();
    assert_eq!(s.merge_remote("diablo".into(), vec![echo]), 0);
    let got = s.messages("diablo".into(), 10);
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].local_id, m.local_id);
}

#[test]
fn mark_sent_after_merge_remote_keeps_one_copy() {
    let (_d, s) = open();
    let m = s.add_outgoing("diablo".into(), "hi".into());
    let mut echo = remote(7, "hi", m.ts + 1.0);
    echo.role = "user".into();
    assert_eq!(s.merge_remote("diablo".into(), vec![echo]), 1);
    assert_eq!(s.messages("diablo".into(), 10).len(), 2);
    s.mark_sent(m.local_id.clone(), 7);
    let got = s.messages("diablo".into(), 10);
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].local_id, m.local_id);
    assert_eq!(got[0].remote_id, Some(7));
    assert!(s.outbox().is_empty());
}

#[test]
fn merge_remote_counts_new_and_never_duplicates() {
    let (_d, s) = open();
    let batch = vec![remote(1, "a", 1.0), remote(2, "b", 2.0), remote(3, "c", 3.0)];
    assert_eq!(s.merge_remote("diablo".into(), batch.clone()), 3);
    assert_eq!(s.merge_remote("diablo".into(), batch), 0);
    assert_eq!(s.messages("diablo".into(), 100).len(), 3);
    assert_eq!(s.merge_remote("diablo".into(), vec![remote(3, "c2", 3.0), remote(4, "d", 4.0)]), 1);
    let got = s.messages("diablo".into(), 100);
    assert_eq!(got.len(), 4);
    assert_eq!(got[2].text, "c2");
    assert_eq!(s.last_remote_id("diablo".into()), 4);
}

#[test]
fn merge_remote_ignores_messages_without_remote_id() {
    let (_d, s) = open();
    let mut m = remote(1, "x", 1.0);
    m.remote_id = None;
    assert_eq!(s.merge_remote("diablo".into(), vec![m]), 0);
    assert!(s.messages("diablo".into(), 10).is_empty());
}

#[test]
fn merge_remote_with_supplied_local_id_and_collisions() {
    let (_d, s) = open();
    let mut a = remote(1, "a", 1.0);
    a.local_id = "r1".into();
    let mut b = remote(2, "b", 2.0);
    b.local_id = "r1".into();
    assert_eq!(s.merge_remote("diablo".into(), vec![a, b]), 2);
    let got = s.messages("diablo".into(), 10);
    assert_eq!(got.len(), 2);
    assert_ne!(got[0].local_id, got[1].local_id);
}

#[test]
fn messages_returns_newest_limit_oldest_first_per_section() {
    let (_d, s) = open();
    let batch: Vec<_> = (1..=10).map(|i| remote(i, &format!("m{i}"), i as f64)).collect();
    s.merge_remote("diablo".into(), batch);
    let mut other = remote(100, "w", 1.0);
    other.section = "workout".into();
    s.merge_remote("workout".into(), vec![other]);
    let got: Vec<_> = s.messages("diablo".into(), 3).into_iter().map(|m| m.text).collect();
    assert_eq!(got, vec!["m8", "m9", "m10"]);
    assert_eq!(s.messages("workout".into(), 3).len(), 1);
    assert_eq!(s.messages("diablo".into(), 0).len(), 0);
    assert_eq!(s.last_remote_id("workout".into()), 100);
    assert_eq!(s.last_remote_id("nothing".into()), 0);
}

#[test]
fn meta_round_trips() {
    let (_d, s) = open();
    let mut m = remote(1, "x", 1.0);
    m.meta = Some("{\"kind\":\"readout\"}".into());
    s.merge_remote("diablo".into(), vec![m]);
    assert_eq!(s.messages("diablo".into(), 1)[0].meta.as_deref(), Some("{\"kind\":\"readout\"}"));
}

fn host(name: &str, priority: i32) -> Host {
    Host {
        name: name.into(),
        url: format!("http://{name}:8755"),
        priority,
        last_ok: None,
        last_ms: None,
        failures: 0,
    }
}

#[test]
fn hosts_crud_and_validation() {
    let (_d, s) = open();
    s.save_host(host("banditbox", 1)).unwrap();
    s.save_host(host("nemurenai", 2)).unwrap();
    let names: Vec<_> = s.hosts().into_iter().map(|h| h.name).collect();
    assert_eq!(names, vec!["diablo-pi", "banditbox", "nemurenai"]);
    let mut moved = host("nemurenai", -1);
    moved.url = "https://n.example".into();
    s.save_host(moved).unwrap();
    let hs = s.hosts();
    assert_eq!(hs.len(), 3);
    assert_eq!(hs[0].name, "nemurenai");
    assert_eq!(hs[0].url, "https://n.example");
    s.remove_host("nemurenai".into());
    assert_eq!(s.hosts().len(), 2);
    assert!(matches!(s.save_host(host("", 0)), Err(CoreError::Invalid { .. })));
    let mut bad = host("x", 0);
    bad.url = "ftp://x".into();
    assert!(matches!(s.save_host(bad), Err(CoreError::Invalid { .. })));
}

#[test]
fn record_probe_updates_and_reorders() {
    let (_d, s) = open();
    s.remove_host("nemurenai".into());
    s.save_host(host("banditbox", 1)).unwrap();
    for _ in 0..3 {
        s.record_probe("diablo-pi".into(), false, 0);
    }
    let hs = s.hosts();
    assert_eq!(hs[0].name, "banditbox");
    assert_eq!(hs[1].name, "diablo-pi");
    assert_eq!(hs[1].failures, 3);
    s.record_probe("diablo-pi".into(), true, 120);
    let hs = s.hosts();
    assert_eq!(hs[0].name, "diablo-pi");
    assert_eq!(hs[0].failures, 0);
    assert_eq!(hs[0].last_ms, Some(120));
    assert!(hs[0].last_ok.unwrap() > 1.0e9);
    s.record_probe("ghost".into(), true, 1);
    assert_eq!(s.hosts().len(), 2);
}

#[test]
fn kv_get_set() {
    let (_d, s) = open();
    assert_eq!(s.get("k".into()), None);
    s.set("k".into(), "v".into());
    s.set("k".into(), "w".into());
    assert_eq!(s.get("k".into()), Some("w".into()));
}

#[test]
fn data_survives_reopen() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("p.db").to_string_lossy().into_owned();
    {
        let s = Store::open(path.clone()).unwrap();
        s.add_outgoing("diablo".into(), "persist".into());
        s.set("a".into(), "b".into());
        s.save_section(section("keep", 0.0)).unwrap();
    }
    let s = Store::open(path).unwrap();
    assert_eq!(s.outbox().len(), 1);
    assert_eq!(s.get("a".into()), Some("b".into()));
    assert!(s.sections().iter().any(|x| x.id == "keep"));
    assert_eq!(s.dirty_sections().len(), 1);
}

#[test]
fn concurrent_use_is_safe() {
    let (_d, s) = open();
    let handles: Vec<_> = (0..8)
        .map(|t| {
            let s = s.clone();
            std::thread::spawn(move || {
                for i in 0..25 {
                    s.add_outgoing("diablo".into(), format!("{t}-{i}"));
                    let _ = s.sections();
                }
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    assert_eq!(s.outbox().len(), 200);
}

#[test]
fn bad_path_is_a_db_error() {
    let dir = tempfile::tempdir().unwrap();
    let r = Store::open(dir.path().to_string_lossy().into_owned());
    assert!(matches!(r, Err(CoreError::Db { .. })));
}

#[test]
fn retry_puts_a_failed_message_back_in_the_queue() {
    let (_dir, s) = open();
    let m = s.add_outgoing("workout".into(), "bench 80 kg".into());
    s.mark_failed(m.local_id.clone());
    assert_eq!(s.outbox()[0].state, MessageState::Failed);
    s.retry(m.local_id.clone());
    assert_eq!(s.outbox()[0].state, MessageState::Pending);
    s.mark_sent(m.local_id.clone(), 7);
    s.retry(m.local_id.clone());
    assert!(s.outbox().is_empty());
}

#[test]
fn names_and_briefs_follow_the_hubs_limits() {
    let (_dir, s) = open();
    let mut long = section("gym", 0.0);
    long.name = "x".repeat(41);
    assert!(matches!(s.save_section(long), Err(CoreError::Invalid { .. })));
    let mut empty = section("gym", 0.0);
    empty.name = "  ".into();
    assert!(matches!(s.save_section(empty), Err(CoreError::Invalid { .. })));
    let mut brief = section("gym", 0.0);
    brief.brief = "b".repeat(2001);
    assert!(matches!(s.save_section(brief), Err(CoreError::Invalid { .. })));
    let mut ok = section("gym", 0.0);
    ok.name = "x".repeat(40);
    assert!(s.save_section(ok).is_ok());
}
