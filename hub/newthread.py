""""New thread" for T3 Code, worked out from what Marcus is looking at, with no model turn.

    "new thread"                with a T3 Code window focused: that window's project
    "new thread in AniBeam"     the T3 project of that name, on whichever box has it
    "new thread"                with neither: a question card lists the projects

The project in a T3 Code window is the part of its title before " / ", as in
"Anibeam / Comparing two approaches". Names match without regard to case, spacing or hyphens.
A name on two boxes goes to the box the focused T3 window is on, else the first box that is up.
Project ids come from each environment's t3_project_list, never from here.

The thread is made idle (no first message, he types it) and opened in T3 Code on his desk
through voice/window.py's thread verb, which is the window tool's own focus. When the project
is unclear, an ask_user card with an action of kind new_thread asks, two to four options at a
time: three projects and "More projects" while more are left. A tap, a number, or his own words
("the Diablo one") answer it.

Only re at the top: hub/route.py imports this on the ears' side too.
"""

import re

KIND = "new_thread"
TITLE = "New thread"
MORE = "More projects"
ASKED = "Which project?"
PAGE = 3
REPLY_WORDS = 8
PROJECTS_TTL_S = 30
QUESTION_TTL_S = 600
HOST_TIMEOUT_S = 4
T3_CLASS = "t3code"

_WAKE = re.compile(r"^(?:(?:hey|hi|ok|okay|hej|yo)\s+)?(?:diablo|dia)\b[\s,.!:]*")
_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_PUNCT = re.compile(r"[^\w\s'-]")
_COMMAND = re.compile(
    r"^(?:(?:please|so|okay|ok|and|now)\s+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    r"(?:(?:make|create|start|open|spin\s+up|give|get|set\s+up)\s+(?:me\s+|us\s+)?)?"
    r"(?:(?:a|an|another)\s+)?(?:new|fresh|empty|blank)\s+(?:t3\s+(?:code\s+)?)?thread"
    r"(?:\s+(?:in|for|on|under|inside)\s+(?:the\s+)?(?!(?:me|us)\b)(?P<name>[\w'-]+(?:\s+[\w'-]+){0,3}?))?"
    r"(?:\s+(?:project|repo|app))?"
    r"(?:\s+(?:please|for\s+me|now|thanks))*$"
)
_FILLER = set("""the a an in on for one project repo app please thanks it that this option number
choice pick go with i'll ill take let's lets do say choose yes ok okay sure sir""".split())

_projects = {"at": 0.0, "rows": None}


def _clean(text):
    low = " ".join(str(text or "").replace("’", "'").lower().split())
    low = _PREFIX.sub("", low, count=1)
    low = _WAKE.sub("", low, count=1)
    return " ".join(_PUNCT.sub(" ", low).split())


def command(text):
    """The project name a "new thread" line names, "" when it names none, None when the line is
    not that command. Anything more than a name ("to fix the orb") is work for the brain."""
    found = _COMMAND.match(_clean(text))
    if not found:
        return None
    return (found.group("name") or "").strip()


def norm(name):
    """A name as it is compared: lower case, letters and digits only. AniBeam, Ani-Beam, ani beam."""
    return re.sub(r"[^0-9a-z]", "", str(name or "").lower())


def window_project(desktop=None, context=None):
    """The project in the focused T3 Code window, or "" when the focused window is not T3 Code.

    desktop is the ears' {"windows": [...]}; context is the turn's note lines, which carry
    "Focused window: com.t3tools.T3Code: Anibeam / A thread (workspace 2)"."""
    windows = [w for w in (desktop or {}).get("windows") or [] if isinstance(w, dict)]
    focused = next((w for w in windows if w.get("focused")), None)
    if focused is not None:
        cls, title = str(focused.get("class") or ""), str(focused.get("title") or "")
    else:
        line = next((str(c) for c in context or [] if str(c).startswith("Focused window: ")), "")
        rest = re.sub(r"\s*\(workspace [^)]*\)\s*$", "", line[len("Focused window: "):])
        cls, _, title = rest.partition(": ")
    if T3_CLASS not in norm(cls):
        return ""
    project, sep, _ = title.partition(" / ")
    return project.strip() if sep else ""


# ---------------------------------------------------------------- projects on every box
def _rows(env, data):
    rows = []
    for p in (data or {}).get("projects") or [] if isinstance(data, dict) else []:
        if not isinstance(p, dict) or not p.get("id") or p.get("deletedAt") or p.get("archivedAt"):
            continue
        rows.append({"env": env, "id": str(p["id"]), "title": str(p.get("title") or "").strip() or "Untitled",
                     "updated": str(p.get("updatedAt") or "")})
    return rows


def _env_projects(env, call):
    rows, cursor = [], None
    for _ in range(10):
        data = call(env, "t3_project_list", {"limit": 100, **({"cursor": cursor} if cursor else {})}, HOST_TIMEOUT_S)
        rows += _rows(env, data)
        cursor = (data or {}).get("nextCursor") if isinstance(data, dict) else None
        if not cursor:
            break
    return rows


def projects(fresh=False, envs=None, call=None, is_up=None, now=None):
    """Every T3 project on every box that is up: [{env, id, title, updated}], boxes in the
    t3.environments order, kept for PROJECTS_TTL_S. A box that is down is skipped; one that is
    slow is waited on at most HOST_TIMEOUT_S a page, all boxes at once, then left out."""
    import concurrent.futures
    import logging
    import time

    from . import t3link

    now = time.time() if now is None else now
    if not fresh and _projects["rows"] is not None and now - _projects["at"] < PROJECTS_TTL_S:
        return list(_projects["rows"])
    call = call or t3link.call_tool
    is_up = is_up or t3link.is_up
    envs = [e for e in (envs if envs is not None else t3link.environments()) if is_up(e)]

    def one(env):
        try:
            return _env_projects(env, call)
        except Exception as error:
            logging.getLogger(__name__).info("t3 projects on %s failed: %s", env, error)
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(envs))) as pool:
        rows = [row for found in pool.map(one, envs) for row in found]
    _projects.update(at=now, rows=rows)
    return list(rows)


def match(name, rows, prefer=""):
    """The projects a said name means, best box first: an exact name (spacing, case and hyphens
    aside), else one the name starts or is held in, else a near spelling. [] when none fits."""
    import difflib

    said = norm(name)
    if not said:
        return []
    exact = [r for r in rows if norm(r["title"]) == said]
    found = exact or [r for r in rows if len(said) >= 3 and (norm(r["title"]).startswith(said) or said in norm(r["title"]))]
    if not found:
        close = difflib.get_close_matches(said, sorted({norm(r["title"]) for r in rows}), n=1, cutoff=0.8)
        found = [r for r in rows if close and norm(r["title"]) == close[0]]
    order = {env: i for i, env in enumerate(dict.fromkeys(r["env"] for r in rows))}
    return sorted(found, key=lambda r: (r["env"] != prefer, order.get(r["env"], 99)))


def one_project(found):
    """The single project a match settles on, or None when it names more than one: the same
    title on several boxes is one project (best box first), two titles are two."""
    if not found:
        return None
    return found[0] if len({norm(r["title"]) for r in found}) == 1 else None


# ---------------------------------------------------------------- making and opening it
def launch(project, call=None):
    """Makes an idle thread in the project. {"thread": id, "link": t3code link, "env"}. Raises on failure."""
    from . import t3link, t3threads

    data = (call or t3link.call_tool)(project["env"], "t3_thread_launch", {"projectId": project["id"], "title": TITLE}, 30)
    data = data if isinstance(data, dict) else {}
    link = t3threads.open_link({"link": data.get("link")}) or t3threads.open_link(data.get("thread") or {})
    thread = str(data.get("threadId") or (data.get("thread") or {}).get("id") or "")
    if not thread:
        raise RuntimeError(f"T3 gave no thread back: {str(data)[:160]}")
    return {"thread": thread, "link": link, "env": project["env"]}


def bring_forward(link, host=None, run=None):
    """(ok, why): opens the thread in T3 Code on the desk and focuses it, through voice/window.py."""
    from . import desktop

    if not link:
        return False, "T3 gave no link to open"
    try:
        done = (run or desktop.desktop_run)([".venv-voice/bin/python", "-m", "voice.window", "thread", link],
                                            timeout=30, **({"host": host} if host else {}))
    except desktop.Unreachable as error:
        return False, str(error)
    said = (done.stdout or done.stderr or "").strip().splitlines()
    return done.returncode == 0, (said[-1] if said else "")[:200]


def _place(project, rows):
    twins = {r["env"] for r in rows if norm(r["title"]) == norm(project["title"])}
    return f"{project['title']} on {project['env']}" if len(twins) > 1 else project["title"]


def make(project, rows, host=None, call=None, run=None):
    """Makes the thread and brings it forward. The one line to say."""
    import logging

    place = _place(project, rows)
    try:
        made = launch(project, call)
    except Exception as error:
        logging.getLogger(__name__).warning("new thread in %s failed: %s", place, error)
        return f"I could not make a thread in {place}."
    ok, why = bring_forward(made["link"], host, run)
    if not ok:
        logging.getLogger(__name__).info("new thread in %s made, not focused: %s", place, why)
        return f"New thread in {place} is made, but T3 Code would not come forward."
    return f"New thread in {place}, ready for you."


# ---------------------------------------------------------------- asking which one
def ordered(rows, prefer=""):
    """Projects for the card: the preferred box first, then the boxes in order, each box's
    projects newest first, the scratch "No project" last on its box."""
    order = {env: i for i, env in enumerate(dict.fromkeys(r["env"] for r in rows))}
    by_new = sorted(rows, key=lambda r: r["updated"], reverse=True)
    return sorted(by_new, key=lambda r: (r["env"] != prefer, order.get(r["env"], 99), norm(r["title"]) == "noproject"))


def page_of(rows, start):
    """(projects on this page, whether a More option follows): all when four or fewer are left,
    else three and More. The last page then always holds two to four."""
    left = rows[start:]
    return (left, False) if len(left) <= PAGE + 1 else (left[:PAGE], True)


def _labels(shown, rows):
    """One label a project, never two the same once case is set aside, as the card needs."""
    labels, taken = [], set()
    for r in shown:
        label, n = _place(r, rows)[:44], 2
        while label.lower() in taken:
            label, n = f"{_place(r, rows)[:40]} {n}", n + 1
        taken.add(label.lower())
        labels.append(label)
    return labels


def _key(r):
    return [r["env"], r["id"]]


def ask(conv, pool, start=0, name="", prefer="", ask_fn=None, rows=None):
    """Asks which project with a question card, paging through pool. rows are all the projects
    (for "on <box>" when a title is on two). (line, question block)."""
    from . import questions

    rows = rows if rows is not None else pool
    listed = ordered(pool, prefer)
    if start < 0 or len(listed) - start < 2:
        start = 0
    shown, more = page_of(listed, start)
    options = [{"label": label} for label in _labels(shown, rows)]
    picks = [{"env": r["env"], "id": r["id"], "title": r["title"]} for r in shown]
    if more:
        options.append({"label": MORE})
        picks.append({"more": start + len(shown)})
    missing = f"No project called {name}. " if name else ""
    asked = (ask_fn or questions.ask)(conv, {
        "header": TITLE, "question": "Which project gets the new thread?", "options": options,
        "allow_text": True,
    }, ttl_s=QUESTION_TTL_S, action={"kind": KIND, "picks": picks, "name": name, "prefer": prefer,
                                     "pool": [_key(r) for r in listed]})
    return f"{missing}{ASKED}", questions.public(asked)


def plan(text, desktop=None, context=None, host=""):
    """What a line asks for: None when it is not the command, else {"name", "window", "prefer"}.
    prefer is the box a focused T3 window is on: the box whose ears heard him, else the desk."""
    name = command(text)
    if name is None:
        return None
    window = window_project(desktop, context)
    return {"name": name, "window": window, "prefer": host if window else ""}


def run(conv, step, rows, host=None, call=None, run_fn=None, ask_fn=None, pool=None):
    """Carries out a plan. pool narrows a name to the projects a card showed, when it fits one of
    them. (line, question block or None)."""
    if not rows:
        return "No T3 Code box is up with a project on it.", None
    name, prefer = step["name"], step["prefer"]
    if name:
        found = (match(name, pool, prefer) if pool else []) or match(name, rows, prefer)
        chosen = one_project(found)
        if chosen is not None:
            return make(chosen, rows, host, call, run_fn), None
        if len(found) > 1:
            return ask(conv, found, 0, "", prefer, ask_fn, rows)
        if len(rows) == 1:
            return f"No project called {name}.", None
        return ask(conv, rows, 0, name, prefer, ask_fn)
    if step["window"]:
        chosen = one_project(match(step["window"], rows, prefer))
        if chosen is not None:
            return make(chosen, rows, host, call, run_fn), None
    if len(rows) == 1:
        return make(rows[0], rows, host, call, run_fn), None
    return ask(conv, rows, 0, "", prefer, ask_fn)


def is_mine(question):
    return isinstance(question, dict) and (question.get("action") or {}).get("kind") == KIND


def _said(text):
    return " ".join(w for w in re.findall(r"[\w'-]+", str(text or "").lower()) if w not in _FILLER)


def _numbers_only(words):
    from . import questions

    return bool(words) and all(w.isdigit() or w in questions.NUMBER_WORDS for w in words.split())


def might_answer(question, text):
    """Cheap first look, with no project list: could this reply answer the card at all? A long
    line or a new command never does."""
    from . import questions

    if not is_mine(question) or command(text) is not None:
        return False
    if questions.parse_reply(question, text) is not None:
        return True
    return 0 < len(_said(text).split()) <= REPLY_WORDS


def pool_of(question, rows):
    """The projects the card was paging through, in its order, from today's list."""
    keys = [tuple(k) for k in (question.get("action") or {}).get("pool") or [] if isinstance(k, list) and len(k) == 2]
    by_key = {(r["env"], r["id"]): r for r in rows}
    return [by_key[k] for k in keys if k in by_key]


def reply_choices(question, text, rows):
    """How a typed or spoken reply answers the card: [] for words that name a project (matched
    like a name, the card's own projects first), the option numbers it names, or None when it
    answers nothing and goes on to the brain."""
    from . import questions

    if not might_answer(question, text):
        return None
    words = _said(text)
    if words and not _numbers_only(words) and (match(words, pool_of(question, rows)) or match(words, rows)):
        return []
    return questions.parse_reply(question, text)


def answer(question, rows, conv, host=None, call=None, run_fn=None, ask_fn=None):
    """An answered card: a pick makes the thread or shows the next page of the same projects,
    words are matched like a name. (line, question block or None)."""
    action = question.get("action") or {}
    picks = action.get("picks") or []
    got = question.get("answer") or {}
    prefer = action.get("prefer") or ""
    pool = pool_of(question, rows)
    chosen = [picks[i] for i in got.get("choices") or [] if 0 <= i < len(picks)]
    if chosen and "more" in chosen[0]:
        if len(rows) == 1:
            return make(rows[0], rows, host, call, run_fn), None
        return ask(conv, pool if len(pool) >= 2 else rows, int(chosen[0]["more"]), "", prefer, ask_fn, rows)
    if chosen:
        pick = chosen[0]
        project = next((r for r in rows if r["env"] == pick.get("env") and r["id"] == pick.get("id")), None)
        if project is None:
            project = {"env": pick.get("env"), "id": pick.get("id"), "title": pick.get("title") or "that project"}
        return make(project, rows, host, call, run_fn), None
    step = {"name": _said(got.get("text")), "window": "", "prefer": prefer}
    return run(conv, step, rows, host, call, run_fn, ask_fn, pool)
