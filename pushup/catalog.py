"""What there is to do: the home exercises and stretches the card asks for, and the training you
log yourself.

Everything maps to muscles, 0 to 1 for how hard it leans on each, which is what the body map
shows. For recovery the muscles roll up into coarse parts: push, pull, legs, core, plus cardio. A
training session loads each part by its weight, its length and how hard it was, and that load
fades with a half-life of RECOVERY_HALF_LIFE. The card steers around sore parts, and after a
heavy day it only offers stretches.

The stretches follow the split progression David Thurin (Movement by David, Stay Flexy) teaches:
hamstrings first, then hip flexors, then both together, then the split itself. His own routines
sit behind his paid programs, so these are the standard stretches for each step, not his list.
"""

import math
import re

PART_OF = {
    "chest": "push", "shoulders": "push", "triceps": "push",
    "biceps": "pull", "forearms": "pull", "traps": "pull", "lats": "pull",
    "abs": "core", "obliques": "core", "lower back": "core", "hip flexors": "core",
    "glutes": "legs", "adductors": "legs", "quads": "legs", "hamstrings": "legs", "calves": "legs",
}
MUSCLES = tuple(PART_OF)

# scale: how many of these a push-up's worth is, so a level's 10 to 20 push-ups is 15 to 30
# squats. Timed ones count seconds. per: what one rep counts, when it isn't the whole body.
EXERCISES = {
    "push-ups": {"one": "push-up", "scale": 1, "muscles": {"chest": 1, "triceps": 0.8, "shoulders": 0.6, "abs": 0.4}},
    # Feet up on a chair: more of the weight on the arms, and the upper chest and shoulders take it.
    "decline push-ups": {"one": "decline push-up", "scale": 0.8,
                         "muscles": {"chest": 1, "shoulders": 0.8, "triceps": 0.8, "abs": 0.5}},
    "squats": {"one": "squat", "scale": 1.5,
               "muscles": {"quads": 1, "glutes": 0.8, "adductors": 0.4, "hamstrings": 0.3, "calves": 0.2}},
    "lunges": {"one": "lunge", "scale": 1.2,
               "muscles": {"quads": 1, "glutes": 0.8, "hamstrings": 0.4, "adductors": 0.3, "calves": 0.2}},
    "bulgarian split squats": {"one": "bulgarian split squat", "scale": 0.8, "per": "each leg",
                               "muscles": {"quads": 1, "glutes": 1, "hamstrings": 0.4, "adductors": 0.4,
                                           "hip flexors": 0.3, "abs": 0.2}},
    "burpees": {"one": "burpee", "scale": 0.5, "cardio": 1,
                "muscles": {"quads": 0.7, "chest": 0.6, "triceps": 0.5, "shoulders": 0.5, "abs": 0.5,
                            "glutes": 0.5, "calves": 0.4}},
    "squat jumps": {"one": "squat jump", "scale": 1, "cardio": 0.8,
                    "muscles": {"quads": 1, "glutes": 0.8, "calves": 0.7, "hamstrings": 0.3}},
    "step-ups": {"one": "step-up", "scale": 1.2, "per": "each leg",
                 "muscles": {"quads": 1, "glutes": 0.8, "hamstrings": 0.3, "calves": 0.3}},
    "calf raises": {"one": "calf raise", "scale": 2, "muscles": {"calves": 1}},
    "sit-ups": {"one": "sit-up", "scale": 1.2, "muscles": {"abs": 1, "hip flexors": 0.6, "obliques": 0.4}},
    "plank": {"one": "plank", "scale": 3, "timed": True,
              "muscles": {"abs": 1, "obliques": 0.5, "shoulders": 0.4, "glutes": 0.3}},
    "side plank": {"one": "side plank", "scale": 2, "timed": True, "per": "each side",
                   "muscles": {"obliques": 1, "abs": 0.5, "shoulders": 0.5, "glutes": 0.4, "adductors": 0.3}},
    "hollow hold": {"one": "hollow hold", "scale": 2, "timed": True, "muscles": {"abs": 1, "hip flexors": 0.6, "obliques": 0.4}},
    "glute bridges": {"one": "glute bridge", "scale": 1.5,
                      "muscles": {"glutes": 1, "hamstrings": 0.6, "lower back": 0.3}},
    "chair dips": {"one": "chair dip", "scale": 1, "muscles": {"triceps": 1, "chest": 0.5, "shoulders": 0.5}},
    "pike push-ups": {"one": "pike push-up", "scale": 0.7,
                      "muscles": {"shoulders": 1, "triceps": 0.7, "chest": 0.3, "traps": 0.3}},
    "jumping jacks": {"one": "jumping jack", "scale": 3, "cardio": 1,
                      "muscles": {"calves": 0.6, "shoulders": 0.4, "quads": 0.3, "adductors": 0.3, "glutes": 0.3}},
    "mountain climbers": {"one": "mountain climber", "scale": 2, "cardio": 0.7,
                          "muscles": {"abs": 0.8, "hip flexors": 0.6, "shoulders": 0.5, "quads": 0.4}},
    "cossack squats": {"one": "cossack squat", "scale": 1, "per": "both sides",
                       "muscles": {"adductors": 1, "quads": 0.8, "glutes": 0.7, "hamstrings": 0.3}},
    "wall sit": {"one": "wall sit", "scale": 3, "timed": True, "muscles": {"quads": 1, "glutes": 0.4, "adductors": 0.2}},
    "supermen": {"one": "superman", "scale": 1,
                 "muscles": {"lower back": 1, "glutes": 0.5, "traps": 0.4, "hamstrings": 0.3, "shoulders": 0.3}},
    # The bodyweight routine's pulling half. Assisted means the feet on a chair take some of the
    # weight; rows go under a table or a low bar with the heels on the floor.
    "assisted pull-ups": {"one": "assisted pull-up", "scale": 0.6,
                          "muscles": {"lats": 1, "biceps": 0.8, "forearms": 0.6, "traps": 0.5, "shoulders": 0.3,
                                      "abs": 0.2}},
    "assisted dips": {"one": "assisted dip", "scale": 0.8,
                      "muscles": {"triceps": 1, "chest": 0.9, "shoulders": 0.7, "abs": 0.2}},
    "body rows": {"one": "body row", "scale": 0.8,
                  "muscles": {"lats": 1, "traps": 0.8, "biceps": 0.7, "forearms": 0.5, "shoulders": 0.4,
                              "lower back": 0.2, "glutes": 0.2}},
    "seated pike ups": {"one": "seated pike up", "scale": 0.8,
                        "muscles": {"hip flexors": 1, "abs": 0.9, "quads": 0.4, "obliques": 0.3, "triceps": 0.3}},
    # The L-sit's floor version: sitting, hands on the floor by the knees, the straight legs lifted
    # off the floor and held. The seat stays down, so it needs no gear and no lockout strength.
    "compression hold": {"one": "compression hold", "scale": 1.5, "timed": True,
                         "muscles": {"hip flexors": 1, "abs": 0.9, "quads": 0.5, "obliques": 0.3, "triceps": 0.3}},
    # Legs held straight out in front, the body up on straight arms. A second of it is about a
    # push-up's worth, so the goals start where a first L-sit does, at 5 to 12 seconds.
    "L-sit": {"one": "L-sit", "scale": 1, "timed": True,
              "muscles": {"abs": 1, "hip flexors": 1, "triceps": 0.6, "quads": 0.5, "shoulders": 0.4, "traps": 0.3,
                          "obliques": 0.3}},
}
# Names people type for these: 'rows', 'pullups', 'dips'. Matched with spaces and hyphens taken out.
ALIASES = {"pushups": "push-ups", "pushup": "push-ups", "pullups": "assisted pull-ups", "pullup": "assisted pull-ups",
           "rows": "body rows", "row": "body rows", "invertedrows": "body rows", "dips": "assisted dips",
           "pikeups": "seated pike ups", "pikes": "pike push-ups",
           "bulgarians": "bulgarian split squats", "bss": "bulgarian split squats", "lsits": "L-sit", "lsit": "L-sit",
           "compression": "compression hold", "compressions": "compression hold", "legliftholds": "compression hold"}

# Holds, in seconds a side. goal: which of his flexibility goals a stretch serves.
STRETCHES = {
    "lying hamstring": {"hold": 45, "sided": True, "goal": "pike", "muscles": {"hamstrings": 1, "calves": 0.3}},
    "seated pike": {"hold": 45, "goal": "pike", "muscles": {"hamstrings": 1, "lower back": 0.6, "calves": 0.4}},
    "standing fold": {"hold": 45, "goal": "pike", "muscles": {"hamstrings": 1, "lower back": 0.5, "calves": 0.5}},
    "hip flexor lunge": {"hold": 45, "sided": True, "goal": "front split", "muscles": {"hip flexors": 1, "quads": 0.5}},
    "couch stretch": {"hold": 45, "sided": True, "goal": "front split", "muscles": {"quads": 1, "hip flexors": 1}},
    "half split": {"hold": 45, "sided": True, "goal": "front split", "muscles": {"hamstrings": 1, "calves": 0.4}},
    "pigeon": {"hold": 60, "sided": True, "goal": "hips", "muscles": {"glutes": 1, "hip flexors": 0.6}},
    "front split": {"hold": 60, "sided": True, "goal": "front split",
                    "muscles": {"hamstrings": 1, "hip flexors": 1, "quads": 0.4}},
    "butterfly": {"hold": 60, "goal": "middle split", "muscles": {"adductors": 1, "glutes": 0.3}},
    "side lunge": {"hold": 30, "sided": True, "goal": "middle split", "muscles": {"adductors": 1, "hamstrings": 0.4}},
    "deep squat": {"hold": 60, "goal": "hips", "muscles": {"adductors": 0.6, "glutes": 0.6, "calves": 0.5, "lower back": 0.3}},
    "pancake": {"hold": 60, "goal": "middle split", "muscles": {"adductors": 1, "hamstrings": 0.8, "lower back": 0.6}},
    "middle split": {"hold": 60, "goal": "middle split", "muscles": {"adductors": 1, "hamstrings": 0.4}},
}
# The challenge's stretches, from David's own videos ("The Only Middle Split Video You'll Ever Need",
# "PNF Stretching And Getting Middle Splits", "The Scientific Way To Get Your Splits", "Stretching
# is Bodybuilding"): practise the split itself, 30 s sets hard enough that you want to quit before
# the end but never in pain, about ten sets a week, half of them active. pnf is the squeeze in the
# middle of each set: sink in, squeeze without moving, then relax and go deeper. cue is his
# hard version, said on the card.
STRETCHES.update({
    "front split practice": {"hold": 30, "sets": 2, "sided": True, "goal": "front split", "hard": True,
                             "pnf": "seconds, heel into the floor",
                             "cue": "Chair or pillows under you, back knee lifted, lean over the front leg",
                             "muscles": {"hamstrings": 1, "hip flexors": 1, "quads": 0.4}},
    "middle split practice": {"hold": 30, "sets": 2, "goal": "middle split", "hard": True,
                              "cue": "Turn knees and toes up as you slide wide, so the hips don't jam",
                              "muscles": {"adductors": 1, "hamstrings": 0.5, "glutes": 0.4}},
    "PNF pancake": {"hold": 30, "sets": 2, "goal": "middle split", "hard": True,
                    "pnf": "seconds, legs down and in",
                    "cue": "Tilt the hips, not the spine. Elbows to the floor if you can",
                    "muscles": {"adductors": 1, "hamstrings": 1, "lower back": 0.5}},
    "deep pigeon": {"hold": 30, "sets": 2, "sided": True, "goal": "hips", "hard": True,
                    "cue": "Chest down over the front shin",
                    "muscles": {"glutes": 1, "hip flexors": 0.6}},
    "lunge with foot grab": {"hold": 30, "sets": 2, "sided": True, "goal": "front split", "hard": True,
                             "cue": "Hips pushed down, then reach back and pull the back foot up",
                             "muscles": {"hip flexors": 1, "quads": 1}},
})
# The mixed cards leave the challenge's own moves out: feet-up push-ups are a choice made in the
# challenge, and David's hard stretches belong to it.
CHALLENGE_ONLY = {"decline push-ups"}
MIXED_EXERCISES = tuple(e for e in EXERCISES if e not in CHALLENGE_ONLY)
# Equipment beyond a floor, a wall and one chair, and the moves that need it: any one of the listed
# tools will do. You say what you have, and a move whose gear you lack never comes up.
TOOLS = {"bar": "a pull-up bar", "table": "a sturdy table", "chairs": "two sturdy chairs",
         "parallettes": "parallettes or dip bars"}
GEAR = {"assisted pull-ups": ("bar",), "assisted dips": ("parallettes", "chairs"), "body rows": ("table",),
        "L-sit": ("parallettes",)}
DEFAULT_EXERCISES = tuple(e for e in MIXED_EXERCISES if e not in GEAR)


# Where a move happens, so the coach sends you to the right spot: a wall sit isn't on the floor.
# Each place: the one-word order, and the longer one.
PLACES = {"floor": ("Floor.", "get on the floor"), "feet": ("Up.", "get on your feet"),
          "chair": ("Chair.", "grab the chair"), "wall": ("Wall.", "get to the wall"),
          "table": ("Table.", "get under the table"), "bar": ("Bar.", "get to the bar"),
          "chairs": ("Chairs.", "get between the chairs"), "parallettes": ("Bars.", "get on the parallettes")}
PLACE = {
    "squats": "feet", "lunges": "feet", "squat jumps": "feet", "calf raises": "feet", "jumping jacks": "feet",
    "cossack squats": "feet", "bulgarian split squats": "chair", "step-ups": "chair", "chair dips": "chair",
    "wall sit": "wall", "body rows": "table", "assisted pull-ups": "bar", "assisted dips": "chairs",
    "L-sit": "parallettes",
}


def place(name):
    """The one-word order and the longer one for where a move happens: ('Wall.', 'get to the wall')."""
    return PLACES[PLACE.get(name, "floor")]


def can_do(name, gear):
    """Whether the gear you have is enough for a move."""
    return name not in GEAR or any(t in gear for t in GEAR[name])


def needs(name):
    """'a pull-up bar', 'parallettes or dip bars, or two sturdy chairs', or '' for none."""
    return ", or ".join(TOOLS[t] for t in GEAR.get(name, ()))


MIXED_STRETCHES = tuple(s for s, v in STRETCHES.items() if not v.get("hard"))
GOALS = {"front split": "front split", "middle split": "side split", "pike": "pike and fold", "hips": "open hips"}

ACTIVITIES = {
    "climbing": {"name": "Climbing", "muscles": {"forearms": 1, "lats": 1, "biceps": 0.8, "traps": 0.6, "shoulders": 0.5,
                                                 "abs": 0.5, "lower back": 0.3, "calves": 0.3}},
    # Short, hard problems: more shoulder and core than a route, and the legs work for the top-outs.
    "bouldering": {"name": "Bouldering", "muscles": {"forearms": 1, "lats": 0.9, "biceps": 0.8, "shoulders": 0.7,
                                                     "abs": 0.7, "traps": 0.6, "obliques": 0.5, "chest": 0.3,
                                                     "quads": 0.3, "calves": 0.3}},
    "weights": {"name": "Weights", "muscles": {m: 0.7 for m in ("chest", "shoulders", "triceps", "biceps", "lats", "traps",
                                                               "quads", "hamstrings", "glutes", "lower back")}},
    "running": {"name": "Running", "cardio": 1,
                "muscles": {"quads": 0.8, "hamstrings": 0.7, "calves": 1, "glutes": 0.6, "hip flexors": 0.5}},
    "cycling": {"name": "Cycling", "cardio": 0.8, "muscles": {"quads": 1, "glutes": 0.6, "calves": 0.5, "hamstrings": 0.4}},
    "swimming": {"name": "Swimming", "cardio": 0.8,
                 "muscles": {"lats": 1, "shoulders": 0.8, "triceps": 0.5, "chest": 0.5, "abs": 0.3}},
    "football": {"name": "Football", "cardio": 0.9,
                 "muscles": {"quads": 0.9, "hamstrings": 0.8, "calves": 0.7, "adductors": 0.6, "hip flexors": 0.6}},
    "hiit": {"name": "HIIT", "cardio": 1,
             "muscles": {"quads": 0.7, "glutes": 0.6, "chest": 0.6, "shoulders": 0.6, "abs": 0.6}},
    "yoga": {"name": "Yoga", "muscles": {"abs": 0.4, "shoulders": 0.3, "glutes": 0.3},
             "stretch": {"hamstrings": 0.8, "hip flexors": 0.7, "lower back": 0.6, "shoulders": 0.5, "adductors": 0.5}},
    "walking": {"name": "Walk or hike", "cardio": 0.3, "muscles": {"quads": 0.4, "calves": 0.4, "glutes": 0.3}},
    "other": {"name": "Other", "cardio": 0.4, "muscles": {m: 0.4 for m in MUSCLES}},
}


def roll_up(entry):
    """The coarse parts recovery works on: each part as its hardest-worked muscle, plus cardio."""
    parts = {}
    for m, w in entry["muscles"].items():
        parts[PART_OF[m]] = max(parts.get(PART_OF[m], 0), w)
    if entry.get("cardio"):
        parts["cardio"] = entry["cardio"]
    return parts


for _e in (*EXERCISES.values(), *ACTIVITIES.values()):
    _e["parts"] = roll_up(_e)
for _name, _s in STRETCHES.items():
    _s.update(one=_name, scale=1, timed=True, stretch=True, parts={})
    if _s.get("sided"):
        _s["per"] = "each side"

PARTS = ("push", "pull", "legs", "core", "cardio")
PART_NAMES = {"push": "chest and arms", "pull": "back and grip", "legs": "legs", "core": "core", "cardio": "lungs"}


# What a part that hurts can be called: its own name, any muscle in it, and the everyday words.
PART_WORDS = {**{p: p for p in PARTS}, **PART_OF, "arms": "push", "arm": "push", "tricep": "push",
              "pecs": "push", "shoulder": "push", "back": "pull", "bicep": "pull", "grip": "pull", "lat": "pull",
              "leg": "legs", "knees": "legs", "quad": "legs", "hamstring": "legs", "glute": "legs", "calf": "legs",
              "stomach": "core", "elbow": "push", "elbows": "push", "wrist": "push", "wrists": "push"}
PART_WORD = {"push": "arms", "pull": "back", "legs": "legs", "core": "core", "cardio": "cardio"}  # what to type back


def part_named(word):
    """The part a word for a body part means, or None."""
    return PART_WORDS.get(word.lower().strip(","))


HURT_PARTS = ("push", "pull", "legs", "core")  # what can hurt; out of breath isn't pain


def hurt_part(exercise):
    """The part a move leans on most, which is what "it hurts" on its card means. None for a move
    that leans on none of them."""
    parts = {p: w for p, w in info(exercise)["parts"].items() if p in HURT_PARTS}
    return max(parts, key=parts.get) if parts else None


def spots_text(spots):
    """'left triceps and both shoulders' for [["triceps", "left"], ["shoulders", "left"], ["shoulders", "right"]]."""
    sides = {}
    for m, side in spots:
        sides.setdefault(m, set()).add(side)
    words = [f"both {m}" if len(s) > 1 else f"{next(iter(s))} {m}" for m, s in sides.items()]
    return " and ".join([", ".join(words[:-1]), words[-1]]) if len(words) > 1 else words[0] if words else ""


def spots_by_part(spots):
    """Spots grouped by the part they belong to: {"push": [["triceps", "left"]], ...}."""
    out = {}
    for s in spots if isinstance(spots, list) else []:
        if isinstance(s, (list, tuple)) and len(s) == 2 and s[0] in PART_OF and s[1] in ("left", "right"):
            out.setdefault(PART_OF[s[0]], []).append([s[0], s[1]])
    return out


def verb(part):
    return "is" if part == "core" else "are"


EFFORT = {"easy": 0.6, "normal": 1.0, "hard": 1.5}

RECOVERY_HALF_LIFE = 20 * 3600
REST_LOAD = 1.6  # a part this loaded means stretches only: two hard hours of climbing is about 3
SORE_LOAD = 0.7  # a part this loaded is left alone by exercises that lean on it


def slider_top(most):
    """A slider's end: the most it can ask for plus a bit, rounded up to a clean step."""
    step = 5 if most <= 40 else 10
    return -(-(most + step) // step) * step


def info(name):
    """An exercise or a stretch. A name nothing knows gets push-up sized goals and no muscles."""
    return EXERCISES.get(name) or STRETCHES.get(name) or {"one": name.rstrip("s"), "scale": 1, "parts": {"push": 1},
                                                          "muscles": {}}


def squash(text):
    return re.sub(r"[\s-]+", "", text.lower())


def candidates(text, known=None):
    """Every name a typed name could mean: exact or an alias first, else each name it is a piece of."""
    known = list(known or (*EXERCISES, *STRETCHES))
    key = squash(text)
    exact = [name for name in known if squash(name) == key]
    if exact:
        return exact
    if ALIASES.get(key) in known:
        return [ALIASES[key]]
    return [name for name in known if key and key in squash(name)]


def find(text, known=None):
    """The exercise or stretch a typed name means: 'rows', 'Body Rows', 'pullups', 'pike ups'.
    None when it means nothing or more than one thing."""
    hits = candidates(text, known)
    return hits[0] if len(hits) == 1 else None


PUSH_UPS = ("push-ups", "decline push-ups")  # both count toward the day's push-ups


def did(entry):
    """Reps (or seconds) actually done. Old entries only have the goal, which counted as done in full."""
    if entry.get("result") not in ("done", "manual"):
        return 0
    return entry.get("did", entry.get("reps", 0))


def exercise_of(entry):
    return entry.get("exercise", "push-ups")


def is_stretch(name):
    return name in STRETCHES


def unit(exercise, n, per=True):
    """'12 squats', '1 squat', '45 seconds of plank', '45 seconds each side'. per=False leaves the
    side out, for a card that says it on a line of its own."""
    e = info(exercise)
    if e.get("stretch"):
        return ("second" if n == 1 else "seconds") + (" each side" if e.get("sided") and per else "")
    word = ("second of " if n == 1 else "seconds of ") + exercise if e.get("timed") else e["one"] if n == 1 else exercise
    return f"{word} {e['per']}" if e.get("per") and per else word


def each_side(exercise):
    """'leg' or 'side' when one number is done on each side, so the card can say so and give the total."""
    per = info(exercise).get("per", "")
    return per.split(" ", 1)[1] if per.startswith("each ") else ""


def describe_set(name, n):
    """'12 squats', '45 seconds of plank', '45 seconds each side of couch stretch'."""
    return f"{n} {unit(name, n)}" + (f" of {name}" if is_stretch(name) else "")


def plural(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"


def day_lines(sets):
    """A day's sets as one phrase per exercise, in the order first done: '27 push-ups in 2 sets',
    '45 seconds of plank'. Sets of one exercise add up; different exercises never do."""
    totals, counts = {}, {}
    for name, n in sets:
        totals[name] = totals.get(name, 0) + n
        counts[name] = counts.get(name, 0) + 1
    return [describe_set(name, n) + (f" in {plural(counts[name], 'set')}" if counts[name] > 1 else "") for name, n in totals.items()]


WORK_PER_MINUTE = 2  # a minute of training counts as two push-ups' worth on the body map
WORK_FULL, STRETCH_FULL = 150, 600  # a week this big lights a muscle all the way: units, seconds


def work_of(e):
    """What one log entry did to each muscle, in push-up equivalents: a set's reps over the move's
    scale, a training session's minutes by its effort. Stretches and skips work nothing."""
    if e.get("result") == "session" and e.get("activity") in ACTIVITIES:
        amount = e.get("minutes", 0) * EFFORT.get(e.get("effort"), 1.0) * WORK_PER_MINUTE
        return {m: amount * w for m, w in ACTIVITIES[e["activity"]]["muscles"].items()}
    did = e.get("did", e.get("reps", 0))
    if e.get("result") in ("done", "manual") and did > 0:
        x = info(e.get("exercise", "push-ups"))
        if not x.get("stretch"):
            return {m: did / x["scale"] * w for m, w in x.get("muscles", {}).items()}
    return {}


def stretch_of(e):
    """What one log entry stretched, in seconds held per muscle: a stretch's hold (both sides when
    it has two), and the stretch in a session like yoga."""
    if e.get("result") == "session" and e.get("activity") in ACTIVITIES:
        return {m: e.get("minutes", 0) * 60 * w * 0.3 for m, w in ACTIVITIES[e["activity"]].get("stretch", {}).items()}
    did = e.get("did", e.get("reps", 0))
    x = info(e.get("exercise", "push-ups"))
    if e.get("result") in ("done", "manual") and did > 0 and x.get("stretch"):
        return {m: did * (2 if x.get("sided") else 1) * w for m, w in x["muscles"].items()}
    return {}


def muscle_week(entries, now, days=7):
    """What each muscle got over the last days: (work, stretch), each {muscle: 0 to 1}, plus the
    raw totals. Work counts push-up equivalents, stretch counts seconds held."""
    work, stretch = dict.fromkeys(MUSCLES, 0.0), dict.fromkeys(MUSCLES, 0.0)
    for e in entries:
        if not 0 <= now - e["ts"] < days * 86400:
            continue
        for m, v in work_of(e).items():
            work[m] += v
        for m, v in stretch_of(e).items():
            stretch[m] += v
    top_w = max(WORK_FULL, max(work.values()))
    top_s = max(STRETCH_FULL, max(stretch.values()))
    return ({m: v / top_w for m, v in work.items()}, {m: v / top_s for m, v in stretch.items()}, work, stretch)


def neglected(work, count=3):
    """The muscles that got least this week, forearms aside (everything works them a bit)."""
    return sorted((m for m in MUSCLES if m != "forearms"), key=lambda m: work[m])[:count]


def balance_weight(name, work):
    """Lean the card toward moves for muscles that got little this week: 1 to 3."""
    muscles = info(name).get("muscles", {})
    total = sum(muscles.values())
    if not total:
        return 1.0
    return 1 + 2 * sum(w * (1 - min(1.0, work[m])) for m, w in muscles.items()) / total


def sessions(entries):
    return [e for e in entries if e.get("result") == "session" and e.get("activity") in ACTIVITIES]


def session_load(e):
    return e.get("minutes", 0) / 60 * EFFORT.get(e.get("effort"), 1.0)


def loads(entries, now):
    """How loaded each body part is right now, from the training sessions of the last four days."""
    out = dict.fromkeys(PARTS, 0.0)
    for e in sessions(entries):
        fade = faded(e, now)
        for part, w in ACTIVITIES[e["activity"]]["parts"].items():
            out[part] += session_load(e) * w * fade
    return out


TRAINING_DAY = 0.5  # half an hour of climbing, or a long walk: enough work that the challenge takes the day off


def training_day(e):
    """Whether a session is enough to count as the day's training: its length and effort times
    how hard it works its hardest part."""
    return session_load(e) * max(ACTIVITIES[e["activity"]]["parts"].values(), default=0) >= TRAINING_DAY


def faded(e, now):
    """How much of a session still counts: halves every RECOVERY_HALF_LIFE, gone after four days."""
    age = now - e["ts"]
    return 0.5 ** (age / RECOVERY_HALF_LIFE) if 0 <= age < 4 * 86400 else 0.0


def resting(entries, now):
    """When a heavy session means no cards: (until, why), or None."""
    load = loads(entries, now)
    part = max(load, key=load.get)
    if load[part] < REST_LOAD:
        return None
    until = now + RECOVERY_HALF_LIFE * math.log2(load[part] / REST_LOAD)
    recent = [e for e in sessions(entries) if faded(e, now)]
    heaviest = max(recent, key=lambda e: session_load(e) * faded(e, now), default=None)
    return until, describe(heaviest) if heaviest else "training"


def describe(e):
    mins = e.get("minutes", 0)
    length = f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 and mins % 60 else f"{mins // 60}h" if mins >= 60 else f"{mins} min"
    effort = "" if e.get("effort", "normal") == "normal" else f" {e['effort']}"
    label = e.get("label")  # what another device called it
    name = label if isinstance(label, str) and label else ACTIVITIES[e["activity"]]["name"]
    return f"{name.lower()} {length}{effort}"


def sore(exercise, load):
    """The body part that rules this exercise out today, or None."""
    for part, w in sorted(info(exercise)["parts"].items(), key=lambda kv: -kv[1]):
        if w >= 0.7 and load[part] >= SORE_LOAD:
            return part
    return None


def recovery_factor(exercise, load):
    """How much of the usual goal is fair while recovering: 1 when fresh, about 0.7 a load in."""
    worst = max((w * load[p] for p, w in info(exercise)["parts"].items()), default=0)
    return math.exp(-0.35 * worst)
