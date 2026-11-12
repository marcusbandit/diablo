import asyncio
import json
import logging
import os
import signal
import time

from . import brains, db, devices, settings, t3link

logger = logging.getLogger(__name__)

ALLOWED_KEY = "restart.allowed"
INTERVAL_KEY = "restart.min_interval_s"
LAST_KEY = "restart.last_at"
ANNOUNCE_KEY = "restart.announce"
DEFAULT_INTERVAL_S = 300
EARS_FRESH_S = 60
KNOWN_EARS_S = 600
SAFE_EARS = {"asleep", "untrained", "off"}
SETTLE_S = 5.0
WAIT_S = 180.0
POLL_S = 1.0
ANNOUNCE_DELAY_S = 15.0
ANNOUNCE_MAX_AGE_S = 600
OPEN_APPROVALS = ("pending", "held", "running")

_ears = {"state": None, "detail": "", "at": 0.0}
_elsewhere = {}
_scheduled = {"conv": None, "task": None}


def report_ears(state, detail="", host=""):
    """banditbox's ears fill the one slot the safety check has always read; ears on any other box
    get their own, which only counts while it is fresh, so a laptop that went to sleep never blocks."""
    entry = dict(state=str(state or "").strip().lower() or None, detail=str(detail or "")[:120], at=time.time())
    host = devices.clean_host(host)
    if host and host != devices.desk_host():
        if host not in _elsewhere and len(_elsewhere) >= 16:
            oldest = min(_elsewhere, key=lambda name: _elsewhere[name]["at"])
            _elsewhere.pop(oldest, None)
        _elsewhere[host] = entry
    else:
        _ears.update(entry)


def ears_elsewhere(now=None, busy=None):
    """{host: state} for ears on other boxes that reported lately and are not quiet. With busy, only
    those states count, the way the desk ears' own check reads them."""
    now = time.time() if now is None else now
    return {host: e["state"] for host, e in list(_elsewhere.items())
            if e["state"] and now - e["at"] <= EARS_FRESH_S
            and (e["state"] in busy if busy is not None else e["state"] not in SAFE_EARS)}


def ears_known(host, now=None):
    """Whether ears on that box have reported in the last ten minutes, so a turn may say it came from there."""
    entry = _elsewhere.get(devices.clean_host(host))
    now = time.time() if now is None else now
    return bool(entry) and entry["state"] != "off" and now - entry["at"] <= KNOWN_EARS_S


def ears_view():
    """What the ears last said and how old it is, for the safety check and the status line."""
    age = time.time() - _ears["at"] if _ears["at"] else None
    return {"state": _ears["state"], "age": age}


def truthy(value):
    return str(value or "").strip().lower() not in ("0", "off", "false", "no", "")


def allowed():
    return truthy(settings.get(ALLOWED_KEY) or "on")


def interval():
    try:
        return max(0.0, float(settings.get(INTERVAL_KEY) or DEFAULT_INTERVAL_S))
    except ValueError:
        return float(DEFAULT_INTERVAL_S)


def last_restart():
    try:
        return float(settings.get(LAST_KEY) or 0)
    except ValueError:
        return 0.0


def under_systemd():
    return bool(os.environ.get("INVOCATION_ID"))


def desktop_down():
    try:
        return not t3link.is_up("banditbox")
    except Exception:
        return False


def ears_reasons():
    return _desk_reasons() + [f"the ears on {host} are {state}, so he is speaking, being listened to or waiting for a reply"
                              for host, state in sorted(ears_elsewhere().items())]


def _desk_reasons():
    view = ears_view()
    state, age = view["state"], view["age"]
    if state is None or (state != "off" and age > EARS_FRESH_S):
        if desktop_down():
            return []
        if state is None:
            return ["the ears have not reported to the hub, so I cannot tell whether he is speaking"]
        return [f"the ears last reported {int(age)} s ago, so I cannot tell whether he is speaking"]
    if state in SAFE_EARS:
        return []
    return [f"the ears are {state}, so he is speaking, being listened to or waiting for a reply"]


def turn_reasons(conv):
    reasons = []
    for name, count in sorted(brains.inflight.items()):
        others = count - (1 if name == conv else 0)
        if others > 0:
            where = "this conversation has another message waiting" if name == conv else f"a turn is running in {name}"
            reasons.append(f"a reply is pending: {where}")
    return reasons


def approval_reasons():
    try:
        from . import mailbox

        marks = ",".join("?" for _ in OPEN_APPROVALS)
        rows = mailbox.query(f"SELECT id, status FROM approvals WHERE status IN ({marks})", OPEN_APPROVALS)
    except Exception as error:
        if "no such table" in str(error):
            return []
        logger.exception("could not read the approvals")
        return ["the approvals could not be read, so I cannot tell whether one waits for him"]
    if not rows:
        return []
    return [f"{len(rows)} approval{'s' if len(rows) != 1 else ''} (request {', '.join('#' + str(r['id']) for r in rows[:5])}) not settled yet"]


def watched():
    return [
        {"thread_id": row["thread_id"], "environment": row["env"], "title": row["title"] or "untitled",
         "status": "waiting on an approval or question" if row["state"] == "asking" else "running"}
        for row in db.open_watches()
    ]


def thread_reasons(threads):
    waiting = [t for t in threads if t["status"] != "running"]
    return [f"the T3 thread \"{t['title']}\" ({t['thread_id']}) on {t['environment']} waits on an approval or question" for t in waiting]


def rate_reasons(now):
    since = now - last_restart()
    limit = interval()
    if since < limit:
        return [f"I restarted {int(since)} s ago, the next restart may come in {int(limit - since)} s"]
    return []


def assess(conv=None, now=None):
    """Whether a restart is safe right now: {"safe", "reasons", "threads"}. Anything unknown counts as unsafe."""
    now = now or time.time()
    reasons = []
    if not allowed():
        reasons.append(f"restarting myself is switched off in the setting {ALLOWED_KEY}")
    if not under_systemd():
        reasons.append("this hub does not run under systemd, so nothing would bring it back up")
    reasons += rate_reasons(now)
    reasons += turn_reasons(conv)
    reasons += ears_reasons()
    reasons += approval_reasons()
    threads = watched()
    reasons += thread_reasons(threads)
    return {"safe": not reasons, "reasons": reasons, "threads": threads}


def record(outcome, reason, conv, detail=""):
    logger.info("restart %s (%s): %s %s", outcome, conv, reason, detail)
    try:
        brains.STATE.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.time(), "outcome": outcome, "conv": conv, "reason": reason[:300], "detail": detail[:300]}
        with open(brains.STATE / "restarts.jsonl", "a") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError:
        pass


def request(conv, reason, dry_run=False, handover=True):
    conv = conv or brains.MAIN
    reason = " ".join(str(reason or "").split())[:300]
    verdict = assess(conv)
    verdict["dry_run"] = bool(dry_run)
    verdict["restarting"] = False
    if not reason and not dry_run:
        verdict["safe"] = False
        verdict["reasons"].append("say why you restart, in a few words")
    if _scheduled["conv"] is not None:
        verdict["safe"] = False
        verdict["reasons"].append("a restart is already scheduled")
    if dry_run:
        record("checked", reason or "dry run", conv, "safe" if verdict["safe"] else "; ".join(verdict["reasons"]))
        return verdict
    if not verdict["safe"]:
        record("refused", reason, conv, "; ".join(verdict["reasons"]))
        return verdict
    _scheduled["conv"] = conv
    verdict["restarting"] = True
    record("scheduled", reason, conv)
    _scheduled["task"] = asyncio.get_running_loop().create_task(perform(conv, reason, handover))
    return verdict


async def perform(conv, reason, handover=True):
    """Wait until everything has been quiet for a few seconds, which covers the reply reaching him, then end the process for systemd to bring back."""
    try:
        deadline = time.monotonic() + WAIT_S
        quiet_since = None
        while True:
            final = assess(None)
            now = time.monotonic()
            if final["safe"]:
                quiet_since = quiet_since if quiet_since is not None else now
                if now - quiet_since >= SETTLE_S:
                    break
            else:
                quiet_since = None
            if now >= deadline:
                why = "; ".join(final["reasons"])
                record("cancelled", reason, conv, why)
                if handover:
                    brains.give_back_handover(conv, f"(Your restart for \"{reason}\" was cancelled because {why}. Ask restart_self again when it is safe.)")
                return
            await asyncio.sleep(POLL_S)
        settings.set_setting(LAST_KEY, str(time.time()))
        settings.set_setting(ANNOUNCE_KEY, json.dumps({"conv": conv, "reason": reason, "at": time.time()}))
        record("restarting", reason, conv)
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception as error:
        logger.exception("the restart failed")
        record("failed", reason, conv, str(error))
        try:
            brains.give_back_handover(conv, f"(Your restart for \"{reason}\" failed: {error}. You are still running.)")
        except Exception:
            logger.exception("could not leave the handover note")
    finally:
        _scheduled["conv"] = None
        _scheduled["task"] = None


def announce_text(note):
    return (
        f"[restart event] You restarted yourself a moment ago because: {note['reason']}. The session resumed and the "
        "watched T3 threads are still registered. Tell Marcus in one short line that you restarted and why. "
        "Do not restart again."
    )


async def announce(deliver):
    """After a self restart, tell Marcus in one line, once."""
    try:
        note = json.loads(settings.get(ANNOUNCE_KEY) or "null")
    except ValueError:
        note = None
    if not isinstance(note, dict):
        return
    settings.set_setting(ANNOUNCE_KEY, "")
    if time.time() - float(note.get("at") or 0) > ANNOUNCE_MAX_AGE_S:
        return
    await asyncio.sleep(ANNOUNCE_DELAY_S)
    conv = note.get("conv") or brains.MAIN
    try:
        turn = await brains.ask(conv, announce_text(note), origin="restart watcher", machine=True)
        text = turn.text.strip()
    except Exception:
        logger.exception("restart announcement turn failed")
        text = ""
    await deliver(text or f"I restarted myself: {note['reason']}.", {"conv": conv, "thread_id": "restart"})
