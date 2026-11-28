import datetime as dt
import time

import catalog

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_NAMES = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}

DAY_TYPES = ("push", "pull", "legs", "core", "planche", "lever", "full", "climbing", "gym", "rest")

DAY_LABELS = {
    "push": "Push day",
    "pull": "Pull day",
    "legs": "Legs day",
    "core": "Core day",
    "planche": "Planche day",
    "lever": "Front lever day",
    "full": "Full body day",
    "climbing": "Climbing day",
    "gym": "Gym day",
    "rest": "Rest day",
}

DEFAULT_SPLIT = {
    "mon": "push",
    "tue": "core",
    "wed": "pull",
    "thu": "legs",
    "fri": "push",
    "sat": "core",
    "sun": "rest",
}

FOCUS_MOVES = {
    "push": (
        "planche lean", "pseudo planche push-ups", "wide push-ups", "diamond push-ups",
        "push-ups", "decline push-ups", "incline push-ups", "pike push-ups",
        "frog stand", "tuck planche", "chair dips", "assisted dips",
    ),
    "pull": (
        "inverted rows", "wide inverted rows", "scapular rows", "tuck front lever",
        "assisted pull-ups",
    ),
    "core": (
        "lying leg raises", "hollow hold", "hollow rocks", "dragon flag negatives",
        "dead bugs", "flutter kicks", "v-ups", "compression hold", "seated pike ups",
        "side plank", "plank", "L-sit", "sit-ups", "supermen", "mountain climbers",
    ),
    "legs": (
        "bulgarian split squats", "squats", "lunges", "cossack squats",
        "step-ups", "glute bridges", "calf raises", "wall sit", "squat jumps",
    ),
    "planche": (
        "planche lean", "pseudo planche push-ups", "frog stand", "tuck planche",
        "pike push-ups", "diamond push-ups", "decline push-ups", "hollow hold", "compression hold",
    ),
    "lever": (
        "tuck front lever", "scapular rows", "inverted rows", "wide inverted rows",
        "dragon flag negatives", "lying leg raises", "hollow hold", "hollow rocks",
    ),
}

FOCUS_PARTS = {
    "push": ("push",),
    "pull": ("pull",),
    "core": ("core",),
    "legs": ("legs",),
    "planche": ("push", "core"),
    "lever": ("pull", "core"),
    "full": ("push", "pull", "legs", "core"),
}


def normalize_split(raw):
    out = dict(DEFAULT_SPLIT)
    if isinstance(raw, dict):
        for day in WEEKDAYS:
            val = str(raw.get(day, "")).strip().lower()
            if val in DAY_TYPES:
                out[day] = val
    return out


def normalize_goals(raw):
    if not isinstance(raw, list):
        return list(catalog.DEFAULT_GOALS)
    out = [str(g).strip().lower() for g in raw if str(g).strip().lower() in catalog.TRAINING_GOALS]
    return out or list(catalog.DEFAULT_GOALS)


def active_hurts(entries, day, day_of):
    muscles = {}
    tendons = {}
    parts = {}
    ordered = [e for e in entries if e.get("result") in ("hurt", "healed") and day_of(e["ts"]) <= day]
    for e in ordered:
        d = day_of(e["ts"])
        result = e["result"]
        raw_spots = e.get("spots") or []
        if raw_spots:
            for s in raw_spots:
                if not isinstance(s, (list, tuple)) or len(s) < 2:
                    continue
                name, side = s[0], s[1]
                inf = catalog.spot_info(name)
                if not inf:
                    continue
                kind = s[2] if len(s) >= 3 and s[2] in ("muscle", "tendon") else inf["kind"]
                bucket = tendons if kind == "tendon" else muscles
                key = (inf["name"], side)
                if result == "hurt":
                    prev = bucket.get(key)
                    streak = (prev["streak"] + 1) if prev and prev["day"] == d - dt.timedelta(days=1) else (prev["streak"] if prev and prev["day"] == d else 1)
                    bucket[key] = {"name": inf["name"], "label": inf["label"], "side": side, "kind": kind, "part": inf["part"], "day": d, "streak": streak, "recovery_days": inf["recovery_days"]}
                else:
                    bucket.pop(key, None)
        else:
            part = e.get("part")
            if part in catalog.HURT_PARTS:
                if result == "hurt":
                    prev = parts.get(part)
                    streak = (prev["streak"] + 1) if prev and prev["day"] == d - dt.timedelta(days=1) else (prev["streak"] if prev and prev["day"] == d else 1)
                    parts[part] = {"part": part, "day": d, "streak": streak}
                else:
                    parts.pop(part, None)
                    for bucket in (muscles, tendons):
                        for k in [k for k, v in bucket.items() if v["part"] == part]:
                            bucket.pop(k, None)
    active_m = [v for v in muscles.values() if 0 <= (day - v["day"]).days <= v["recovery_days"]]
    active_t = [v for v in tendons.values() if 0 <= (day - v["day"]).days <= v["recovery_days"]]
    active_p = [v for v in parts.values() if 0 <= (day - v["day"]).days <= 1]
    return {"muscles": active_m, "tendons": active_t, "parts": active_p}


def tendon_blocks(exercise, hurt_tendons):
    e = catalog.info(exercise)
    ex_tendons = e.get("tendons", {})
    for h in hurt_tendons:
        if ex_tendons.get(h["name"], 0) >= 0.4:
            return h
    return None


def muscle_blocks(exercise, hurt_muscles):
    e = catalog.info(exercise)
    ex_muscles = e.get("muscles", {})
    for h in hurt_muscles:
        inf = catalog.spot_info(h["name"])
        base = inf.get("base", h["name"]) if inf else h["name"]
        if ex_muscles.get(base, 0) >= 0.7:
            return h
    return None


def is_rest_logged(entries, day, day_of):
    for e in reversed(entries):
        if day_of(e["ts"]) != day:
            continue
        if e.get("result") == "rest":
            return True
        if e.get("result") == "resume_day":
            return False
    return False


def recent_heavy_sessions(entries, now, hours=36):
    out = []
    for e in catalog.sessions(entries):
        age = now - e["ts"]
        if 0 <= age <= hours * 3600 and catalog.is_heavy_entry(e):
            out.append(e)
    return out


def exercise_fits_focus(name, focus):
    if focus == "full":
        return True
    if name in FOCUS_MOVES.get(focus, ()):
        return True
    e = catalog.info(name)
    want_parts = FOCUS_PARTS.get(focus, ())
    if not want_parts:
        return False
    top = max((e["parts"].get(p, 0) for p in catalog.HURT_PARTS), default=0)
    return top > 0 and any(e["parts"].get(p, 0) >= 0.8 * top for p in want_parts)


def goal_score(name, goals):
    e = catalog.info(name)
    ex_goals = set(e.get("goals", ()))
    overlap = len(ex_goals & set(goals))
    return 1.0 + 0.55 * overlap


def recovery_summary(entries, now):
    p_load = catalog.loads(entries, now)
    t_load = catalog.tendon_loads(entries, now)
    sore_parts = [p for p in catalog.HURT_PARTS if p_load.get(p, 0) >= catalog.SORE_LOAD]
    sore_tendons = [t for t, v in t_load.items() if v >= catalog.TENDON_SORE_LOAD]
    return {"parts": p_load, "tendons": t_load, "sore_parts": sore_parts, "sore_tendons": sore_tendons}


def day_plan(cfg, entries, now=None, day_of=None):
    now = time.time() if now is None else now
    if day_of is None:
        day_of = lambda ts: (dt.datetime.fromtimestamp(ts) - dt.timedelta(hours=7)).date()
    day = day_of(now)
    weekday = WEEKDAYS[day.weekday()]
    split = normalize_split(cfg.get("split"))
    goals = normalize_goals(cfg.get("goals"))
    gear = list(cfg.get("gear", catalog.DEFAULT_GEAR))
    scheduled = split[weekday]
    rec = recovery_summary(entries, now)
    p_load, t_load = rec["parts"], rec["tendons"]
    hurts = active_hurts(entries, day, day_of)
    heavy_recent = recent_heavy_sessions(entries, now, hours=36)
    trained_today = [e for e in catalog.sessions(entries) if day_of(e["ts"]) == day and catalog.training_day(e)]

    base = {
        "day": day.isoformat(),
        "weekday": weekday,
        "scheduled": scheduled,
        "focus": scheduled,
        "label": DAY_LABELS.get(scheduled, scheduled.capitalize()),
        "reason": "",
        "rest": False,
        "gentle": False,
        "sore_parts": rec["sore_parts"],
        "sore_tendons": rec["sore_tendons"],
        "hurting_muscles": hurts["muscles"],
        "hurting_tendons": hurts["tendons"],
        "goals": goals,
        "exercises": [],
    }

    if is_rest_logged(entries, day, day_of):
        return {**base, "focus": "rest", "label": "Rest day", "reason": "You marked today as a rest day", "rest": True, "gentle": True}

    if trained_today:
        what = ", ".join(catalog.describe(e) for e in trained_today)
        return {**base, "focus": "rest", "label": "Trained today", "reason": f"Already logged {what} today", "rest": True, "gentle": True}

    if scheduled in ("rest", "climbing", "gym"):
        why = {
            "rest": f"{WEEKDAY_NAMES[weekday]} is a scheduled rest day",
            "climbing": f"{WEEKDAY_NAMES[weekday]} is scheduled for climbing",
            "gym": f"{WEEKDAY_NAMES[weekday]} is scheduled for the gym",
        }[scheduled]
        return {**base, "focus": "rest", "label": DAY_LABELS[scheduled], "reason": why, "rest": True, "gentle": True}

    if max((p_load.get(p, 0) for p in catalog.HURT_PARTS), default=0) >= catalog.REST_LOAD or max(t_load.values(), default=0) >= 1.35:
        heaviest = max(heavy_recent, key=lambda e: catalog.session_load(e) * catalog.faded(e, now), default=None)
        what = catalog.describe(heaviest) if heaviest else "heavy training"
        return {**base, "focus": "rest", "label": "Recovery day", "reason": f"Recovering from {what}", "rest": True, "gentle": True}

    usable = [e for e in cfg.get("exercises", catalog.DEFAULT_EXERCISES) if catalog.can_do(e, gear)]
    if not usable:
        usable = [e for e in catalog.DEFAULT_EXERCISES if catalog.can_do(e, gear)] or ["push-ups"]

    def safe_for(focus_name):
        out = []
        for e in usable:
            if not exercise_fits_focus(e, focus_name):
                continue
            if catalog.sore(e, p_load, t_load):
                continue
            if tendon_blocks(e, hurts["tendons"]):
                continue
            if muscle_blocks(e, hurts["muscles"]):
                continue
            out.append(e)
        return out

    focus = scheduled
    gentle = bool(heavy_recent or hurts["tendons"] or hurts["muscles"])
    reason = ""
    pool = safe_for(focus)

    if not pool:
        alt_order = ["core", "legs", "push", "pull"]
        if focus in alt_order:
            alt_order.remove(focus)
        for alt in alt_order:
            cand = safe_for(alt)
            if cand:
                heaviest = max(heavy_recent, key=lambda e: catalog.session_load(e) * catalog.faded(e, now), default=None)
                if hurts["tendons"]:
                    t_names = ", ".join(sorted({h["label"].lower() for h in hurts["tendons"]}))
                    reason = f"Shifted from {DAY_LABELS[scheduled].lower()} to protect your {t_names}"
                elif heaviest:
                    reason = f"Shifted from {DAY_LABELS[scheduled].lower()} while recovering from {catalog.describe(heaviest)}"
                else:
                    reason = f"Shifted from {DAY_LABELS[scheduled].lower()} to fresh muscles"
                focus = alt
                pool = cand
                gentle = True
                break

    if not pool:
        heaviest = max(heavy_recent, key=lambda e: catalog.session_load(e) * catalog.faded(e, now), default=None)
        why = f"Recovering from {catalog.describe(heaviest)}" if heaviest else "Resting sore muscles and tendons"
        return {**base, "focus": "rest", "label": "Recovery day", "reason": why, "rest": True, "gentle": True}

    if not reason and heavy_recent:
        heaviest = max(heavy_recent, key=lambda e: catalog.session_load(e) * catalog.faded(e, now))
        reason = f"Gentle day after {catalog.describe(heaviest)}; passes never count as skips"

    recent_done = [
        catalog.exercise_of(e) for e in entries
        if catalog.did(e) > 0 and 0 <= now - e["ts"] < 24 * 3600 and not catalog.is_stretch(catalog.exercise_of(e))
    ]
    last_ex = recent_done[-1] if recent_done else None
    week_work = catalog.muscle_week(entries, now)[0]

    def rank_key(name):
        gs = goal_score(name, goals)
        bw = catalog.balance_weight(name, week_work)
        rep_pen = 1.0 / (1.0 + recent_done.count(name))
        last_pen = 0.25 if name == last_ex and len(pool) > 1 else 1.0
        focus_list = FOCUS_MOVES.get(focus, ())
        order_bonus = 1.2 if name in focus_list[:4] else 1.0
        return -(gs * bw * rep_pen * last_pen * order_bonus)

    ranked = sorted(pool, key=rank_key)
    label = DAY_LABELS.get(focus, focus.capitalize())
    if focus == "push" and "planche" in goals:
        label = "Push day · Planche focus"
    elif focus == "pull" and "front_lever" in goals:
        label = "Pull day · Front lever focus"
    elif focus == "core" and ("planche" in goals or "front_lever" in goals):
        label = "Core day · Abs and levers"

    return {
        **base,
        "focus": focus,
        "label": label,
        "reason": reason,
        "rest": False,
        "gentle": gentle,
        "exercises": ranked,
    }
