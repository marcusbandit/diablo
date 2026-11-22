"""Every exercise Marcus can log, each under one correct name.

The id is what goes into the pushup inbox. Where pushup's catalog (pushup/catalog.py) knows the
move, the id is its key, so pushup maps it onto the muscles; the rest are named the way a gym
names them. A name typed any way ("pushup", "Push-up", "push ups") resolves to one id, and an id
never has a near duplicate beside it.
"""

import difflib
import re

GROUPS = ("push", "pull", "legs", "core", "cardio")

# id, name, group, timed, aliases
LIBRARY = (
    ("push-ups", "Push-ups", "push", False, ("press-ups",)),
    ("decline push-ups", "Decline push-ups", "push", False, ("feet-up push-ups", "feet up push-ups")),
    ("incline push-ups", "Incline push-ups", "push", False, ()),
    ("diamond push-ups", "Diamond push-ups", "push", False, ("close-grip push-ups", "triangle push-ups")),
    ("wide push-ups", "Wide push-ups", "push", False, ("wide-grip push-ups",)),
    ("archer push-ups", "Archer push-ups", "push", False, ()),
    ("knee push-ups", "Knee push-ups", "push", False, ()),
    ("pseudo planche push-ups", "Pseudo planche push-ups", "push", False, ("planche push-ups",)),
    ("pike push-ups", "Pike push-ups", "push", False, ("pikes",)),
    ("handstand push-ups", "Handstand push-ups", "push", False, ("hspu",)),
    ("handstand hold", "Handstand hold", "push", True, ("handstand", "wall handstand")),
    ("chair dips", "Chair dips", "push", False, ("bench dips",)),
    ("assisted dips", "Assisted dips", "push", False, ()),
    ("dips", "Dips", "push", False, ("parallel bar dips", "bar dips")),
    ("bench press", "Bench press", "push", False, ("barbell bench press", "flat bench")),
    ("incline bench press", "Incline bench press", "push", False, ()),
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
    ("body rows", "Body rows", "pull", False, ("rows", "inverted rows", "australian pull-ups", "table rows")),
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
    ("sit-ups", "Sit-ups", "core", False, ()),
    ("crunches", "Crunches", "core", False, ()),
    ("bicycle crunches", "Bicycle crunches", "core", False, ("bicycles",)),
    ("leg raises", "Leg raises", "core", False, ("lying leg raises",)),
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
    ("hollow hold", "Hollow hold", "core", True, ("hollow body hold",)),
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

# pushup's own key for a move it knows under another spelling.
PUSHUP_KEY = {"l-sit": "L-sit"}

DEFAULTS = ("push-ups", "squats", "body rows", "plank", "lunges", "sit-ups", "glute bridges", "burpees")

BY_ID = {row[0]: {"id": row[0], "name": row[1], "group": row[2], "timed": row[3]} for row in LIBRARY}
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


def resolve(text):
    """The id a typed name means, or None. 'pushup', 'Push-up' and 'push ups' are all push-ups."""
    key = squash(text)
    if not key:
        return None
    if key in INDEX:
        return INDEX[key]
    return INDEX.get(key[:-1]) if key.endswith("s") else None


def is_deictic(text):
    return " ".join(str(text or "").lower().split()) in DEICTIC


def entry(ident):
    return dict(BY_ID[ident]) if ident in BY_ID else None


def stored(ident):
    """The name pushup's inbox reader knows the move under."""
    return PUSHUP_KEY.get(ident, ident)


def name_of(raw):
    """How an exercise shows, for anything ever logged: the library's name, or the old text tidied."""
    ident = resolve(raw)
    if ident:
        return BY_ID[ident]["name"]
    text = " ".join(str(raw or "").split())
    return text[:1].upper() + text[1:] if text else "Exercise"


def timed(raw):
    ident = resolve(raw)
    return bool(ident and BY_ID[ident]["timed"])


def search(query, limit=12):
    """Exercises for a typed query, best first: exact names, then names that start with it, then
    a word that starts with it, then anywhere in it, then close spellings."""
    key = squash(query)
    if not key:
        return []
    words = [w for w in re.split(r"[^a-z0-9]+", str(query).lower()) if w]
    exact = resolve(query)
    scored = []
    for ident, name, _group, _timed, aliases in LIBRARY:
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
    """The full list for the app's search, with the other names each is known by."""
    return [dict(entry(row[0]), aliases=list(row[4])) for row in LIBRARY]
