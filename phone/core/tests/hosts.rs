use diablo_phone::{choose_order, Host};

fn host(name: &str, priority: i32, ok: Option<f64>, ms: Option<u32>, failures: u32) -> Host {
    Host {
        name: name.into(),
        url: format!("http://{name}:1"),
        priority,
        last_ok: ok,
        last_ms: ms,
        failures,
    }
}

fn names(h: &[Host]) -> Vec<&str> {
    h.iter().map(|x| x.name.as_str()).collect()
}

#[test]
fn priority_then_latency() {
    let out = choose_order(
        vec![
            host("c", 1, None, Some(10), 0),
            host("b", 0, None, Some(90), 0),
            host("a", 0, None, Some(20), 0),
            host("d", 1, None, None, 0),
        ],
        1000.0,
    );
    assert_eq!(names(&out), vec!["a", "b", "c", "d"]);
}

#[test]
fn repeated_failures_with_old_success_go_last() {
    let now = 10_000.0;
    let out = choose_order(
        vec![
            host("dead", 0, Some(now - 601.0), Some(1), 3),
            host("never", 0, None, None, 5),
            host("ok", 5, Some(now), Some(500), 0),
        ],
        now,
    );
    assert_eq!(names(&out), vec!["ok", "dead", "never"]);
}

#[test]
fn recent_success_keeps_a_failing_host_in_place() {
    let now = 10_000.0;
    let out = choose_order(
        vec![host("other", 1, None, Some(1), 0), host("flaky", 0, Some(now - 599.0), Some(50), 4)],
        now,
    );
    assert_eq!(names(&out), vec!["flaky", "other"]);
}

#[test]
fn fewer_than_three_failures_do_not_demote() {
    let out = choose_order(
        vec![host("b", 1, None, None, 0), host("a", 0, None, None, 2)],
        0.0,
    );
    assert_eq!(names(&out), vec!["a", "b"]);
}

#[test]
fn ties_break_by_name_regardless_of_input_order() {
    let a = vec![host("z", 0, None, Some(5), 0), host("m", 0, None, Some(5), 0), host("a", 0, None, Some(5), 0)];
    let mut b = a.clone();
    b.reverse();
    assert_eq!(choose_order(a, 1.0), choose_order(b, 1.0));
}

#[test]
fn empty_and_single() {
    assert!(choose_order(vec![], 0.0).is_empty());
    assert_eq!(choose_order(vec![host("x", 0, None, None, 9)], 0.0).len(), 1);
}
