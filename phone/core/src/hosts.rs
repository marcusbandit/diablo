use std::cmp::Ordering;

const STALE_SECS: f64 = 600.0;
const MAX_FAILURES: u32 = 3;

#[derive(uniffi::Record, Clone, Debug, PartialEq)]
pub struct Host {
    pub name: String,
    pub url: String,
    pub priority: i32,
    pub last_ok: Option<f64>,
    pub last_ms: Option<u32>,
    pub failures: u32,
}

fn given_up(host: &Host, now: f64) -> bool {
    host.failures >= MAX_FAILURES
        && match host.last_ok {
            Some(ok) => now - ok > STALE_SECS,
            None => true,
        }
}

fn compare(a: &Host, b: &Host, now: f64) -> Ordering {
    given_up(a, now)
        .cmp(&given_up(b, now))
        .then(a.priority.cmp(&b.priority))
        .then(a.last_ms.unwrap_or(u32::MAX).cmp(&b.last_ms.unwrap_or(u32::MAX)))
        .then_with(|| a.name.cmp(&b.name))
}

/// Orders hosts from best to worst candidate. A host that failed at least
/// 3 times in a row and whose last success is older than 10 minutes (or
/// that never succeeded) goes after all others. Within each group, lower
/// `priority` comes first, then lower `last_ms` (unknown latency counts as
/// slowest), then `name`. `now` and `last_ok` are seconds since the epoch.
/// The result does not depend on the input order.
#[uniffi::export]
pub fn choose_order(hosts: Vec<Host>, now: f64) -> Vec<Host> {
    let mut hosts = hosts;
    hosts.sort_by(|a, b| compare(a, b, now));
    hosts
}
