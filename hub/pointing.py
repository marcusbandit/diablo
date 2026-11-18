"""Pointing at things on the desktop: the marks the brain draws over the real screen.

The brain's point_on_screen tool (hub/mcp_server.py) gives beats, each a line he says and the
marks that go with it. A mark's target is the visible words of a control ({text, window, near})
or a box in global logical pixels. Words are found on the desktop by `python -m voice.hands
find-text`, all of a call's lookups at once; each beat goes to POST /v1/screen/marks as soon as
its own lookups are done, so the first marks show while later ones are still being found.

A walkthrough beat waits (wait "click") until the pointer has been in its box and left it. The
beats after it are sent with their words unresolved and `lazy`; presence looks them up on the
screen as it is by then, through `python -m hub.pointing resolve BEAT_JSON` on the desktop.

Standard library only at the top, so the desktop can run this file without the hub's packages.
"""

import json
import math
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

KINDS = ("ring", "underline", "arrow", "label", "spotlight", "highlight", "num", "check", "cross")
MAX_BEATS = 8
MAX_MARKS = 8
FIND_SECONDS = 12
LIMIT = 100000


def number(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and abs(out) <= LIMIT else None


def clean_box(raw):
    if not isinstance(raw, dict):
        return None
    x, y, w, h = (number(raw.get(key)) for key in ("x", "y", "w", "h"))
    if None in (x, y, w, h) or w < 0 or h < 0:
        return None
    return {"x": round(x, 1), "y": round(y, 1), "w": round(min(w, 20000), 1), "h": round(min(h, 20000), 1)}


def clean_point(raw):
    if not isinstance(raw, dict):
        return None
    x, y = number(raw.get("x")), number(raw.get("y"))
    return None if x is None or y is None else {"x": round(x, 1), "y": round(y, 1)}


def words(value, limit):
    return " ".join(str(value or "").split())[:limit]


def clean_target(raw):
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        return None
    text = words(raw.get("text"), 120)
    if not text:
        return None
    target = {"text": text, "window": words(raw.get("window"), 80) or "front"}
    near = words(raw.get("near"), 120)
    if near:
        target["near"] = near
    return target


def clean_mark(raw):
    """One mark as presence draws it, or None when it has no kind or nothing to point at."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        return None
    kind = raw["kind"]
    target = raw.get("target")
    target = {"text": target} if isinstance(target, str) else target if isinstance(target, dict) else {}
    box = clean_box(raw.get("box")) or clean_box(target.get("box"))
    point = None if box else clean_point(raw.get("point")) or clean_point(target.get("point"))
    found = None if box or point else clean_target(target)
    if not (box or point or found):
        return None
    mark = {"kind": kind}
    if box:
        mark["box"] = box
    elif point:
        mark["point"] = point
    else:
        mark["target"] = found
    if kind == "label":
        mark["text"] = words(raw.get("text"), 40)
        if not mark["text"]:
            return None
    if kind == "num":
        n = number(raw.get("n"))
        mark["n"] = int(n) if n is not None and 1 <= n <= 99 else 1
    if kind == "arrow" and clean_point(raw.get("from")):
        mark["from"] = clean_point(raw.get("from"))
    return mark


def clean_beats(raw):
    """The beats as given, cleaned: at most MAX_BEATS, each with a say line, its marks and wait."""
    if isinstance(raw, dict):
        raw = [raw]
    beats = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        marks = [m for m in (clean_mark(x) for x in (item.get("marks") or [])[:MAX_MARKS]) if m]
        say = words(item.get("say"), 300)
        if not marks and not say:
            continue
        beat = {"say": say, "marks": marks, "wait": "click" if item.get("wait") == "click" else ""}
        if item.get("caption"):
            beat["caption"] = True
        beats.append(beat)
        if len(beats) >= MAX_BEATS:
            break
    return beats


def clean_payload(body):
    """POST /v1/screen/marks as it goes out on the event stream, or the reason it is refused."""
    if not isinstance(body, dict):
        return "give a beat or clear"
    if body.get("clear") and not body.get("marks") and not body.get("say"):
        return {"clear": True}
    show = "".join(ch for ch in str(body.get("show") or "") if ch.isalnum())[:32]
    if not show:
        return "a beat needs its show id"
    n, count = number(body.get("n")), number(body.get("count"))
    n = int(n) if n is not None and 0 <= n < MAX_BEATS else None
    if n is None:
        return f"n is the beat's place, 0 to {MAX_BEATS - 1}"
    count = int(count) if count is not None and 1 <= count <= MAX_BEATS else n + 1
    lazy = bool(body.get("lazy"))
    marks = [m for m in (clean_mark(x) for x in (body.get("marks") or [])[:MAX_MARKS]) if m]
    if not lazy and any("target" in m for m in marks):
        return "only a lazy beat may carry words to look up"
    out = {"show": show, "n": n, "count": max(count, n + 1), "say": words(body.get("say"), 300), "marks": marks,
           "wait": "click" if body.get("wait") == "click" else "", "lazy": lazy}
    if body.get("caption"):
        out["caption"] = True
    return out


HANDS = "./deploy/diablo-hands"


def find_command(text, window, hands=HANDS):
    return f"{hands} find-text --window {shlex.quote(window or 'front')} -- {shlex.quote(text)}"


def parse_found(code, out):
    """find-text's answer: the matches, or the error it gave, or why it could not be read."""
    lines = [line for line in str(out or "").strip().splitlines() if line.strip().startswith("{")]
    try:
        body = json.loads(lines[-1]) if lines else {}
    except ValueError:
        body = {}
    if code != 0 or not isinstance(body.get("matches"), list):
        return {"matches": [], "error": str(body.get("error") or f"find-text failed ({code})")[:200]}
    return body


def ssh_runner(host, repo, timeout=FIND_SECONDS, run=subprocess.run):
    """find(text, window) over ssh on the desktop box, the way the window verbs reach it."""
    import socket

    local = socket.gethostname() == host

    def find(text, window):
        command = f"cd {shlex.quote(repo)} && {find_command(text, window)}"
        argv = ["bash", "-c", command] if local else ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", host, command]
        try:
            done = run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"matches": [], "error": f"could not reach {host}: {error}"[:200]}
        return parse_found(done.returncode, done.stdout or done.stderr)

    return find


def local_runner(timeout=FIND_SECONDS, run=subprocess.run):
    """find(text, window) on this machine, for presence resolving a beat after a wait."""

    def find(text, window):
        hands = str(Path(__file__).resolve().parent.parent / "deploy" / "diablo-hands")
        argv = [hands, "find-text", "--window", window or "front", "--", text]
        try:
            done = run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"matches": [], "error": str(error)[:200]}
        return parse_found(done.returncode, done.stdout or done.stderr)

    return find


def fold(text):
    return " ".join(str(text or "").casefold().split())


def middle(box):
    return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2


def pick(matches, text, near=None):
    """The best match for the words: the same words before a longer line holding them, accessible
    controls before OCR, the one nearest `near` when given, then the smallest."""
    want = fold(text)
    best, best_key = None, None
    for match in matches or []:
        box = clean_box(match) if isinstance(match, dict) else None
        if not box:
            continue
        got = fold(match.get("text"))
        closeness = 0 if got == want else 1 if got.startswith(want) else 2
        source = 0 if match.get("source") == "atspi" else 1
        distance = 0.0
        if near:
            (ax, ay), (bx, by) = middle(box), middle(near)
            distance = math.hypot(ax - bx, ay - by)
        key = (closeness, round(distance / 200), source, box["w"] * box["h"])
        if best_key is None or key < best_key:
            best, best_key = box, key
    return best


def resolve(target, find):
    """A target's box, or (None, why)."""
    if target.get("near"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            wanted = pool.submit(find, target["text"], target.get("window"))
            nearby = pool.submit(find, target["near"], target.get("window"))
            found, around = wanted.result(), nearby.result()
        anchor = pick(around.get("matches"), target["near"])
    else:
        found, anchor = find(target["text"], target.get("window")), None
    box = pick(found.get("matches"), target["text"], anchor)
    if box:
        return box, None
    return None, found.get("error") or "not on screen"


def key_of(target):
    return (target["text"], target.get("window") or "front", target.get("near") or "")


def settle(beat, results):
    """The beat with each text target swapped for its box; marks not found are dropped."""
    marks, missing = [], []
    for mark in beat["marks"]:
        if "target" not in mark:
            marks.append(mark)
            continue
        box, why = results[key_of(mark["target"])]
        if box is None:
            missing.append({"text": mark["target"]["text"], "why": why})
            continue
        kept = {k: v for k, v in mark.items() if k != "target"}
        kept["box"] = box
        marks.append(kept)
    return {**beat, "marks": marks}, missing


def resolve_beat(beat, find, workers=6):
    targets = {key_of(m["target"]): m["target"] for m in beat.get("marks") or [] if "target" in m}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(targets) or 1))) as pool:
        futures = {key: pool.submit(resolve, target, find) for key, target in targets.items()}
        results = {key: future.result() for key, future in futures.items()}
    return settle(beat, results)


def play(beats, find, send, show=None, workers=8, clock=time.monotonic):
    """Looks up every eager beat's words at once and sends the beats in order, each as soon as its
    own lookups are back. Beats after a waiting beat go out lazy, to be looked up on the desktop.
    Returns what happened: sent beats, words not found, lazy count, and any send failure."""
    show = show or uuid.uuid4().hex[:10]
    count = len(beats)
    first_wait = next((i for i, b in enumerate(beats) if b.get("wait") == "click"), count)
    started = clock()
    report = {"show": show, "sent": [], "missing": [], "lazy": 0, "failed": None, "first_ms": None}
    eager = [b for i, b in enumerate(beats) if i <= first_wait]
    targets = {}
    for beat in eager:
        for mark in beat["marks"]:
            if "target" in mark:
                targets.setdefault(key_of(mark["target"]), mark["target"])
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(targets) or 1)))
    try:
        futures = {key: pool.submit(resolve, target, find) for key, target in targets.items()}
        for n, beat in enumerate(beats):
            lazy = n > first_wait
            if lazy:
                body = dict(beat)
                report["lazy"] += 1
            else:
                wanted = {key_of(m["target"]) for m in beat["marks"] if "target" in m}
                body, missing = settle(beat, {key: futures[key].result() for key in wanted})
                report["missing"] += [{"beat": n + 1, **m} for m in missing]
            failed = send({**body, "show": show, "n": n, "count": count, "lazy": lazy})
            if failed:
                report["failed"] = failed
                break
            report["sent"].append(n)
            if report["first_ms"] is None:
                report["first_ms"] = round((clock() - started) * 1000)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return report


def summary(report, beats):
    if report["failed"]:
        shown = len(report["sent"])
        return f"not shown: {report['failed']}" if not shown else f"showed {shown} of {len(beats)} beats, then: {report['failed']}"
    parts = [f"Pointing: {len(report['sent'])} beat{'s' if len(report['sent']) != 1 else ''} on screen"]
    if report["first_ms"] is not None:
        parts[0] += f", the first after {report['first_ms']} ms"
    if report["missing"]:
        lost = "; ".join(f"\"{m['text']}\" in beat {m['beat']} ({m['why']})" for m in report["missing"])
        parts.append(f"Not found, so not marked: {lost}. Say so in a few words, or describe where it is")
    if report["lazy"]:
        parts.append(f"The last {report['lazy']} beat{'s are' if report['lazy'] != 1 else ' is'} looked up on screen once he has done the step before")
    parts.append("Now say the beats' lines in order")
    return ". ".join(parts) + "."


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["resolve"]:
        sys.exit("usage: python -m hub.pointing resolve BEAT_JSON")
    try:
        beat = json.loads(argv[1] if len(argv) > 1 else sys.stdin.read() or "{}")
    except ValueError:
        beat = {}
    if not isinstance(beat, dict):
        beat = {}
    marks = [m for m in (clean_mark(x) for x in (beat.get("marks") or [])[:MAX_MARKS]) if m]
    settled, missing = resolve_beat({**beat, "marks": marks}, local_runner())
    print(json.dumps({"beat": {**settled, "lazy": False}, "missing": missing}))


if __name__ == "__main__":
    main()
