import difflib
import json
import re
from pathlib import Path

GROUPS = ("push", "pull", "legs", "core", "cardio")

LIBRARY = (
    ("push-ups", "Push-ups", "push", False, ("press-ups", "standard push-ups", "regular push-ups")),
    ("wide push-ups", "Wide push-ups", "push", False, ("wide-grip push-ups", "wide hand push-ups")),
    ("diamond push-ups", "Diamond push-ups", "push", False, ("close-grip push-ups", "close push-ups", "narrow push-ups", "triangle push-ups")),
    ("incline push-ups", "Incline push-ups", "push", False, ("inclines", "hands elevated push-ups", "elevated push-ups")),
    ("decline push-ups", "Decline push-ups", "push", False, ("feet-up push-ups", "feet up push-ups")),
    ("archer push-ups", "Archer push-ups", "push", False, ()),
    ("knee push-ups", "Knee push-ups", "push", False, ()),
    ("planche lean", "Planche lean", "push", True, ("planche leans", "forward lean")),
    ("pseudo planche push-ups", "Pseudo planche push-ups", "push", False, ("planche push-ups", "pppu")),
    ("frog stand", "Frog stand", "push", True, ("crow pose", "frog balance")),
    ("tuck planche", "Tuck planche", "push", True, ("hand lever", "planche", "planche hold")),
    ("pike push-ups", "Pike push-ups", "push", False, ("pikes",)),
    ("handstand push-ups", "Handstand push-ups", "push", False, ("hspu",)),
    ("handstand hold", "Handstand hold", "push", True, ("handstand", "wall handstand")),
    ("chair dips", "Chair dips", "push", False, ("bench dips",)),
    ("assisted dips", "Assisted dips", "push", False, ()),
    ("dips", "Dips", "push", False, ("parallel bar dips", "bar dips")),
    ("bench press", "Bench press", "push", False, ("barbell bench press", "flat bench")),
    ("incline bench press", "Incline bench press", "push", False, ("incline press",)),
    ("dumbbell bench press", "Dumbbell bench press", "push", False, ("db bench press",)),
    ("overhead press", "Overhead press", "push", False, ("ohp", "military press", "shoulder press")),
    ("dumbbell shoulder press", "Dumbbell shoulder press", "push", False, ("db shoulder press",)),
    ("lateral raises", "Lateral raises", "push", False, ("side raises", "lat raises")),
    ("front raises", "Front raises", "push", False, ()),
    ("triceps extensions", "Triceps extensions", "push", False, ("overhead triceps extensions", "tricep extensions")),
    ("skull crushers", "Skull crushers", "push", False, ("lying triceps extensions",)),
    ("triceps pushdowns", "Triceps pushdowns", "push", False, ("tricep pushdowns", "cable pushdowns")),
    ("chest flyes", "Chest flyes", "push", False, ("chest flies", "dumbbell flyes", "pec flyes")),
    ("pull-ups", "Pull-ups", "pull", False, ()),
    ("chin-ups", "Chin-ups", "pull", False, ("chins",)),
    ("assisted pull-ups", "Assisted pull-ups", "pull", False, ("band pull-ups",)),
    ("negative pull-ups", "Negative pull-ups", "pull", False, ("pull-up negatives",)),
    ("scapular pull-ups", "Scapular pull-ups", "pull", False, ("scap pulls", "scapula pull-ups")),
    ("dead hang", "Dead hang", "pull", True, ("hang", "bar hang")),
    ("inverted rows", "Inverted rows", "pull", False, ("body rows", "rows", "supine rows", "australian pull-ups", "table rows")),
    ("wide inverted rows", "Wide inverted rows", "pull", False, ("wide rows", "wide table rows")),
    ("scapular rows", "Scapular rows", "pull", False, ("scap rows", "table scap pulls")),
    ("tuck front lever", "Tuck front lever", "pull", True, ("front lever", "lever hanging", "table front lever", "hanging lever")),
    ("barbell rows", "Barbell rows", "pull", False, ("bent-over rows", "bent over rows")),
    ("dumbbell rows", "Dumbbell rows", "pull", False, ("one-arm rows", "db rows")),
    ("lat pulldowns", "Lat pulldowns", "pull", False, ("pulldowns",)),
    ("seated cable rows", "Seated cable rows", "pull", False, ("cable rows",)),
    ("face pulls", "Face pulls", "pull", False, ()),
    ("biceps curls", "Biceps curls", "pull", False, ("bicep curls", "curls", "dumbbell curls")),
    ("hammer curls", "Hammer curls", "pull", False, ()),
    ("shrugs", "Shrugs", "pull", False, ()),
    ("deadlifts", "Deadlifts", "pull", False, ("deadlift",)),
    ("supermen", "Supermen", "pull", False, ("superman", "supermans")),
    ("squats", "Squats", "legs", False, ("air squats", "bodyweight squats")),
    ("back squats", "Back squats", "legs", False, ("barbell squats",)),
    ("front squats", "Front squats", "legs", False, ()),
    ("goblet squats", "Goblet squats", "legs", False, ()),
    ("pistol squats", "Pistol squats", "legs", False, ("pistols",)),
    ("squat jumps", "Squat jumps", "legs", False, ("jump squats",)),
    ("lunges", "Lunges", "legs", False, ("forward lunges",)),
    ("reverse lunges", "Reverse lunges", "legs", False, ()),
    ("walking lunges", "Walking lunges", "legs", False, ()),
    ("bulgarian split squats", "Bulgarian split squats", "legs", False, ("bulgarians", "bss", "split squats")),
    ("cossack squats", "Cossack squats", "legs", False, ()),
    ("step-ups", "Step-ups", "legs", False, ("box step-ups",)),
    ("box jumps", "Box jumps", "legs", False, ()),
    ("wall sit", "Wall sit", "legs", True, ("wall squat",)),
    ("glute bridges", "Glute bridges", "legs", False, ("bridges", "hip bridges")),
    ("hip thrusts", "Hip thrusts", "legs", False, ("barbell hip thrusts",)),
    ("romanian deadlifts", "Romanian deadlifts", "legs", False, ("rdl", "rdls", "stiff-leg deadlifts")),
    ("nordic curls", "Nordic curls", "legs", False, ("nordics", "nordic hamstring curls")),
    ("leg press", "Leg press", "legs", False, ()),
    ("leg extensions", "Leg extensions", "legs", False, ()),
    ("leg curls", "Leg curls", "legs", False, ("hamstring curls",)),
    ("calf raises", "Calf raises", "legs", False, ("calves", "heel raises")),
    ("lying leg raises", "Lying leg raises", "core", False, ("leg raises", "straight leg raises", "floor leg raises", "leg lifts", "lying straight leg raises")),
    ("hollow hold", "Hollow hold", "core", True, ("hollow body hold",)),
    ("hollow rocks", "Hollow rocks", "core", False, ("hollow body rocks",)),
    ("dragon flag negatives", "Dragon flag negatives", "core", False, ("dragon flags", "dragon flag")),
    ("sit-ups", "Sit-ups", "core", False, ()),
    ("crunches", "Crunches", "core", False, ()),
    ("bicycle crunches", "Bicycle crunches", "core", False, ("bicycles",)),
    ("hanging leg raises", "Hanging leg raises", "core", False, ()),
    ("hanging knee raises", "Hanging knee raises", "core", False, ("knee raises",)),
    ("russian twists", "Russian twists", "core", False, ()),
    ("v-ups", "V-ups", "core", False, ("v ups", "jackknives")),
    ("flutter kicks", "Flutter kicks", "core", False, ()),
    ("dead bugs", "Dead bugs", "core", False, ()),
    ("bird dogs", "Bird dogs", "core", False, ()),
    ("ab wheel rollouts", "Ab wheel rollouts", "core", False, ("ab rollouts", "rollouts", "ab wheel")),
    ("plank", "Plank", "core", True, ("front plank", "forearm plank")),
    ("side plank", "Side plank", "core", True, ()),
    ("compression hold", "Compression hold", "core", True, ("compression", "leg lift hold")),
    ("seated pike ups", "Seated pike-ups", "core", False, ("pike ups",)),
    ("l-sit", "L-sit", "core", True, ()),
    ("burpees", "Burpees", "cardio", False, ()),
    ("jumping jacks", "Jumping jacks", "cardio", False, ("star jumps",)),
    ("mountain climbers", "Mountain climbers", "cardio", False, ()),
    ("high knees", "High knees", "cardio", False, ()),
    ("kettlebell swings", "Kettlebell swings", "cardio", False, ("kb swings", "swings")),
    ("jump rope", "Jump rope", "cardio", False, ("skipping", "rope skips", "skipping rope")),
)

POINTS = {
    "push-ups": "Mid chest, triceps, and front shoulders. Balanced push base.",
    "wide push-ups": "Outer chest and front shoulders. Wider hands emphasize pecs over triceps.",
    "diamond push-ups": "Triceps, inner chest, and front delts. Close hands build lockout power.",
    "incline push-ups": "Lower chest and light triceps. Easier angle for volume or recovery.",
    "decline push-ups": "Upper chest and front shoulders. Feet up loads the arms harder.",
    "planche lean": "Front delts, straight-arm biceps tendon, and core. Foundation for the planche.",
    "pseudo planche push-ups": "Front delts, chest, and core with hands by your hips. Direct planche strength.",
    "frog stand": "Balance, wrists, and front delts. First step to holding feet off the floor.",
    "tuck planche": "Straight-arm protraction and tucked core. Real feet-off-floor planche hold.",
    "pike push-ups": "Front and side shoulders plus triceps. Vertical pressing strength.",
    "inverted rows": "Lats, mid back, and biceps under your table. Builds front lever pull strength.",
    "wide inverted rows": "Upper back, rhomboids, and rear delts under your table.",
    "scapular rows": "Straight-arm shoulder blade retraction. Builds the lat lock for front lever.",
    "tuck front lever": "Lats and abs holding horizontal under the table. Direct front lever hold.",
    "lying leg raises": "Lower abs and hip flexors. Straight-leg lifts for core and front lever.",
    "hollow hold": "Deep abs and lower back brace. The bodyline for planche and front lever.",
    "hollow rocks": "Dynamic abs and hollow bodyline control.",
    "dragon flag negatives": "Full straight-body core strength holding a table leg.",
    "dead bugs": "Deep core stability with zero tendon load.",
    "flutter kicks": "Lower abs and hip flexor endurance.",
    "v-ups": "Full upper and lower abs compression.",
    "bulgarian split squats": "Single-leg quads, glutes, and balance using a chair.",
    "cossack squats": "Inner thighs, quads, and hip opening for climbing.",
}

PUSHUP_KEY = {"l-sit": "L-sit"}

DEFAULTS = ("push-ups", "wide push-ups", "lying leg raises", "inverted rows", "planche lean", "hollow hold", "bulgarian split squats", "squats")

BY_ID = {
    row[0]: {"id": row[0], "name": row[1], "group": row[2], "timed": row[3], **({"point": POINTS[row[0]]} if row[0] in POINTS else {})}
    for row in LIBRARY
}
CUSTOM_BY_ID = {}
DEICTIC = {"these", "this", "those", "that", "them", "it", "same", "the same", "same again", "again", "more", "more of these"}


def squash(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _forms(key):
    out = {key}
    if key.endswith("es"):
        out.add(key[:-2])
    if key.endswith("s"):
        out.add(key[:-1])
    out.update({key + "s", key + "es"})
    return out


def _index():
    index = {}
    for ident, name, _group, _timed, aliases in LIBRARY:
        for text in (ident, name, PUSHUP_KEY.get(ident, ""), *aliases):
            if not text:
                continue
            for form in _forms(squash(text)):
                index.setdefault(form, set()).add(ident)
    return {key: next(iter(ids)) for key, ids in index.items() if len(ids) == 1}


INDEX = _index()

PHRASE_HINTS = (
    (("back", "straight", "leg"), "lying leg raises"),
    (("lying", "legs", "up", "down"), "lying leg raises"),
    (("floor", "legs", "up", "down"), "lying leg raises"),
    (("push", "without", "feet"), "tuck planche"),
    (("hand", "lever"), "tuck planche"),
    (("hanging", "lever"), "tuck front lever"),
    (("lever", "table"), "tuck front lever"),
    (("close", "push"), "diamond push-ups"),
    (("wide", "push"), "wide push-ups"),
    (("incline", "push"), "incline push-ups"),
    (("decline", "push"), "decline push-ups"),
)


def infer_group(name):
    low = " ".join(str(name or "").lower().split())
    if any(w in low for w in ("push", "bench", "press", "dip", "fly", "tricep", "chest", "pec", "shoulder", "delt", "planche", "handstand")):
        return "push"
    if any(w in low for w in ("pull", "chin", "row", "lat", "curl", "bicep", "lever", "hang", "shrug", "forearm", "grip", "climb")):
        return "pull"
    if any(w in low for w in ("squat", "lunge", "leg press", "leg ext", "leg curl", "deadlift", "rdl", "glute", "calf", "calves", "hip thrust", "nordic", "step-up", "wall sit")):
        return "legs"
    if any(w in low for w in ("ab", "core", "plank", "hollow", "crunch", "leg raise", "sit-up", "twist", "oblique", "dragon", "dead bug", "flutter", "v-up", "compression", "pike up", "rollout")):
        return "core"
    if any(w in low for w in ("run", "jump", "burpee", "cardio", "hiit", "swing", "rope", "climber", "sprint")):
        return "cardio"
    return "push"


def infer_timed(name):
    low = " ".join(str(name or "").lower().split())
    return any(w in low for w in ("hold", "plank", "hang", "sit", "stand", "lean", "lever"))


def clean_id(text):
    return " ".join(str(text or "").strip().lower().split())


def display_name(text):
    t = " ".join(str(text or "").strip().split())
    return t[:1].upper() + t[1:] if t else "Exercise"


def load_custom(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for ident, meta in raw.items():
        if isinstance(ident, str) and isinstance(meta, dict):
            cid = clean_id(ident)
            out[cid] = {
                "id": cid,
                "name": display_name(meta.get("name") or cid),
                "group": meta.get("group") if meta.get("group") in GROUPS else infer_group(cid),
                "timed": bool(meta.get("timed")) if "timed" in meta else infer_timed(cid),
                "custom": True,
                **({"point": meta["point"]} if meta.get("point") else {}),
                **({"muscles": meta["muscles"]} if isinstance(meta.get("muscles"), dict) else {}),
                **({"tendons": meta["tendons"]} if isinstance(meta.get("tendons"), dict) else {}),
            }
    CUSTOM_BY_ID.update(out)
    return out


def register_custom(text, path=None, group=None, timed=None, muscles=None, tendons=None, point=None):
    cid = clean_id(text)
    if not cid:
        return None
    item = {
        "id": cid,
        "name": display_name(text),
        "group": group if group in GROUPS else infer_group(cid),
        "timed": bool(timed) if timed is not None else infer_timed(cid),
        "custom": True,
    }
    if point:
        item["point"] = str(point)
    if isinstance(muscles, dict) and muscles:
        item["muscles"] = muscles
    if isinstance(tendons, dict) and tendons:
        item["tendons"] = tendons
    CUSTOM_BY_ID[cid] = item
    if path is not None:
        p = Path(path)
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text())
            except (OSError, ValueError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        data[cid] = item
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n")
    return dict(item)


def resolve(text):
    key = squash(text)
    if not key:
        return None
    if key in INDEX:
        return INDEX[key]
    if key.endswith("s") and key[:-1] in INDEX:
        return INDEX[key[:-1]]
    cid = clean_id(text)
    if cid in CUSTOM_BY_ID:
        return cid
    for custom_id in CUSTOM_BY_ID:
        if squash(custom_id) == key:
            return custom_id
    words = set(re.findall(r"[a-z]+", str(text or "").lower()))
    stems = words | {w[:-1] for w in words if len(w) > 3 and w.endswith("s")}
    for needed, target in PHRASE_HINTS:
        if all(w in stems for w in needed):
            return target
    return None


def is_deictic(text):
    return " ".join(str(text or "").lower().split()) in DEICTIC


def entry(ident):
    if ident in BY_ID:
        return dict(BY_ID[ident])
    if ident in CUSTOM_BY_ID:
        return dict(CUSTOM_BY_ID[ident])
    return None


def stored(ident):
    return PUSHUP_KEY.get(ident, ident)


def name_of(raw):
    ident = resolve(raw)
    if ident:
        got = entry(ident)
        if got:
            return got["name"]
    text = " ".join(str(raw or "").split())
    return text[:1].upper() + text[1:] if text else "Exercise"


def timed(raw):
    ident = resolve(raw)
    got = entry(ident) if ident else None
    return bool(got and got.get("timed"))


def search(query, limit=12):
    key = squash(query)
    if not key:
        return []
    words = [w for w in re.split(r"[^a-z0-9]+", str(query).lower()) if w]
    exact = resolve(query)
    scored = []
    rows = [*LIBRARY, *((c["id"], c["name"], c["group"], c["timed"], ()) for c in CUSTOM_BY_ID.values() if c["id"] not in BY_ID)]
    for ident, name, _group, _timed, aliases in rows:
        names = [name, *aliases]
        keys = [squash(n) for n in names]
        if ident == exact:
            rank = 0
        elif keys[0].startswith(key):
            rank = 1
        elif any(k.startswith(key) for k in keys):
            rank = 1.5
        elif words and all(any(part.startswith(w) for n in names for part in re.split(r"[^a-z0-9]+", n.lower())) for w in words):
            rank = 2
        elif any(key in k for k in keys):
            rank = 3
        else:
            ratio = max(difflib.SequenceMatcher(None, key, k).ratio() for k in keys)
            if ratio < 0.72:
                continue
            rank = 4 - ratio
        scored.append((rank, len(name), name.lower(), ident))
    scored.sort()
    return [entry(row[-1]) for row in scored[:limit]]


def closest(text, limit=3):
    return [hit["name"] for hit in search(text, limit)]


def library():
    base = [dict(entry(row[0]), aliases=list(row[4])) for row in LIBRARY]
    extra = [dict(c, aliases=[]) for c in CUSTOM_BY_ID.values() if c["id"] not in BY_ID]
    return base + extra
