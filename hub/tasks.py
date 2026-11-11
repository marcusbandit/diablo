"""Live task cards: what Diablo runs or watches, kept by the hub with no model turn.

A watched T3 thread is a task whose id is the thread id; hub/watch.py and hub/t3threads.py
update it from the thread's state. Other work is a task made with the update_task tool.
Every change publishes {"kind": "task", "change", "task"} on /v1/events.
"""
import logging
import re
import secrets
import threading
import time

from . import db, events

logger = logging.getLogger(__name__)

KINDS = ("self_improvement", "partner")
STATES = ("queued", "working", "waiting", "done", "failed")
ACTIVE = ("queued", "working", "waiting")
FINISHED = ("done", "failed")
LINK_KEYS = ("pr", "pr_number", "pr_state", "issue", "thread")
WINDOW_S = 12 * 3600
FAILED_KEPT_S = 7 * 86400
STALE_S = 48 * 3600
PRUNE_S = 30 * 86400
MOST = 50
STEP_CHARS = 120
RESULT_CHARS = 200
TITLE_CHARS = 120
SELF_REPO = "github.com/marcusbandit/diablo"
QUEUED = {"queued", "pending", "requested"}
REWATCH_S = 300
REWATCH_GRACE_S = 120
UNREADABLE_RETRY_S = 600
PRUNE_EVERY_S = 3600

_write = threading.Lock()
_pruned = [0.0]


def new_id():
    return f"task-{secrets.token_hex(4)}"


def line(text, most):
    text = " ".join(str(text or "").split())
    if len(text) <= most:
        return text
    return text[: most - 3].rstrip() + "..."


def blank_links():
    return {"pr": "", "pr_number": None, "pr_state": "", "issue": "", "thread": ""}


def public(task):
    links = blank_links()
    links.update({k: v for k, v in (task.get("links") or {}).items() if k in LINK_KEYS})
    return {
        "id": task["id"],
        "title": task.get("title") or "Untitled",
        "kind": task.get("kind") if task.get("kind") in KINDS else "partner",
        "machine": task.get("machine") or "",
        "state": task.get("state") if task.get("state") in STATES else "working",
        "step": task.get("step") or "",
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "seen_at": task.get("seen_at"),
        "updated_at": task.get("updated_at"),
        "links": links,
        "result": task.get("result") or "",
        "source": task.get("source") or "tool",
    }


def clean(fields):
    """Only the fields a caller may set, each trimmed to its shape; unknown or bad values are dropped."""
    out = {}
    if fields.get("title") is not None and str(fields["title"]).strip():
        out["title"] = line(fields["title"], TITLE_CHARS)
    if fields.get("kind") in KINDS:
        out["kind"] = fields["kind"]
        out["kind_source"] = fields.get("kind_source") or "arg"
    if fields.get("state") in STATES:
        out["state"] = fields["state"]
    for key, most in (("step", STEP_CHARS), ("result", RESULT_CHARS)):
        if fields.get(key) is not None:
            out[key] = line(fields[key], most)
    if fields.get("machine") is not None:
        out["machine"] = line(fields["machine"], 40)
    if fields.get("source"):
        out["source"] = line(fields["source"], 300)
    links = fields.get("links")
    if isinstance(links, dict):
        kept = {}
        for key in LINK_KEYS:
            if key not in links:
                continue
            value = links[key]
            if key == "pr_number":
                try:
                    kept[key] = int(value) if value not in (None, "") else None
                except (TypeError, ValueError):
                    continue
            else:
                kept[key] = line(value, 300)
        if kept.get("pr") and "pr_number" not in kept:
            found = re.search(r"/pull/(\d+)", kept["pr"])
            if found:
                kept["pr_number"] = int(found.group(1))
        out["links"] = kept
    return out


def _compare(task):
    return {k: v for k, v in public(task).items() if k != "updated_at"} | {"kind_source": task.get("kind_source")}


def save(task_id, fields, now=None):
    """Create or update one task and publish the change. Returns the public task."""
    with _write:
        return _save(task_id, fields, now)


def _save(task_id, fields, now=None):
    now = time.time() if now is None else now
    task_id = str(task_id or "").strip() or new_id()
    before = db.get_task(task_id)
    changes = clean(fields)
    task = dict(before) if before else {
        "id": task_id, "title": "Untitled", "kind": "partner", "kind_source": "guess", "machine": "",
        "state": "working", "step": "", "started_at": now, "finished_at": None, "seen_at": None,
        "updated_at": now, "links": blank_links(), "result": "", "source": "tool",
    }
    links = dict(task.get("links") or {})
    links.update(changes.pop("links", {}))
    task.update(changes)
    task["links"] = links
    if before and changes.get("state") and changes["state"] != before.get("state"):
        task["seen_at"] = None
        if changes["state"] in ACTIVE:
            task["finished_at"] = None
            if before.get("state") in FINISHED:
                task["started_at"] = fields.get("started_at") or now
    for key in ("started_at", "finished_at"):
        if fields.get(key):
            task[key] = float(fields[key])
    if task["state"] in FINISHED and not task.get("finished_at"):
        task["finished_at"] = now
    if not task.get("started_at"):
        task["started_at"] = now
    if before and _compare(before) == _compare(task):
        return public(before)
    task["updated_at"] = now
    db.put_task(task)
    shown = public(task)
    events.publish({"kind": "task", "change": "updated" if before else "created", "task": shown})
    return shown


def seen(task_id, now=None):
    now = time.time() if now is None else now
    with _write:
        task = db.get_task(task_id)
        if not task:
            return None
        if task.get("seen_at"):
            return public(task)
        task["seen_at"] = now
        task["updated_at"] = now
        db.put_task(task)
    shown = public(task)
    events.publish({"kind": "task", "change": "seen", "task": shown})
    return shown


RANK = {"waiting": 0, "failed": 1, "working": 2, "queued": 3}


def listing(states=None, since=None, now=None):
    """Active tasks, failed ones not yet seen, and the rest that finished since `since`."""
    now = time.time() if now is None else now
    since = now - WINDOW_S if since is None else since
    shown = []
    for task in db.list_tasks(states or None, limit=500):
        state = task.get("state")
        ended = task.get("finished_at") or task.get("updated_at") or 0
        if state in ACTIVE:
            keep = (task.get("updated_at") or 0) >= now - STALE_S
        elif state == "failed" and not task.get("seen_at"):
            keep = ended >= now - FAILED_KEPT_S
        else:
            keep = ended >= since
        if keep:
            shown.append(public(task))

    def order(task):
        rank = RANK.get(task["state"], 4)
        if task["state"] == "failed" and task["seen_at"]:
            rank = 4
        when = task["finished_at"] if rank == 4 else task["updated_at"]
        return rank, -(when or 0)

    return sorted(shown, key=order)[:MOST]


def prune(now=None):
    """Drops finished tasks older than PRUNE_S, at most once every PRUNE_EVERY_S."""
    now = time.time() if now is None else now
    if now - _pruned[0] >= PRUNE_EVERY_S:
        _pruned[0] = now
        db.prune_tasks(now - PRUNE_S)


def thread_state(thread, run):
    """A T3 thread's state as a task state, read the same way as /v1/t3/threads, and the step
    to show when the state says it all."""
    from .t3threads import state_of

    shown = state_of(thread, run)
    status = str((run or {}).get("status") or thread.get("status") or "")
    if shown == "working":
        return ("queued" if not thread.get("activeRunId") and status in QUEUED else "working"), None
    if shown == "stopped":
        return "done", STOPPED_STEP
    if shown == "finished":
        return "done", None
    if shown in ("waiting", "failed"):
        return shown, None
    return "working", None


STOPPED_STEP = "Stopped"


PATH = re.compile(r"(?:~|\.{0,2})?/[^\s\"']+")


def short_paths(text):
    return PATH.sub(lambda m: m.group(0).rstrip("/").rsplit("/", 1)[-1] or m.group(0), str(text or ""))


def latest_step(items):
    """The newest thing a thread did, in one line: its last words to the user, else its last tool."""
    for item in reversed([i for i in items or [] if isinstance(i, dict)]):
        kind = item.get("type")
        text = str(item.get("text") or "").strip()
        if kind == "assistant_message" and text:
            first = re.split(r"(?<=[.!?])\s|\n", text, maxsplit=1)[0]
            return line(short_paths(first), STEP_CHARS)
        if kind == "dynamic_tool" and item.get("title"):
            return line(short_paths(item["title"]), STEP_CHARS)
        if kind == "command_execution" and text.startswith("$"):
            words = text[1:].split()
            if words:
                verb = "Running" if item.get("status") == "in_progress" else "Ran"
                return line(f"{verb} {short_paths(words[0])}", STEP_CHARS)
    return None


def kind_of_project(project):
    """self_improvement for the diablo repo, partner for anything else."""
    project = project or {}
    repo = str(((project.get("repositoryIdentity") or {}).get("canonicalKey")) or "").lower()
    root = str(project.get("workspaceRoot") or "").rstrip("/")
    if repo == SELF_REPO or repo.endswith("/marcusbandit/diablo"):
        return "self_improvement"
    if not repo and root.rsplit("/", 1)[-1] == "diablo":
        return "self_improvement"
    return "partner"


_kinds = {}
_unreadable = {}


def resolve_kind(env, project_id, timeout=10):
    """The kind of work in a T3 project, read once per project. None when it cannot be read,
    and then not asked again for UNREADABLE_RETRY_S."""
    if not project_id:
        return None
    key = (env, project_id)
    if key in _kinds:
        return _kinds[key]
    if time.time() - _unreadable.get(key, 0.0) < UNREADABLE_RETRY_S:
        return None
    from . import t3link
    try:
        project = t3link.call_tool(env, "t3_project_read", {"projectId": project_id}, timeout)
    except Exception as error:
        logger.info("project %s on %s not readable: %s", project_id, env, error)
        project = None
    if not isinstance(project, dict) or not (project.get("id") or project.get("workspaceRoot")):
        _unreadable[key] = time.time()
        return None
    _kinds[key] = kind_of_project(project)
    return _kinds[key]


def kind_at_watch(env, thread_id, timeout=10):
    """The kind for a thread being watched, from its project; ("partner", "guess") when unknown."""
    from . import t3link
    try:
        data = t3link.call_tool(env, "t3_thread_read", {"threadId": thread_id, "limit": 1, "maxCharsPerItem": 1, "runLimit": 1}, timeout)
        project_id = ((data or {}).get("thread") or {}).get("projectId") if isinstance(data, dict) else None
    except Exception as error:
        logger.info("thread %s on %s not readable at watch time: %s", thread_id, env, error)
        project_id = None
    kind = resolve_kind(env, project_id, timeout)
    return (kind, "project") if kind else ("partner", "guess")


def observe(watch, data, step=None):
    """Bring a watched thread's task card up to date from a t3_thread_read answer. Returns the task."""
    from .t3threads import newest_pr, open_link
    from .watch import parse_time

    thread = (data or {}).get("thread") or {}
    runs = (data or {}).get("recentRuns") or []
    run = runs[0] if runs and isinstance(runs[0], dict) else {}
    state, fixed = thread_state(thread, run)
    known = db.get_task(watch["thread_id"])
    ended = parse_time(run.get("completedAt"))
    began = parse_time(run.get("startedAt") or run.get("requestedAt"))
    created = float(watch.get("created") or 0)
    stale = bool(state in FINISHED and known and known.get("state") in ACTIVE and began and created
                 and began < created - REWATCH_GRACE_S and time.time() - created < REWATCH_S)
    if stale:
        state, fixed = known["state"], None
    fields = {
        "title": watch.get("title") or thread.get("title") or (known or {}).get("title"),
        "machine": watch.get("env") or "",
        "state": state,
        "source": f"t3:{watch['thread_id']}",
    }
    pending = int(thread.get("pendingRequestCount") or 0)
    if state == "waiting":
        fields["step"] = f"Waits on {pending} request{'s' if pending != 1 else ''} from you" if pending else "Waits on you"
    elif fixed:
        fields["step"] = fixed
    elif step:
        fields["step"] = step
    elif not known:
        fields["step"] = line(thread.get("title") or "", STEP_CHARS)
    elif known.get("state") == "waiting" or known.get("step") == STOPPED_STEP:
        fields["step"] = "Carrying on" if state in ACTIVE else ""
    if began and not stale:
        fields["started_at"] = began
    if state in FINISHED:
        finished = ended or parse_time(thread.get("updatedAt"))
        if finished:
            fields["finished_at"] = finished
    pr = newest_pr(thread)
    links = {"thread": open_link(thread)} if thread.get("link") else {}
    if pr and pr.get("url"):
        links.update({"pr": pr["url"], "pr_number": pr.get("number"), "pr_state": str(pr.get("state") or "")})
    fields["links"] = links
    if not known or known.get("kind_source") == "guess":
        kind = resolve_kind(watch.get("env"), thread.get("projectId"))
        if kind:
            fields.update(kind=kind, kind_source="project")
        elif not known:
            fields.update(kind="partner", kind_source="guess")
    return save(watch["thread_id"], fields)
