"""What the receipts show: today set by set, the last 30 days, how many cards got done, how strong
the sets are getting, and every move's totals. Pure functions over the log, so the daemon builds it
once and the card only draws it."""

import datetime as dt

import catalog

PUSH_UPS, did, exercise_of = catalog.PUSH_UPS, catalog.did, catalog.exercise_of


def outcome(e):
    """What a card came to: hit, short or skipped. None for what wasn't a card answered by you:
    sets logged by hand, training, rest day passes, cards that never got an answer."""
    r = e.get("result")
    if r == "skip" or (r == "done" and did(e) <= 0):
        return "skipped"
    if r == "done":
        return "hit" if did(e) >= e.get("reps", 0) else "short"
    return None


def cards(entries, day_of, first, last):
    """Cards answered between two days, both included: {hit, short, skipped}."""
    out = {"hit": 0, "short": 0, "skipped": 0}
    for e in entries:
        o = outcome(e)
        if o and first <= day_of(e["ts"]) <= last:
            out[o] += 1
    return out


def momentum(entries):
    """Cards done in a row, back from the last one: a card with reps in it counts, a hand-logged
    set or training doesn't break the run, a skip ends it."""
    n = 0
    for e in reversed(entries):
        o = outcome(e)
        if o == "skipped":
            break
        if o:
            n += 1
    return n


def main_move(entries):
    """The move the strength numbers follow: the one with the most sets, push-ups when it's a tie."""
    counts = {}
    for e in entries:
        if did(e) > 0 and not catalog.is_stretch(exercise_of(e)):
            name = "push-ups" if exercise_of(e) in PUSH_UPS else exercise_of(e)
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "push-ups"
    return max(counts, key=lambda k: (counts[k], k == "push-ups"))


GROUPS = ("push", "pull", "legs", "core")
MAIN = 0.8  # a move counts for every group it works at least this share as hard as its main one


def leans_on(parts):
    """The groups a move or a session mainly works: push-ups are push and not core, burpees are
    push and legs, an hour of yoga is core."""
    top = max((parts.get(g, 0) for g in GROUPS), default=0)
    return [g for g in GROUPS if top and parts.get(g, 0) >= MAIN * top]


def timeline(entries, day_of, today, days=28):
    """Which muscle groups got worked on which day: push, pull, legs and core. A set or a session
    counts for the groups it mainly works (leans_on). Per group and day: the work in push-up
    equivalents on the group's hardest-worked muscle and the sets; plus the sessions, the move
    that fed each group most, and the last week day by day."""
    first = today - dt.timedelta(days=days - 1)
    work = {g: [0.0] * days for g in GROUPS}
    sets = {g: [0] * days for g in GROUPS}
    moves = {g: {} for g in GROUPS}
    sessions = []
    week = {today - dt.timedelta(days=k): {"sets": [], "trained": []} for k in range(7)}
    for e in entries:
        day = day_of(e["ts"])
        if not first <= day <= today:
            continue
        i = (day - first).days
        session = e.get("result") == "session" and e.get("activity") in catalog.ACTIVITIES
        if not session and not (e.get("result") in ("done", "manual") and did(e) > 0):
            continue
        if day in week:
            if session:
                week[day]["trained"].append(catalog.describe(e))
            else:
                week[day]["sets"].append((exercise_of(e), did(e)))
        if not session and catalog.is_stretch(exercise_of(e)):
            continue  # a hold stretches; it works nothing
        groups = {}
        for muscle, v in catalog.work_of(e).items():
            g = catalog.PART_OF[muscle]
            groups[g] = max(groups.get(g, 0.0), v)
        leans = leans_on(catalog.ACTIVITIES[e["activity"]]["parts"] if session else catalog.info(exercise_of(e))["parts"])
        for g in leans:
            work[g][i] += groups.get(g, 0.0)
        if session:
            sessions.append({"day": i, "what": catalog.describe(e), "groups": leans})
            continue
        name = "push-ups" if exercise_of(e) in PUSH_UPS else exercise_of(e)
        for g in leans:
            sets[g][i] += 1
            moves[g][name] = moves[g].get(name, 0) + 1
    recent = [{"date": d.isoformat(), "sets": len(w["sets"]), "lines": [*w["trained"], *catalog.day_lines(w["sets"])]}
              for d, w in week.items()]
    return {"first": first.isoformat(), "days": days, "work": {g: [round(v, 1) for v in work[g]] for g in GROUPS},
            "sets": sets, "sessions": sessions, "recent": recent,
            "moves": {g: max(moves[g], key=moves[g].get) if moves[g] else "" for g in GROUPS}}


def build(entries, day_of, today, days=30):
    """Everything the receipts draw, as plain JSON."""
    first = today - dt.timedelta(days=days - 1)
    main = main_move([e for e in entries if day_of(e["ts"]) >= first])
    per_day = {first + dt.timedelta(days=i): {"sets": 0, "skips": 0, "reps": 0, "best": 0, "main_sets": 0}
               for i in range(days)}
    moves = {}
    today_events = []
    week_start = today - dt.timedelta(days=6)
    for e in entries:
        day = day_of(e["ts"])
        if day < first or day > today:
            continue
        name, n, o = exercise_of(e), did(e), outcome(e)
        slot = per_day[day]
        if n > 0:
            slot["sets"] += 1
            counted = "push-ups" if name in PUSH_UPS else name
            if counted == main:
                slot["reps"] += n
                slot["best"] = max(slot["best"], n)
                slot["main_sets"] += 1
            m = moves.setdefault(name, {"exercise": name, "sets7": 0, "total7": 0, "sets30": 0, "best": 0,
                                        "last": 0, "days": {}})
            m["sets30"] += 1
            m["best"] = max(m["best"], n)
            m["last"] = n
            m["days"][day] = max(m["days"].get(day, 0), n)
            if day >= week_start:
                m["sets7"] += 1
                m["total7"] += n
        elif o == "skipped":
            slot["skips"] += 1
        if day == today and (n > 0 or o == "skipped"):
            today_events.append({"t": e["ts"], "exercise": name, "did": n, "goal": e.get("reps", 0),
                                 "result": "manual" if e.get("result") == "manual" else o})
    out_days = []
    for i in range(days):
        d = first + dt.timedelta(days=i)
        out_days.append({"date": d.isoformat(), **per_day[d]})
    rows = []
    for m in sorted(moves.values(), key=lambda m: (-m["sets7"], -m["sets30"])):
        trend = [m["days"].get(today - dt.timedelta(days=13 - i), 0) for i in range(14)]
        rows.append({k: m[k] for k in ("exercise", "sets7", "total7", "sets30", "best", "last")} | {"trend": trend})
    prev_end = week_start - dt.timedelta(days=1)
    return {
        "today": today_events,
        "days": out_days,
        "week": cards(entries, day_of, week_start, today),
        "last_week": cards(entries, day_of, prev_end - dt.timedelta(days=6), prev_end),
        "momentum": momentum(entries),
        "main": main,
        "moves": rows,
        "timeline": timeline(entries, day_of, today),
    }

