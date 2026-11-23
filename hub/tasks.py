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
UNTRACKED_S = 6 * 3600
UNWATCHED_STEP = "No longer watched"
UNTRACKED_STEP = "No update in a while"
EXPIRED_STEP = "Expired: nothing was asked"
WAITING_BARE_S = 3600
ASK_KINDS = ("question", "approval")
QUESTION_CHARS = 300
HEADER_CHARS = 24
LABEL_CHARS = 48
DESCRIPTION_CHARS = 140
OPTIONS_MAX = 4
RECOMMENDED = re.compile(r"\s*\(recommended\)\s*$", re.IGNORECASE)
APPROVAL_ASK = {"kind": "approval", "question": "It asks you to approve a step in T3"}
REQUEST_ASK = {"kind": "question", "question": "It asks you something in T3"}

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
        "ask": task.get("ask") if task.get("state") == "waiting" and task.get("ask") else None,
    }


def clean_ask(ask):
    """A question as its card carries it, or None: kind, request id, header, the question, up to
    OPTIONS_MAX options with a label, a description and whether it is the recommended one, whether
    more than one may be picked, and how many more questions wait behind this one."""
    if not isinstance(ask, dict):
        return None
    question = line(ask.get("question") or ask.get("prompt") or ask.get("text") or "", QUESTION_CHARS)
    if not question:
        return None
    wanted = ask.get("recommended")
    options, pick = [], None
    for raw in ask.get("options") or ask.get("choices") or []:
        if len(options) == OPTIONS_MAX:
            break
        if isinstance(raw, str):
            raw = {"label": raw}
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or raw.get("title") or raw.get("value") or "")
        marked = bool(RECOMMENDED.search(label)) or raw.get("recommended") is True
        label = line(RECOMMENDED.sub("", label), LABEL_CHARS)
        if not label:
            continue
        if pick is None and (marked or (isinstance(wanted, str) and wanted.strip().lower() == label.lower())):
            pick = len(options)
        options.append({"label": label, "description": line(raw.get("description") or "", DESCRIPTION_CHARS)})
    if pick is None and isinstance(wanted, int) and not isinstance(wanted, bool) and 0 <= wanted < len(options):
        pick = wanted
    for index, option in enumerate(options):
        option["recommended"] = index == pick
    try:
        more = max(0, int(ask.get("more") or 0))
    except (TypeError, ValueError):
        more = 0
    return {
        "kind": ask.get("kind") if ask.get("kind") in ASK_KINDS else "question",
        "id": line(ask.get("id") or ask.get("requestId") or "", 120),
        "header": line(ask.get("header") or "", HEADER_CHARS),
        "question": question,
        "options": options,
        "multi": ask.get("multi") is True or ask.get("multiSelect") is True,
        "more": more,
    }


def ask_line(ask):
    """The question and its options in one line, the recommended one marked."""
    if not ask:
        return ""
    labels = [o["label"] + (" (recommended)" if o.get("recommended") else "") for o in ask.get("options") or []]
    text = ask["question"]
    if labels:
        text += " Options: " + "; ".join(labels) + "."
    if ask.get("more"):
        text += f" {ask['more']} more after this."
    return text


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
    if "ask" in fields:
        out["ask"] = clean_ask(fields["ask"])
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
        "updated_at": now, "links": blank_links(), "result": "", "source": "tool", "ask": None,
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
    if task["state"] != "waiting":
        task["ask"] = None
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


def taken(task, now):
    """How long a task has run, or ran, in words."""
    start = task.get("started_at") or 0
    end = task.get("finished_at") if task.get("state") in FINISHED and task.get("finished_at") else now
    seconds = max(0, int(end - start)) if start else 0
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


def spoken_listing(state=None, now=None):
    """The task cards as the list_tasks tool answers them: one line each, waiting ones first."""
    now = time.time() if now is None else now
    if state and state not in STATES:
        return f"state is one of {', '.join(STATES)}"
    shown = listing([state] if state else None, now=now)
    if not shown:
        return f"no {state} tasks" if state else "no tasks"
    rows = []
    for task in shown:
        parts = [f"{task['state']}: {task['title']}"]
        if task["ask"]:
            parts.append("asks: " + ask_line(task["ask"]))
        elif task["step"]:
            parts.append(task["step"])
        elif task["state"] == "waiting":
            parts.append("waits on Marcus")
        parts.append(("took " if task["state"] in FINISHED else "for ") + taken(task, now))
        if task["machine"]:
            parts.append(f"on {task['machine']}")
        rows.append(f"{', '.join(parts)} [{task['id']}]")
    waiting = sum(1 for task in shown if task["state"] == "waiting")
    head = f"{waiting} waiting on Marcus" if waiting else "nothing waiting on Marcus"
    return head + "\n" + "\n".join(rows)


def prune(now=None):
    """Drops finished tasks older than PRUNE_S, at most once every PRUNE_EVERY_S."""
    now = time.time() if now is None else now
    if now - _pruned[0] >= PRUNE_EVERY_S:
        _pruned[0] = now
        db.prune_tasks(now - PRUNE_S)


RUNNING = ("queued", "working")


def bare_wait_s():
    from . import settings
    try:
        return max(60.0, float(settings.get("tasks.waiting_bare_s") or WAITING_BARE_S))
    except (TypeError, ValueError):
        return float(WAITING_BARE_S)


def sweep(now=None):
    """Finishes the cards nothing keeps true any more, so none stays working or waiting forever:
    a T3 thread's card whose watch is closed, a card made with update_task that has had no update
    for UNTRACKED_S, and a card that has waited on Marcus with no question on it for bare_wait_s.
    Each card is read again under the write lock, so one rewritten meanwhile (a new watch, a fresh
    update) stays. A running card's finish time is its last update, so an old card does not show
    as just finished; a waiting card that expires finishes now, so list_tasks reports it.
    Returns the finished cards; the expired waiting ones carry expired=True."""
    now = time.time() if now is None else now
    bare = bare_wait_s()
    finished = []
    for listed in db.list_tasks(RUNNING + ("waiting",), limit=500):
        with _write:
            task = db.get_task(listed["id"])
            if not task or task.get("state") != listed.get("state") or task.get("updated_at") != listed.get("updated_at"):
                continue
            last = float(task.get("updated_at") or now)
            from_t3 = str(task.get("source") or "").startswith("t3:")
            watched = from_t3 and db.watch_is_open(task["id"])
            if task["state"] == "waiting":
                if from_t3 and not watched:
                    fields = {"step": UNWATCHED_STEP, "result": "It waited on you when its watch closed"}
                elif not task.get("ask") and now - last >= bare:
                    fields = {"step": EXPIRED_STEP, "result": f"Waited {taken({'started_at': last}, now)} with no question on it"}
                else:
                    continue
                shown = _save(task["id"], dict(fields, state="done", finished_at=now), now)
                finished.append(dict(shown, expired=True))
                continue
            if from_t3:
                if watched:
                    continue
                step = UNWATCHED_STEP
            elif now - last >= UNTRACKED_S:
                step = UNTRACKED_STEP
            else:
                continue
            finished.append(_save(task["id"], {"state": "done", "step": step, "finished_at": min(last, now)}, now))
    return finished


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


def ask_of_request(request_id, body):
    """The first question of a T3 pending request read with t3_pending_request, as a card ask."""
    if isinstance(body, dict) and isinstance(body.get("request"), dict):
        body = body["request"]
    if not isinstance(body, dict):
        return None
    questions = body.get("questions")
    questions = [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else [body]
    if not questions:
        return None
    return clean_ask(dict(questions[0], id=request_id, kind="question", more=len(questions) - 1))


_asks = {}


def read_ask(env, thread, timeout=15):
    """What a thread waiting on Marcus asks him, read once for each set of pending requests. A
    request that is not a question (an approval) or that cannot be read still gets a question, so a
    waiting card never shows a bare line."""
    from . import t3link

    thread_id = thread.get("threadId") or thread.get("id") or ""
    key = (int(thread.get("pendingRequestCount") or 0), str(thread.get("updatedAt") or ""))
    known = _asks.get(thread_id)
    if known and known[0] == key:
        return known[1]
    try:
        listed = t3link.call_tool(env, "t3_pending_request", {"action": "list", "threadId": thread_id}, timeout)
        ids = [str(i) for i in (listed or {}).get("requestIds") or []] if isinstance(listed, dict) else []
        if ids:
            body = t3link.call_tool(env, "t3_pending_request", {"action": "read", "threadId": thread_id, "requestId": ids[0]}, timeout)
            ask = ask_of_request(ids[0], body) or clean_ask(dict(REQUEST_ASK, id=ids[0]))
            ask["more"] += len(ids) - 1
        else:
            ask = clean_ask(APPROVAL_ASK)
    except Exception as error:
        logger.info("pending requests of %s on %s not readable: %s", thread_id, env, error)
        return (known[1] if known else None) or clean_ask(REQUEST_ASK)
    if len(_asks) > 200:
        _asks.clear()
    _asks[thread_id] = (key, ask)
    return ask


def observe(watch, data, step=None, ask=None):
    """Bring a watched thread's task card up to date from a t3_thread_read answer, with what it
    asks when it waits on Marcus (read here when not given). Returns the task."""
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
        fields["ask"] = ask or read_ask(watch.get("env"), dict(thread, threadId=watch["thread_id"]))
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
