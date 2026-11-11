"""Reminders that speak: set an hour, hear the word when it comes.

  python -m remind add 14:30 "the tea is ready"
  python -m remind add +20m "check the oven"
  python -m remind add 2026-09-14 09:00 "standup"
  python -m remind list
  python -m remind remove <id>
  python -m remind run            scheduler loop, run by diablo-remind.service
"""

import contextlib
import fcntl
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

STATE_DIR = Path.home() / ".local/state/diablo"
VOICE_API = "http://127.0.0.1:8757/v1/announce"
UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}
CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2}):(\d{2})(?::(\d{2}))?$")
DELTA_RE = re.compile(r"^(\+?(?:\d+\s*[smhd]\s*)+)$")
DELTA_PART_RE = re.compile(r"(\d+)\s*([smhd])")
MAX_ATTEMPTS = 120
POLL_S = 1.0

log = logging.getLogger("diablo.remind")


def store_path():
    return STATE_DIR / "reminders.json"


def parse_when(text, now=None):
    """Turn "14:30", "2026-09-14 09:00" or "+20m" into an epoch second."""
    now = time.time() if now is None else now
    text = text.strip().lower()
    delta = DELTA_RE.match(text)
    if delta:
        seconds = sum(int(n) * UNIT[u] for n, u in DELTA_PART_RE.findall(delta.group(1)))
        if seconds:
            return now + seconds
    m = DATETIME_RE.match(text)
    if m:
        y, mo, d, h, mi, s = (int(p or 0) for p in m.groups())
        _check_ymd(y, mo, d)
        _check_hms(h, mi, s)
        return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    m = CLOCK_RE.match(text)
    if m:
        h, mi, s = (int(p or 0) for p in m.groups())
        _check_hms(h, mi, s)
        lt = time.localtime(now)
        target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, s, 0, 0, -1))
        if target <= now:
            lt = time.localtime(now + 86400)
            target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, s, 0, 0, -1))
        return target
    raise ValueError(f"cannot read a time from {text!r}")


def _check_hms(h, mi, s):
    if not (0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59):
        raise ValueError(f"impossible time of day: {h:02d}:{mi:02d}:{s:02d}")


def _check_ymd(y, mo, d):
    if not (1 <= mo <= 12 and 1 <= d <= 31 and 2000 <= y <= 3000):
        raise ValueError(f"impossible date: {y:04d}-{mo:02d}-{d:02d}")


@contextlib.contextmanager
def _locked(path):
    lock = Path(str(path) + ".lock")
    with open(lock, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load(path=None):
    path = Path(path) if path else store_path()
    try:
        items = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [r for r in items if isinstance(r, dict)]


def save(items, path=None):
    path = Path(path) if path else store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(items, key=lambda r: (r.get("when", 0), r.get("created", 0)))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".reminders", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(items, handle, indent=1)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def add(when, message, path=None):
    path = Path(path) if path else store_path()
    reminder = {
        "id": secrets.token_hex(4),
        "when": float(when),
        "message": str(message).strip()[:300],
        "created": time.time(),
        "status": "pending",
        "attempts": 0,
    }
    with _locked(path):
        items = load(path)
        items.append(reminder)
        save(items, path)
    return reminder


def pending(path=None):
    rows = [r for r in load(path) if r.get("status") == "pending"]
    return sorted(rows, key=lambda r: (r.get("when", 0), r.get("created", 0)))


def cancel(rid, path=None):
    """Remove the one pending reminder whose id starts with rid."""
    path = Path(path) if path else store_path()
    with _locked(path):
        items = load(path)
        hits = [
            r for r in items
            if r.get("status") == "pending" and str(r.get("id", "")).startswith(rid)
        ]
        if len(hits) != 1:
            return False
        save([r for r in items if r is not hits[0]], path)
        return True


def mark_fired(rid, status="fired", path=None):
    path = Path(path) if path else store_path()
    with _locked(path):
        items = load(path)
        for r in items:
            if r.get("id") == rid:
                r["status"] = status
                r["fired_at"] = time.time()
        save(items, path)


def bump_attempts(rid, path=None):
    path = Path(path) if path else store_path()
    with _locked(path):
        items = load(path)
        for r in items:
            if r.get("id") == rid:
                r["attempts"] = int(r.get("attempts", 0)) + 1
        save(items, path)


def due(now=None, path=None):
    now = time.time() if now is None else now
    return [r for r in pending(path) if r.get("when", 0) <= now]


def announce_voice(text, api=VOICE_API, timeout=5):
    """Hand the line to the voice service; its queue keeps speech from overlapping."""
    try:
        req = urllib.request.Request(
            api,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except OSError:
        return False


def tick(now=None, path=None, announce=None):
    """Fire every due reminder once; speak returns True when it was handed off."""
    announce = announce or (lambda text: announce_voice(text))
    now = time.time() if now is None else now
    served = 0
    for r in due(now, path):
        if int(r.get("attempts", 0)) >= MAX_ATTEMPTS:
            mark_fired(r["id"], status="missed", path=path)
            log.warning("reminder missed, voice unreachable too long: %s", r["message"])
            continue
        if announce(f"Sir, a reminder: {r['message']}"):
            mark_fired(r["id"], path=path)
            served += 1
        else:
            bump_attempts(r["id"], path=path)
    return served


def prune(path=None, keep_days=7):
    """Drop finished reminders older than keep_days so the file stays small."""
    path = Path(path) if path else store_path()
    horizon = time.time() - keep_days * 86400
    with _locked(path):
        items = load(path)
        kept = [
            r for r in items
            if r.get("status") == "pending" or r.get("fired_at", 0) >= horizon
        ]
        if len(kept) != len(items):
            save(kept, path)


def run(poll=POLL_S, path=None):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    log.info("reminder scheduler watching %s", store_path() if path is None else path)
    last_prune = 0.0
    while True:
        try:
            tick(path=path)
            if time.time() - last_prune > 3600:
                prune(path=path)
                last_prune = time.time()
        except Exception:
            log.exception("reminder tick failed")
        time.sleep(poll)


def fmt_when(ts):
    return time.strftime("%a %d %b %H:%M", time.localtime(ts))


def cmd_add(args):
    if len(args) < 2:
        print(__doc__.strip())
        return 1
    when = " ".join(args[:-1])
    message = " ".join(args[1:])
    reminder = add(parse_when(when), message)
    print(f"reminder set for {fmt_when(reminder['when'])}: {reminder['message']} ({reminder['id']})")
    return 0


def cmd_list(args):
    rows = pending()
    if not rows:
        print("no reminders pending.")
        return 0
    for r in rows:
        late = " (late)" if r["when"] <= time.time() else ""
        print(f"{fmt_when(r['when'])}{late}  {r['message']}  ({r['id']})")
    return 0


def cmd_remove(args):
    if not args:
        print("which id? see: python -m remind list")
        return 1
    if cancel(args[0]):
        print("gone, my lord. it will not speak.")
        return 0
    print("no single pending reminder matches that id.")
    return 1


def main(args=None):
    args = sys.argv[1:] if args is None else args
    cmds = {"add": cmd_add, "list": cmd_list, "remove": cmd_remove, "rm": cmd_remove, "run": run}
    if not args or args[0] not in cmds:
        print(__doc__.strip())
        return 1
    result = cmds[args[0]](args[1:])
    return 0 if result is None else result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
        sys.exit(130)
