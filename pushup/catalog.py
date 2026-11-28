import json
import math
import re
from pathlib import Path

PART_OF = {
    "chest": "push", "shoulders": "push", "triceps": "push",
    "biceps": "pull", "forearms": "pull", "traps": "pull", "lats": "pull",
    "abs": "core", "obliques": "core", "lower back": "core", "hip flexors": "core",
    "glutes": "legs", "adductors": "legs", "quads": "legs", "hamstrings": "legs", "calves": "legs",
}
MUSCLES = tuple(PART_OF)

SUB_MUSCLES = {
    "upper chest": {"base": "chest", "part": "push", "region": "torso", "label": "Upper chest"},
    "mid chest": {"base": "chest", "part": "push", "region": "torso", "label": "Mid chest"},
    "lower chest": {"base": "chest", "part": "push", "region": "torso", "label": "Lower chest"},
    "front delts": {"base": "shoulders", "part": "push", "region": "shoulder", "label": "Front delts"},
    "side delts": {"base": "shoulders", "part": "push", "region": "shoulder", "label": "Side delts"},
    "rear delts": {"base": "shoulders", "part": "pull", "region": "shoulder", "label": "Rear delts"},
    "triceps long head": {"base": "triceps", "part": "push", "region": "arm", "label": "Triceps (long head)"},
    "triceps lateral head": {"base": "triceps", "part": "push", "region": "arm", "label": "Triceps (outer)"},
    "biceps": {"base": "biceps", "part": "pull", "region": "arm", "label": "Biceps"},
    "brachialis": {"base": "biceps", "part": "pull", "region": "arm", "label": "Brachialis"},
    "forearm flexors": {"base": "forearms", "part": "pull", "region": "arm", "label": "Forearm flexors"},
    "forearm extensors": {"base": "forearms", "part": "pull", "region": "arm", "label": "Forearm extensors"},
    "upper traps": {"base": "traps", "part": "pull", "region": "shoulder", "label": "Upper traps"},
    "mid traps": {"base": "traps", "part": "pull", "region": "torso", "label": "Mid traps & rhomboids"},
    "lats": {"base": "lats", "part": "pull", "region": "torso", "label": "Lats"},
    "serratus": {"base": "chest", "part": "push", "region": "torso", "label": "Serratus"},
    "upper abs": {"base": "abs", "part": "core", "region": "core", "label": "Upper abs"},
    "lower abs": {"base": "abs", "part": "core", "region": "core", "label": "Lower abs"},
    "obliques": {"base": "obliques", "part": "core", "region": "core", "label": "Obliques"},
    "lower back": {"base": "lower back", "part": "core", "region": "torso", "label": "Lower back"},
    "hip flexors": {"base": "hip flexors", "part": "core", "region": "core", "label": "Hip flexors"},
    "glutes": {"base": "glutes", "part": "legs", "region": "core", "label": "Glutes"},
    "glute medius": {"base": "glutes", "part": "legs", "region": "core", "label": "Outer glutes"},
    "quads": {"base": "quads", "part": "legs", "region": "thigh", "label": "Quads"},
    "adductors": {"base": "adductors", "part": "legs", "region": "thigh", "label": "Inner thighs (adductors)"},
    "hamstrings": {"base": "hamstrings", "part": "legs", "region": "thigh", "label": "Hamstrings"},
    "calves": {"base": "calves", "part": "legs", "region": "lower_leg", "label": "Calves"},
    "tibialis": {"base": "calves", "part": "legs", "region": "lower_leg", "label": "Shins (tibialis)"},
}

TENDONS = {
    "finger pulleys": {
        "label": "Finger pulleys (A2/A4)", "part": "pull", "region": "hand", "recovery_days": 4,
        "aliases": ("finger pulley", "finger tendon", "finger tendons", "a2 pulley", "a4 pulley", "fingers", "pulley"),
    },
    "wrist tendons": {
        "label": "Wrist tendons", "part": "push", "region": "hand", "recovery_days": 3,
        "aliases": ("wrist", "wrists", "wrist tendon", "wrist flexor", "wrist extensor"),
    },
    "medial elbow": {
        "label": "Inner elbow (golfer's elbow)", "part": "pull", "region": "arm", "recovery_days": 4,
        "aliases": ("golfers elbow", "golfer's elbow", "inner elbow", "medial epicondyle", "elbow tendon"),
    },
    "lateral elbow": {
        "label": "Outer elbow (tennis elbow)", "part": "pull", "region": "arm", "recovery_days": 4,
        "aliases": ("tennis elbow", "outer elbow", "lateral epicondyle"),
    },
    "biceps tendon": {
        "label": "Biceps tendon", "part": "pull", "region": "arm", "recovery_days": 4,
        "aliases": ("bicep tendon", "distal biceps"),
    },
    "triceps tendon": {
        "label": "Triceps tendon", "part": "push", "region": "arm", "recovery_days": 3,
        "aliases": ("tricep tendon", "back of elbow"),
    },
    "shoulder tendon": {
        "label": "Rotator cuff / shoulder tendon", "part": "push", "region": "shoulder", "recovery_days": 4,
        "aliases": ("rotator cuff", "supraspinatus", "shoulder joint", "ac joint"),
    },
    "hip flexor tendon": {
        "label": "Hip flexor / groin tendon", "part": "core", "region": "core", "recovery_days": 3,
        "aliases": ("groin tendon", "hip tendon", "psoas tendon"),
    },
    "patellar tendon": {
        "label": "Knee / patellar tendon", "part": "legs", "region": "thigh", "recovery_days": 4,
        "aliases": ("knee", "knees", "knee tendon", "patellar", "quad tendon", "it band"),
    },
    "hamstring tendon": {
        "label": "Hamstring tendon", "part": "legs", "region": "thigh", "recovery_days": 4,
        "aliases": ("proximal hamstring", "back of knee"),
    },
    "achilles tendon": {
        "label": "Achilles tendon", "part": "legs", "region": "lower_leg", "recovery_days": 4,
        "aliases": ("achilles", "ankle tendon", "ankle", "ankles"),
    },
}

REGIONS = {
    "shoulder": {
        "label": "Shoulder",
        "muscles": ("front delts", "side delts", "rear delts", "upper traps"),
        "tendons": ("shoulder tendon",),
    },
    "arm": {
        "label": "Arm and elbow",
        "muscles": ("biceps", "brachialis", "triceps long head", "triceps lateral head", "forearm flexors", "forearm extensors"),
        "tendons": ("medial elbow", "lateral elbow", "biceps tendon", "triceps tendon"),
    },
    "hand": {
        "label": "Hand and wrist",
        "muscles": ("forearm flexors", "forearm extensors"),
        "tendons": ("finger pulleys", "wrist tendons"),
    },
    "torso": {
        "label": "Chest and back",
        "muscles": ("upper chest", "mid chest", "lower chest", "serratus", "lats", "mid traps", "lower back"),
        "tendons": (),
    },
    "core": {
        "label": "Core and hips",
        "muscles": ("upper abs", "lower abs", "obliques", "hip flexors", "glutes", "glute medius"),
        "tendons": ("hip flexor tendon",),
    },
    "thigh": {
        "label": "Thigh and knee",
        "muscles": ("quads", "hamstrings", "adductors"),
        "tendons": ("patellar tendon", "hamstring tendon"),
    },
    "lower_leg": {
        "label": "Calf and ankle",
        "muscles": ("calves", "tibialis"),
        "tendons": ("achilles tendon",),
    },
}

TRAINING_GOALS = {
    "planche": {"name": "Planche", "summary": "Hands-only push-up and straight-arm forward lean strength"},
    "front_lever": {"name": "Front lever", "summary": "Horizontal straight-body pull and core tension"},
    "core": {"name": "Core and abs", "summary": "Lower abs, hollow bodyline, compression, and anti-extension"},
    "climbing": {"name": "Climbing support", "summary": "Antagonist pushing, shoulder health, core tension, and tendon care"},
    "physique": {"name": "Athletic physique", "summary": "Balanced upper body, core definition, and lean strength"},
    "legs": {"name": "Legs", "summary": "Single-leg power, stability, and lower-body strength"},
    "flexibility": {"name": "Flexibility", "summary": "Front split, side split, pike, and open hips"},
}

DEFAULT_GOALS = ["planche", "front_lever", "core", "climbing", "physique"]


def _m(**kw):
    return {k.replace("_", " "): v for k, v in kw.items()}


EXERCISES = {
    "push-ups": {
        "one": "push-up", "scale": 1,
        "muscles": _m(chest=1, triceps=0.8, shoulders=0.6, abs=0.4),
        "tendons": _m(wrist_tendons=0.4, triceps_tendon=0.4, shoulder_tendon=0.4),
        "goals": ("physique", "climbing", "planche"),
        "point": "Mid chest, triceps, and front shoulders. Balanced horizontal push base.",
    },
    "wide push-ups": {
        "one": "wide push-up", "scale": 1,
        "muscles": _m(chest=1, shoulders=0.65, triceps=0.45, abs=0.4),
        "tendons": _m(shoulder_tendon=0.55, wrist_tendons=0.4),
        "goals": ("physique", "climbing"),
        "point": "Outer chest and front shoulders. Wider hands shift load off triceps onto pecs.",
    },
    "diamond push-ups": {
        "one": "diamond push-up", "scale": 0.8,
        "muscles": _m(triceps=1, chest=0.8, shoulders=0.65, abs=0.4),
        "tendons": _m(triceps_tendon=0.65, wrist_tendons=0.5, shoulder_tendon=0.4),
        "goals": ("physique", "planche", "climbing"),
        "point": "Triceps, inner chest, and front delts. Close hands build lockout strength.",
    },
    "incline push-ups": {
        "one": "incline push-up", "scale": 1.3,
        "muscles": _m(chest=0.95, triceps=0.65, shoulders=0.45, abs=0.3),
        "tendons": _m(wrist_tendons=0.25, shoulder_tendon=0.25),
        "goals": ("physique", "climbing"),
        "point": "Lower chest and light triceps. Easier angle for clean volume or recovery days.",
    },
    "decline push-ups": {
        "one": "decline push-up", "scale": 0.8,
        "muscles": _m(chest=1, shoulders=0.85, triceps=0.8, abs=0.5),
        "tendons": _m(shoulder_tendon=0.55, wrist_tendons=0.5, triceps_tendon=0.45),
        "goals": ("physique", "planche", "climbing"),
        "point": "Upper chest and front shoulders. Feet up shifts more bodyweight onto the arms.",
    },
    "pike push-ups": {
        "one": "pike push-up", "scale": 0.7,
        "muscles": _m(shoulders=1, triceps=0.75, chest=0.3, traps=0.35),
        "tendons": _m(shoulder_tendon=0.65, wrist_tendons=0.5, triceps_tendon=0.45),
        "goals": ("planche", "physique", "climbing"),
        "point": "Front and side shoulders plus triceps. Vertical pressing strength for planche and overhead.",
    },
    "planche lean": {
        "one": "planche lean", "scale": 1.6, "timed": True,
        "muscles": _m(shoulders=1, chest=0.7, abs=0.7, triceps=0.5, biceps=0.45, forearms=0.45),
        "tendons": _m(wrist_tendons=0.85, biceps_tendon=0.7, shoulder_tendon=0.7),
        "goals": ("planche", "core", "physique"),
        "point": "Front delts, straight-arm biceps tendon, and core. The number one foundation for a planche.",
    },
    "pseudo planche push-ups": {
        "one": "pseudo planche push-up", "scale": 0.55,
        "muscles": _m(shoulders=1, chest=0.85, triceps=0.75, abs=0.6, biceps=0.4),
        "tendons": _m(wrist_tendons=0.85, shoulder_tendon=0.75, biceps_tendon=0.55),
        "goals": ("planche", "physique"),
        "point": "Front delts, chest, and core with hands by your hips. Direct strength for planche push-ups.",
    },
    "frog stand": {
        "one": "frog stand", "scale": 1.4, "timed": True,
        "muscles": _m(shoulders=0.9, forearms=0.8, abs=0.7, triceps=0.6, chest=0.5),
        "tendons": _m(wrist_tendons=0.9, shoulder_tendon=0.5, triceps_tendon=0.45),
        "goals": ("planche", "core"),
        "point": "Balance, wrist strength, and front delts. Teaches holding your feet off the floor.",
    },
    "tuck planche": {
        "one": "tuck planche", "scale": 0.8, "timed": True,
        "muscles": _m(shoulders=1, chest=0.8, abs=0.85, triceps=0.7, biceps=0.5, forearms=0.6),
        "tendons": _m(wrist_tendons=0.95, biceps_tendon=0.8, shoulder_tendon=0.85),
        "goals": ("planche",),
        "point": "Straight-arm shoulder protraction and tucked core. Your first real feet-off-floor planche hold.",
    },
    "chair dips": {
        "one": "chair dip", "scale": 1,
        "muscles": _m(triceps=1, chest=0.5, shoulders=0.55),
        "tendons": _m(triceps_tendon=0.6, shoulder_tendon=0.55, wrist_tendons=0.4),
        "goals": ("physique", "climbing"),
        "point": "Triceps and front shoulders. Antagonist arm strength that balances climbing.",
    },
    "assisted dips": {
        "one": "assisted dip", "scale": 0.8,
        "muscles": _m(triceps=1, chest=0.9, shoulders=0.7, abs=0.2),
        "tendons": _m(shoulder_tendon=0.65, triceps_tendon=0.6, wrist_tendons=0.4),
        "goals": ("physique", "planche"),
        "point": "Lower chest and triceps. Heavy pushing compound between chairs or bars.",
    },
    "inverted rows": {
        "one": "inverted row", "scale": 0.8,
        "muscles": _m(lats=1, traps=0.8, biceps=0.7, forearms=0.55, shoulders=0.4, lower_back=0.2, glutes=0.2),
        "tendons": _m(medial_elbow=0.55, finger_pulleys=0.45, biceps_tendon=0.45),
        "goals": ("front_lever", "physique", "climbing"),
        "point": "Lats, mid back, and biceps under your table. Horizontal pull that builds front lever strength.",
    },
    "wide inverted rows": {
        "one": "wide inverted row", "scale": 0.75,
        "muscles": _m(traps=1, lats=0.85, shoulders=0.7, biceps=0.6, forearms=0.5),
        "tendons": _m(medial_elbow=0.5, lateral_elbow=0.5, finger_pulleys=0.45),
        "goals": ("front_lever", "physique", "climbing"),
        "point": "Upper back, rhomboids, and rear delts. Keeps climbing shoulders balanced and strong.",
    },
    "scapular rows": {
        "one": "scapular row", "scale": 1.0,
        "muscles": _m(traps=1, lats=0.85, forearms=0.5, abs=0.4),
        "tendons": _m(finger_pulleys=0.4, medial_elbow=0.4, shoulder_tendon=0.35),
        "goals": ("front_lever", "climbing"),
        "point": "Straight-arm shoulder blade pull under your table. Builds the exact lat lock for a front lever.",
    },
    "tuck front lever": {
        "one": "tuck front lever", "scale": 1.0, "timed": True,
        "muscles": _m(lats=1, abs=0.9, traps=0.8, forearms=0.75, biceps=0.5, lower_back=0.4),
        "tendons": _m(finger_pulleys=0.7, medial_elbow=0.75, biceps_tendon=0.7, shoulder_tendon=0.55),
        "goals": ("front_lever", "climbing", "core"),
        "point": "Lats and abs holding your body horizontal under the table. Direct front lever progression.",
    },
    "assisted pull-ups": {
        "one": "assisted pull-up", "scale": 0.6,
        "muscles": _m(lats=1, biceps=0.8, forearms=0.6, traps=0.5, shoulders=0.3, abs=0.2),
        "tendons": _m(finger_pulleys=0.65, medial_elbow=0.65, biceps_tendon=0.55),
        "goals": ("front_lever", "climbing", "physique"),
        "point": "Lats, biceps, and grip. Vertical pull for climbing and lever strength.",
    },
    "lying leg raises": {
        "one": "lying leg raise", "scale": 1.0,
        "muscles": _m(abs=1, hip_flexors=0.85, obliques=0.35, quads=0.25),
        "tendons": _m(hip_flexor_tendon=0.45),
        "goals": ("core", "front_lever", "planche", "physique"),
        "point": "Lower abs and hip flexors. Lifting straight legs builds core tension for front lever and climbing.",
    },
    "hollow hold": {
        "one": "hollow hold", "scale": 2, "timed": True,
        "muscles": _m(abs=1, hip_flexors=0.65, obliques=0.45),
        "tendons": _m(hip_flexor_tendon=0.35),
        "goals": ("core", "planche", "front_lever", "climbing"),
        "point": "Deep abs and lower back brace. The exact rigid bodyline used in planche and front lever.",
    },
    "hollow rocks": {
        "one": "hollow rock", "scale": 1.3,
        "muscles": _m(abs=1, hip_flexors=0.7, obliques=0.5),
        "tendons": _m(hip_flexor_tendon=0.4),
        "goals": ("core", "planche", "front_lever"),
        "point": "Dynamic abs and bodyline control. Keeps your ribs tucked while moving.",
    },
    "dragon flag negatives": {
        "one": "dragon flag negative", "scale": 0.45,
        "muscles": _m(abs=1, lats=0.6, hip_flexors=0.75, obliques=0.5, lower_back=0.4),
        "tendons": _m(hip_flexor_tendon=0.45, shoulder_tendon=0.35),
        "goals": ("front_lever", "core", "physique"),
        "point": "Full straight-body anti-extension holding a table leg. One of the best home front lever builders.",
    },
    "dead bugs": {
        "one": "dead bug", "scale": 1.5,
        "muscles": _m(abs=1, obliques=0.5, hip_flexors=0.45),
        "tendons": {},
        "goals": ("core", "climbing"),
        "point": "Deep core stability with zero tendon strain. Great even on climbing recovery days.",
    },
    "flutter kicks": {
        "one": "flutter kick", "scale": 2.0,
        "muscles": _m(abs=0.9, hip_flexors=1, quads=0.3),
        "tendons": _m(hip_flexor_tendon=0.45),
        "goals": ("core", "physique"),
        "point": "Lower abs and hip flexor endurance. Keeps tension under your belly button.",
    },
    "v-ups": {
        "one": "v-up", "scale": 0.85,
        "muscles": _m(abs=1, hip_flexors=0.8, obliques=0.4),
        "tendons": _m(hip_flexor_tendon=0.45),
        "goals": ("core", "physique", "front_lever"),
        "point": "Full upper and lower abs folding together. Explosive core compression.",
    },
    "sit-ups": {
        "one": "sit-up", "scale": 1.2,
        "muscles": _m(abs=1, hip_flexors=0.6, obliques=0.4),
        "tendons": _m(hip_flexor_tendon=0.35),
        "goals": ("core",),
        "point": "Upper abs and hip flexors. Classic trunk flexion.",
    },
    "plank": {
        "one": "plank", "scale": 3, "timed": True,
        "muscles": _m(abs=1, obliques=0.5, shoulders=0.4, glutes=0.3),
        "tendons": _m(shoulder_tendon=0.25),
        "goals": ("core", "climbing"),
        "point": "Isometric abs and glute brace. Teaches keeping a straight line from shoulders to heels.",
    },
    "side plank": {
        "one": "side plank", "scale": 2, "timed": True, "per": "each side",
        "muscles": _m(obliques=1, abs=0.5, shoulders=0.5, glutes=0.4, adductors=0.3),
        "tendons": _m(shoulder_tendon=0.35),
        "goals": ("core", "climbing", "physique"),
        "point": "Obliques and lateral hip stability. Helps keep your hips into the climbing wall.",
    },
    "seated pike ups": {
        "one": "seated pike up", "scale": 0.8,
        "muscles": _m(hip_flexors=1, abs=0.9, quads=0.4, obliques=0.3, triceps=0.3),
        "tendons": _m(hip_flexor_tendon=0.5),
        "goals": ("core", "planche", "flexibility"),
        "point": "Hip flexors and lower abs compression. Helps tuck legs cleanly for L-sit and planche.",
    },
    "compression hold": {
        "one": "compression hold", "scale": 1.5, "timed": True,
        "muscles": _m(hip_flexors=1, abs=0.9, quads=0.5, obliques=0.3, triceps=0.3),
        "tendons": _m(hip_flexor_tendon=0.5),
        "goals": ("core", "planche"),
        "point": "Hip flexors and abs holding straight legs off the floor. Floor-based L-sit strength.",
    },
    "L-sit": {
        "one": "L-sit", "scale": 1, "timed": True,
        "muscles": _m(abs=1, hip_flexors=1, triceps=0.6, quads=0.5, shoulders=0.4, traps=0.3, obliques=0.3),
        "tendons": _m(wrist_tendons=0.55, triceps_tendon=0.4, hip_flexor_tendon=0.45),
        "goals": ("core", "planche"),
        "point": "Abs, hip flexors, and straight-arm depression. Bridges core and planche work.",
    },
    "supermen": {
        "one": "superman", "scale": 1,
        "muscles": _m(lower_back=1, glutes=0.5, traps=0.4, hamstrings=0.3, shoulders=0.3),
        "tendons": {},
        "goals": ("core", "climbing", "front_lever"),
        "point": "Lower back, glutes, and posture muscles. Protects your spine with zero grip load.",
    },
    "squats": {
        "one": "squat", "scale": 1.5,
        "muscles": _m(quads=1, glutes=0.8, adductors=0.4, hamstrings=0.3, calves=0.2),
        "tendons": _m(patellar_tendon=0.5),
        "goals": ("legs", "physique"),
        "point": "Quads and glutes. Full-range lower-body base without tiring your arms or grip.",
    },
    "lunges": {
        "one": "lunge", "scale": 1.2,
        "muscles": _m(quads=1, glutes=0.8, hamstrings=0.4, adductors=0.3, calves=0.2),
        "tendons": _m(patellar_tendon=0.55),
        "goals": ("legs", "climbing"),
        "point": "Single-leg quads and glutes. Builds high-step leg power for climbing.",
    },
    "bulgarian split squats": {
        "one": "bulgarian split squat", "scale": 0.8, "per": "each leg",
        "muscles": _m(quads=1, glutes=1, hamstrings=0.4, adductors=0.4, hip_flexors=0.3, abs=0.2),
        "tendons": _m(patellar_tendon=0.65),
        "goals": ("legs", "physique", "climbing"),
        "point": "Quads, glutes, and balance on one leg. Serious leg strength using just a chair.",
    },
    "cossack squats": {
        "one": "cossack squat", "scale": 1, "per": "both sides",
        "muscles": _m(adductors=1, quads=0.8, glutes=0.7, hamstrings=0.3),
        "tendons": _m(patellar_tendon=0.5, hip_flexor_tendon=0.4),
        "goals": ("legs", "flexibility", "climbing"),
        "point": "Inner thighs, quads, and hip mobility. Opens your hips for wide climbing stems.",
    },
    "step-ups": {
        "one": "step-up", "scale": 1.2, "per": "each leg",
        "muscles": _m(quads=1, glutes=0.8, hamstrings=0.3, calves=0.3),
        "tendons": _m(patellar_tendon=0.5),
        "goals": ("legs", "climbing"),
        "point": "Quads and glutes stepping onto a chair. Direct high-foot climbing drive.",
    },
    "glute bridges": {
        "one": "glute bridge", "scale": 1.5,
        "muscles": _m(glutes=1, hamstrings=0.6, lower_back=0.3),
        "tendons": _m(hamstring_tendon=0.3),
        "goals": ("legs", "core", "climbing"),
        "point": "Glutes and hamstrings with zero knee or wrist load. Great for active recovery.",
    },
    "calf raises": {
        "one": "calf raise", "scale": 2,
        "muscles": _m(calves=1),
        "tendons": _m(achilles_tendon=0.7),
        "goals": ("legs", "climbing"),
        "point": "Calves and foot stability. Helps stand cleanly on small climbing footholds.",
    },
    "wall sit": {
        "one": "wall sit", "scale": 3, "timed": True,
        "muscles": _m(quads=1, glutes=0.4, adductors=0.2),
        "tendons": _m(patellar_tendon=0.45),
        "goals": ("legs",),
        "point": "Isometric quad burn. Builds knee tendon tolerance without heavy impact.",
    },
    "squat jumps": {
        "one": "squat jump", "scale": 1, "cardio": 0.8,
        "muscles": _m(quads=1, glutes=0.8, calves=0.7, hamstrings=0.3),
        "tendons": _m(patellar_tendon=0.8, achilles_tendon=0.8),
        "goals": ("legs", "climbing"),
        "point": "Explosive quads and calves. Builds dyno and jumping power.",
    },
    "burpees": {
        "one": "burpee", "scale": 0.5, "cardio": 1,
        "muscles": _m(quads=0.7, chest=0.6, triceps=0.5, shoulders=0.5, abs=0.5, glutes=0.5, calves=0.4),
        "tendons": _m(wrist_tendons=0.5, patellar_tendon=0.55, achilles_tendon=0.5),
        "goals": ("physique",),
        "point": "Full-body conditioning and lungs.",
    },
    "jumping jacks": {
        "one": "jumping jack", "scale": 3, "cardio": 1,
        "muscles": _m(calves=0.6, shoulders=0.4, quads=0.3, adductors=0.3, glutes=0.3),
        "tendons": _m(achilles_tendon=0.55),
        "goals": ("physique",),
        "point": "Light cardio warm-up for calves and shoulders.",
    },
    "mountain climbers": {
        "one": "mountain climber", "scale": 2, "cardio": 0.7,
        "muscles": _m(abs=0.8, hip_flexors=0.6, shoulders=0.5, quads=0.4),
        "tendons": _m(wrist_tendons=0.4, hip_flexor_tendon=0.4),
        "goals": ("core", "physique"),
        "point": "Abs, hip flexors, and shoulder endurance at cardio pace.",
    },
}

RENAMED = {"body rows": "inverted rows", "leg raises": "lying leg raises"}


def current(name):
    return RENAMED.get(name, name)


ALIASES = {
    "pushups": "push-ups", "pushup": "push-ups",
    "widepushups": "wide push-ups", "widepushup": "wide push-ups", "widegrippushups": "wide push-ups",
    "diamondpushups": "diamond push-ups", "diamondpushup": "diamond push-ups",
    "closepushups": "diamond push-ups", "closegrippushups": "diamond push-ups", "narrowpushups": "diamond push-ups",
    "inclinepushups": "incline push-ups", "inclinepushup": "incline push-ups", "inclines": "incline push-ups",
    "declinepushups": "decline push-ups", "declinepushup": "decline push-ups", "feetuppushups": "decline push-ups",
    "plancheleans": "planche lean", "planchelean": "planche lean",
    "pseudoplanchepushups": "pseudo planche push-ups", "pseudoplanche": "pseudo planche push-ups", "pppu": "pseudo planche push-ups",
    "frogstands": "frog stand", "crowpose": "frog stand",
    "tuckplanche": "tuck planche", "handlever": "tuck planche", "planche": "tuck planche",
    "tuckfrontlever": "tuck front lever", "frontlever": "tuck front lever", "tablelever": "tuck front lever",
    "pullups": "pull-ups", "pullup": "pull-ups",
    "rows": "inverted rows", "row": "inverted rows", "bodyrows": "inverted rows", "bodyrow": "inverted rows",
    "invertedrow": "inverted rows", "supinerows": "inverted rows", "supinerow": "inverted rows",
    "australianpullups": "inverted rows", "australianpullup": "inverted rows", "tablerows": "inverted rows",
    "tablerow": "inverted rows", "widerows": "wide inverted rows", "wideinvertedrow": "wide inverted rows",
    "scaprows": "scapular rows", "scapularrow": "scapular rows",
    "dips": "assisted dips",
    "pikeups": "seated pike ups", "pikes": "pike push-ups",
    "bulgarians": "bulgarian split squats", "bss": "bulgarian split squats",
    "lsits": "L-sit", "lsit": "L-sit",
    "compression": "compression hold", "compressions": "compression hold", "legliftholds": "compression hold",
    "legraises": "lying leg raises", "legraise": "lying leg raises",
    "lyinglegraises": "lying leg raises", "lyinglegraise": "lying leg raises",
    "straightlegraises": "lying leg raises", "floorlegraises": "lying leg raises",
    "hollowrocks": "hollow rocks", "hollowrock": "hollow rocks", "hollowbodyrocks": "hollow rocks",
    "dragonflag": "dragon flag negatives", "dragonflags": "dragon flag negatives", "dragonflagnegative": "dragon flag negatives",
    "deadbug": "dead bugs", "flutterkick": "flutter kicks", "vups": "v-ups", "vup": "v-ups",
}

STRETCHES = {
    "lying hamstring": {"hold": 45, "sided": True, "goal": "pike", "muscles": {"hamstrings": 1, "calves": 0.3}},
    "seated pike": {"hold": 45, "goal": "pike", "muscles": {"hamstrings": 1, "lower back": 0.6, "calves": 0.4}},
    "standing fold": {"hold": 45, "goal": "pike", "muscles": {"hamstrings": 1, "lower back": 0.5, "calves": 0.5}},
    "hip flexor lunge": {"hold": 45, "sided": True, "goal": "front split", "muscles": {"hip flexors": 1, "quads": 0.5}},
    "couch stretch": {"hold": 45, "sided": True, "goal": "front split", "muscles": {"quads": 1, "hip flexors": 1}},
    "half split": {"hold": 45, "sided": True, "goal": "front split", "muscles": {"hamstrings": 1, "calves": 0.4}},
    "pigeon": {"hold": 60, "sided": True, "goal": "hips", "muscles": {"glutes": 1, "hip flexors": 0.6}},
    "front split": {"hold": 60, "sided": True, "goal": "front split", "muscles": {"hamstrings": 1, "hip flexors": 1, "quads": 0.4}},
    "butterfly": {"hold": 60, "goal": "middle split", "muscles": {"adductors": 1, "glutes": 0.3}},
    "side lunge": {"hold": 30, "sided": True, "goal": "middle split", "muscles": {"adductors": 1, "hamstrings": 0.4}},
    "deep squat": {"hold": 60, "goal": "hips", "muscles": {"adductors": 0.6, "glutes": 0.6, "calves": 0.5, "lower back": 0.3}},
    "pancake": {"hold": 60, "goal": "middle split", "muscles": {"adductors": 1, "hamstrings": 0.8, "lower back": 0.6}},
    "middle split": {"hold": 60, "goal": "middle split", "muscles": {"adductors": 1, "hamstrings": 0.4}},
}

STRETCHES.update({
    "front split practice": {
        "hold": 30, "sets": 2, "sided": True, "goal": "front split", "hard": True,
        "pnf": "seconds, heel into the floor",
        "cue": "Chair or pillows under you, back knee lifted, lean over the front leg",
        "muscles": {"hamstrings": 1, "hip flexors": 1, "quads": 0.4},
    },
    "middle split practice": {
        "hold": 30, "sets": 2, "goal": "middle split", "hard": True,
        "cue": "Turn knees and toes up as you slide wide, so the hips don't jam",
        "muscles": {"adductors": 1, "hamstrings": 0.5, "glutes": 0.4},
    },
    "PNF pancake": {
        "hold": 30, "sets": 2, "goal": "middle split", "hard": True,
        "pnf": "seconds, legs down and in",
        "cue": "Tilt the hips, not the spine. Elbows to the floor if you can",
        "muscles": {"adductors": 1, "hamstrings": 1, "lower back": 0.5},
    },
    "deep pigeon": {
        "hold": 30, "sets": 2, "sided": True, "goal": "hips", "hard": True,
        "cue": "Chest down over the front shin",
        "muscles": {"glutes": 1, "hip flexors": 0.6},
    },
    "lunge with foot grab": {
        "hold": 30, "sets": 2, "sided": True, "goal": "front split", "hard": True,
        "cue": "Hips pushed down, then reach back and pull the back foot up",
        "muscles": {"hip flexors": 1, "quads": 1},
    },
})

CHALLENGE_ONLY = set()
MIXED_EXERCISES = tuple(e for e in EXERCISES if e not in CHALLENGE_ONLY)

TOOLS = {
    "chair": "a chair",
    "table": "a sturdy table",
    "bar": "a pull-up bar",
    "chairs": "two sturdy chairs",
    "parallettes": "parallettes or dip bars",
    "gym": "gym weights or machines",
}
DEFAULT_GEAR = ["chair", "table"]

GEAR = {
    "incline push-ups": ("chair", "table"),
    "decline push-ups": ("chair",),
    "chair dips": ("chair",),
    "bulgarian split squats": ("chair",),
    "step-ups": ("chair",),
    "inverted rows": ("table",),
    "wide inverted rows": ("table",),
    "scapular rows": ("table",),
    "tuck front lever": ("table", "bar"),
    "dragon flag negatives": ("table", "chair"),
    "assisted pull-ups": ("bar",),
    "assisted dips": ("parallettes", "chairs"),
    "L-sit": ("parallettes", "chairs"),
}

DEFAULT_EXERCISES = tuple(e for e in MIXED_EXERCISES if e not in GEAR or any(t in DEFAULT_GEAR for t in GEAR[e]))

PLACES = {
    "floor": ("Floor.", "get on the floor"),
    "feet": ("Up.", "get on your feet"),
    "chair": ("Chair.", "grab the chair"),
    "wall": ("Wall.", "get to the wall"),
    "table": ("Table.", "get under the table"),
    "bar": ("Bar.", "get to the bar"),
    "chairs": ("Chairs.", "get between the chairs"),
    "parallettes": ("Bars.", "get on the parallettes"),
    "gym": ("Gym.", "at the gym"),
}

PLACE = {
    "squats": "feet", "lunges": "feet", "squat jumps": "feet", "calf raises": "feet", "jumping jacks": "feet",
    "cossack squats": "feet", "bulgarian split squats": "chair", "step-ups": "chair", "chair dips": "chair",
    "incline push-ups": "chair", "decline push-ups": "chair", "wall sit": "wall",
    "inverted rows": "table", "wide inverted rows": "table", "scapular rows": "table", "tuck front lever": "table",
    "dragon flag negatives": "table", "assisted pull-ups": "bar", "assisted dips": "chairs", "L-sit": "parallettes",
}


def place(name):
    return PLACES[PLACE.get(name, "floor")]


def can_do(name, gear):
    if name in LOGGED and name not in EXERCISES:
        return "gym" in gear
    return name not in GEAR or any(t in gear for t in GEAR[name])


def needs(name):
    if name in LOGGED and name not in EXERCISES:
        return TOOLS["gym"]
    return ", or ".join(TOOLS[t] for t in GEAR.get(name, ()))


MIXED_STRETCHES = tuple(s for s, v in STRETCHES.items() if not v.get("hard"))
GOALS = {"front split": "front split", "middle split": "side split", "pike": "pike and fold", "hips": "open hips"}

ACTIVITIES = {
    "climbing": {
        "name": "Climbing",
        "muscles": _m(forearms=1, lats=1, biceps=0.85, traps=0.65, shoulders=0.55, abs=0.55, lower_back=0.3, calves=0.3),
        "tendons": _m(finger_pulleys=1.0, medial_elbow=0.85, lateral_elbow=0.55, biceps_tendon=0.65, wrist_tendons=0.55, shoulder_tendon=0.55),
    },
    "bouldering": {
        "name": "Bouldering",
        "muscles": _m(forearms=1, lats=0.95, biceps=0.85, shoulders=0.75, abs=0.75, traps=0.65, obliques=0.55, chest=0.35, quads=0.35, calves=0.35),
        "tendons": _m(finger_pulleys=1.0, medial_elbow=0.9, lateral_elbow=0.6, biceps_tendon=0.7, wrist_tendons=0.65, shoulder_tendon=0.65, achilles_tendon=0.35),
    },
    "weights": {
        "name": "Weights",
        "muscles": {m: 0.7 for m in ("chest", "shoulders", "triceps", "biceps", "lats", "traps", "quads", "hamstrings", "glutes", "lower back")},
        "tendons": _m(shoulder_tendon=0.5, medial_elbow=0.45, patellar_tendon=0.45, wrist_tendons=0.4),
    },
    "gym": {
        "name": "Gym",
        "muscles": {m: 0.7 for m in ("chest", "shoulders", "triceps", "biceps", "lats", "traps", "quads", "hamstrings", "glutes", "lower back")},
        "tendons": _m(shoulder_tendon=0.5, medial_elbow=0.45, patellar_tendon=0.45, wrist_tendons=0.4),
    },
    "running": {
        "name": "Running", "cardio": 1,
        "muscles": _m(quads=0.8, hamstrings=0.7, calves=1, glutes=0.6, hip_flexors=0.5),
        "tendons": _m(achilles_tendon=0.85, patellar_tendon=0.75, hamstring_tendon=0.55),
    },
    "cycling": {
        "name": "Cycling", "cardio": 0.8,
        "muscles": _m(quads=1, glutes=0.6, calves=0.5, hamstrings=0.4),
        "tendons": _m(patellar_tendon=0.45),
    },
    "swimming": {
        "name": "Swimming", "cardio": 0.8,
        "muscles": _m(lats=1, shoulders=0.8, triceps=0.5, chest=0.5, abs=0.3),
        "tendons": _m(shoulder_tendon=0.6),
    },
    "football": {
        "name": "Football", "cardio": 0.9,
        "muscles": _m(quads=0.9, hamstrings=0.8, calves=0.7, adductors=0.6, hip_flexors=0.6),
        "tendons": _m(achilles_tendon=0.65, patellar_tendon=0.65, hamstring_tendon=0.6, hip_flexor_tendon=0.5),
    },
    "hiit": {
        "name": "HIIT", "cardio": 1,
        "muscles": _m(quads=0.7, glutes=0.6, chest=0.6, shoulders=0.6, abs=0.6),
        "tendons": _m(patellar_tendon=0.5, achilles_tendon=0.5, wrist_tendons=0.4),
    },
    "yoga": {
        "name": "Yoga",
        "muscles": _m(abs=0.4, shoulders=0.3, glutes=0.3),
        "stretch": _m(hamstrings=0.8, hip_flexors=0.7, lower_back=0.6, shoulders=0.5, adductors=0.5),
        "tendons": _m(wrist_tendons=0.25),
    },
    "walking": {
        "name": "Walk or hike", "cardio": 0.3,
        "muscles": _m(quads=0.4, calves=0.4, glutes=0.3),
        "tendons": _m(achilles_tendon=0.25),
    },
    "other": {
        "name": "Other", "cardio": 0.4,
        "muscles": {m: 0.4 for m in MUSCLES},
        "tendons": {},
    },
}


def roll_up(entry):
    parts = {}
    for m, w in entry.get("muscles", {}).items():
        base = SUB_MUSCLES[m]["base"] if m in SUB_MUSCLES else m
        if base in PART_OF:
            parts[PART_OF[base]] = max(parts.get(PART_OF[base], 0), w)
    if entry.get("cardio"):
        parts["cardio"] = entry["cardio"]
    return parts


LOGGED = {
    "archer push-ups": {"scale": 0.5, "muscles": _m(chest=1, triceps=0.8, shoulders=0.7, abs=0.5), "tendons": _m(shoulder_tendon=0.6, wrist_tendons=0.5)},
    "knee push-ups": {"scale": 1.5, "muscles": _m(chest=1, triceps=0.7, shoulders=0.5), "tendons": _m(wrist_tendons=0.3)},
    "handstand push-ups": {"scale": 0.3, "muscles": _m(shoulders=1, triceps=0.9, traps=0.6, chest=0.3), "tendons": _m(wrist_tendons=0.75, shoulder_tendon=0.8, triceps_tendon=0.6)},
    "handstand hold": {"scale": 1, "timed": True, "muscles": _m(shoulders=1, triceps=0.6, traps=0.5, abs=0.5, forearms=0.4), "tendons": _m(wrist_tendons=0.75, shoulder_tendon=0.65)},
    "dips": {"scale": 0.6, "muscles": _m(triceps=1, chest=0.9, shoulders=0.7, abs=0.2), "tendons": _m(shoulder_tendon=0.7, triceps_tendon=0.65, wrist_tendons=0.4)},
    "bench press": {"scale": 0.7, "weighted": True, "muscles": _m(chest=1, triceps=0.7, shoulders=0.6), "tendons": _m(shoulder_tendon=0.6, triceps_tendon=0.5, wrist_tendons=0.4)},
    "incline bench press": {"scale": 0.7, "weighted": True, "muscles": _m(chest=1, shoulders=0.8, triceps=0.7), "tendons": _m(shoulder_tendon=0.65, triceps_tendon=0.5)},
    "dumbbell bench press": {"scale": 0.7, "weighted": True, "muscles": _m(chest=1, triceps=0.6, shoulders=0.6), "tendons": _m(shoulder_tendon=0.55, wrist_tendons=0.4)},
    "overhead press": {"scale": 0.7, "weighted": True, "muscles": _m(shoulders=1, triceps=0.8, traps=0.5, abs=0.4), "tendons": _m(shoulder_tendon=0.7, triceps_tendon=0.5, wrist_tendons=0.45)},
    "dumbbell shoulder press": {"scale": 0.7, "weighted": True, "muscles": _m(shoulders=1, triceps=0.7, traps=0.4), "tendons": _m(shoulder_tendon=0.65, wrist_tendons=0.4)},
    "lateral raises": {"scale": 1.2, "weighted": True, "muscles": _m(shoulders=1, traps=0.4), "tendons": _m(shoulder_tendon=0.5)},
    "front raises": {"scale": 1.2, "weighted": True, "muscles": _m(shoulders=1, chest=0.3), "tendons": _m(shoulder_tendon=0.5)},
    "triceps extensions": {"scale": 1, "weighted": True, "muscles": _m(triceps=1), "tendons": _m(triceps_tendon=0.7)},
    "skull crushers": {"scale": 1, "weighted": True, "muscles": _m(triceps=1, forearms=0.2), "tendons": _m(triceps_tendon=0.75)},
    "triceps pushdowns": {"scale": 1.2, "weighted": True, "muscles": _m(triceps=1), "tendons": _m(triceps_tendon=0.55)},
    "chest flyes": {"scale": 1, "weighted": True, "muscles": _m(chest=1, shoulders=0.4), "tendons": _m(shoulder_tendon=0.55, biceps_tendon=0.4)},
    "pull-ups": {"scale": 0.4, "muscles": _m(lats=1, biceps=0.7, forearms=0.6, traps=0.5, shoulders=0.3, abs=0.2), "tendons": _m(finger_pulleys=0.7, medial_elbow=0.75, biceps_tendon=0.6)},
    "chin-ups": {"scale": 0.4, "muscles": _m(lats=1, biceps=0.9, forearms=0.6, traps=0.4, abs=0.2), "tendons": _m(finger_pulleys=0.65, medial_elbow=0.75, biceps_tendon=0.75)},
    "negative pull-ups": {"scale": 0.5, "muscles": _m(lats=1, biceps=0.7, forearms=0.6, traps=0.5), "tendons": _m(finger_pulleys=0.65, medial_elbow=0.75, biceps_tendon=0.65)},
    "scapular pull-ups": {"scale": 1, "muscles": _m(traps=1, lats=0.6, forearms=0.5), "tendons": _m(finger_pulleys=0.5, shoulder_tendon=0.4)},
    "dead hang": {"scale": 2, "timed": True, "muscles": _m(forearms=1, lats=0.5, shoulders=0.3), "tendons": _m(finger_pulleys=0.85, medial_elbow=0.5, shoulder_tendon=0.35)},
    "barbell rows": {"scale": 0.7, "weighted": True, "muscles": _m(lats=1, traps=0.8, biceps=0.6, lower_back=0.5, forearms=0.4), "tendons": _m(medial_elbow=0.5, biceps_tendon=0.45)},
    "dumbbell rows": {"scale": 0.8, "weighted": True, "muscles": _m(lats=1, traps=0.6, biceps=0.6, forearms=0.4), "tendons": _m(medial_elbow=0.5, biceps_tendon=0.45)},
    "lat pulldowns": {"scale": 0.8, "weighted": True, "muscles": _m(lats=1, biceps=0.6, traps=0.4, forearms=0.4), "tendons": _m(medial_elbow=0.5, shoulder_tendon=0.4)},
    "seated cable rows": {"scale": 0.8, "weighted": True, "muscles": _m(lats=1, traps=0.8, biceps=0.6, forearms=0.4), "tendons": _m(medial_elbow=0.45, biceps_tendon=0.4)},
    "face pulls": {"scale": 1, "weighted": True, "muscles": _m(shoulders=1, traps=0.8), "tendons": _m(shoulder_tendon=0.3)},
    "biceps curls": {"scale": 1, "weighted": True, "muscles": _m(biceps=1, forearms=0.5), "tendons": _m(biceps_tendon=0.65, medial_elbow=0.5)},
    "hammer curls": {"scale": 1, "weighted": True, "muscles": _m(biceps=0.9, forearms=1), "tendons": _m(lateral_elbow=0.55, biceps_tendon=0.55)},
    "shrugs": {"scale": 1.2, "weighted": True, "muscles": _m(traps=1, forearms=0.4), "tendons": {}},
    "deadlifts": {"scale": 0.5, "weighted": True, "muscles": _m(hamstrings=1, glutes=1, lower_back=1, traps=0.6, forearms=0.6, quads=0.4, lats=0.4), "tendons": _m(hamstring_tendon=0.65, finger_pulleys=0.45)},
    "back squats": {"scale": 0.7, "weighted": True, "muscles": _m(quads=1, glutes=0.9, adductors=0.5, lower_back=0.4, hamstrings=0.3), "tendons": _m(patellar_tendon=0.75)},
    "front squats": {"scale": 0.7, "weighted": True, "muscles": _m(quads=1, glutes=0.7, abs=0.4, adductors=0.4), "tendons": _m(patellar_tendon=0.75, wrist_tendons=0.5)},
    "goblet squats": {"scale": 1, "weighted": True, "muscles": _m(quads=1, glutes=0.8, adductors=0.5, abs=0.3), "tendons": _m(patellar_tendon=0.6)},
    "pistol squats": {"scale": 0.4, "muscles": _m(quads=1, glutes=0.9, hip_flexors=0.4, calves=0.3, abs=0.3), "tendons": _m(patellar_tendon=0.8, achilles_tendon=0.45)},
    "reverse lunges": {"scale": 1.2, "muscles": _m(quads=1, glutes=0.9, hamstrings=0.4, adductors=0.3), "tendons": _m(patellar_tendon=0.45)},
    "walking lunges": {"scale": 1.2, "muscles": _m(quads=1, glutes=0.8, hamstrings=0.4, calves=0.3), "tendons": _m(patellar_tendon=0.55)},
    "box jumps": {"scale": 0.8, "cardio": 0.6, "muscles": _m(quads=1, glutes=0.8, calves=0.8, hamstrings=0.3), "tendons": _m(patellar_tendon=0.8, achilles_tendon=0.85)},
    "hip thrusts": {"scale": 1, "weighted": True, "muscles": _m(glutes=1, hamstrings=0.5, quads=0.3), "tendons": _m(hamstring_tendon=0.35)},
    "romanian deadlifts": {"scale": 0.7, "weighted": True, "muscles": _m(hamstrings=1, glutes=0.8, lower_back=0.7, forearms=0.3), "tendons": _m(hamstring_tendon=0.7)},
    "nordic curls": {"scale": 0.3, "muscles": _m(hamstrings=1, glutes=0.3, calves=0.2), "tendons": _m(hamstring_tendon=0.85)},
    "leg press": {"scale": 0.8, "weighted": True, "muscles": _m(quads=1, glutes=0.7, adductors=0.3, hamstrings=0.2), "tendons": _m(patellar_tendon=0.65)},
    "leg extensions": {"scale": 1, "weighted": True, "muscles": _m(quads=1), "tendons": _m(patellar_tendon=0.75)},
    "leg curls": {"scale": 1, "weighted": True, "muscles": _m(hamstrings=1, calves=0.3), "tendons": _m(hamstring_tendon=0.65)},
    "crunches": {"scale": 1.5, "muscles": _m(abs=1, obliques=0.4), "tendons": {}},
    "bicycle crunches": {"scale": 1.5, "muscles": _m(obliques=1, abs=0.8, hip_flexors=0.4), "tendons": {}},
    "hanging leg raises": {"scale": 0.6, "muscles": _m(abs=1, hip_flexors=0.9, forearms=0.5, lats=0.3), "tendons": _m(finger_pulleys=0.55, hip_flexor_tendon=0.5)},
    "hanging knee raises": {"scale": 0.9, "muscles": _m(abs=1, hip_flexors=0.7, forearms=0.5), "tendons": _m(finger_pulleys=0.5)},
    "russian twists": {"scale": 2, "muscles": _m(obliques=1, abs=0.6), "tendons": {}},
    "bird dogs": {"scale": 1.5, "muscles": _m(lower_back=1, glutes=0.6, abs=0.4, shoulders=0.2), "tendons": {}},
    "ab wheel rollouts": {"scale": 0.6, "muscles": _m(abs=1, lats=0.5, shoulders=0.5, hip_flexors=0.3), "tendons": _m(shoulder_tendon=0.45, wrist_tendons=0.35)},
    "high knees": {"scale": 3, "cardio": 1, "muscles": _m(hip_flexors=0.8, quads=0.5, calves=0.6), "tendons": _m(achilles_tendon=0.5)},
    "kettlebell swings": {"scale": 1, "weighted": True, "cardio": 0.7, "muscles": _m(glutes=1, hamstrings=0.8, lower_back=0.6, shoulders=0.3, forearms=0.4), "tendons": _m(hamstring_tendon=0.5)},
    "jump rope": {"scale": 5, "cardio": 1, "muscles": _m(calves=1, shoulders=0.3, forearms=0.3), "tendons": _m(achilles_tendon=0.75)},
}

CUSTOM = {}

for _name, _e in LOGGED.items():
    _e.setdefault("one", _name.rstrip("s"))
    _e.setdefault("tendons", {})

for _e in (*EXERCISES.values(), *LOGGED.values(), *ACTIVITIES.values()):
    _e.setdefault("tendons", {})
    _e["parts"] = roll_up(_e)

for _name, _s in STRETCHES.items():
    _s.update(one=_name, scale=1, timed=True, stretch=True, parts={}, tendons={})
    if _s.get("sided"):
        _s["per"] = "each side"

PARTS = ("push", "pull", "legs", "core", "cardio")
PART_NAMES = {"push": "chest and arms", "pull": "back and grip", "legs": "legs", "core": "core", "cardio": "lungs"}

PART_WORDS = {
    **{p: p for p in PARTS},
    **PART_OF,
    **{k: v["part"] for k, v in SUB_MUSCLES.items()},
    **{k: v["part"] for k, v in TENDONS.items()},
    "arms": "push", "arm": "push", "tricep": "push", "pecs": "push", "pec": "push", "shoulder": "push",
    "delt": "push", "delts": "push", "back": "pull", "bicep": "pull", "grip": "pull", "lat": "pull",
    "leg": "legs", "quad": "legs", "hamstring": "legs", "glute": "legs", "calf": "legs",
    "stomach": "core", "ab": "core", "elbow": "pull", "elbows": "pull", "wrist": "push", "wrists": "push",
    "finger": "pull", "fingers": "pull", "knee": "legs", "knees": "legs", "ankle": "legs", "ankles": "legs",
}
PART_WORD = {"push": "arms", "pull": "back", "legs": "legs", "core": "core", "cardio": "cardio"}


def part_named(word):
    return PART_WORDS.get(word.lower().strip(","))


HURT_PARTS = ("push", "pull", "legs", "core")


def spot_info(name):
    raw = " ".join(str(name or "").lower().replace("_", " ").split())
    if not raw:
        return None
    if raw in TENDONS:
        t = TENDONS[raw]
        return {"name": raw, "kind": "tendon", "part": t["part"], "region": t["region"], "label": t["label"], "recovery_days": t["recovery_days"]}
    for key, t in TENDONS.items():
        if raw in t.get("aliases", ()):
            return {"name": key, "kind": "tendon", "part": t["part"], "region": t["region"], "label": t["label"], "recovery_days": t["recovery_days"]}
    if raw in SUB_MUSCLES:
        s = SUB_MUSCLES[raw]
        return {"name": raw, "kind": "muscle", "base": s["base"], "part": s["part"], "region": s["region"], "label": s["label"], "recovery_days": 2}
    if raw in PART_OF:
        return {"name": raw, "kind": "muscle", "base": raw, "part": PART_OF[raw], "region": None, "label": raw.capitalize(), "recovery_days": 2}
    if raw + "s" in PART_OF:
        m = raw + "s"
        return {"name": m, "kind": "muscle", "base": m, "part": PART_OF[m], "region": None, "label": m.capitalize(), "recovery_days": 2}
    if raw == "calf":
        return {"name": "calves", "kind": "muscle", "base": "calves", "part": "legs", "region": "lower_leg", "label": "Calves", "recovery_days": 2}
    return None


def is_tendon(name):
    inf = spot_info(name)
    return bool(inf and inf["kind"] == "tendon")


def hurt_part(exercise):
    parts = {p: w for p, w in info(exercise)["parts"].items() if p in HURT_PARTS}
    return max(parts, key=parts.get) if parts else None


def spots_text(spots):
    sides = {}
    for item in spots:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        m, side = item[0], item[1]
        inf = spot_info(m)
        label = inf["label"].lower() if inf and inf["kind"] == "tendon" else m
        sides.setdefault(label, set()).add(side)
    words = [f"both {m}" if len(s) > 1 else f"{next(iter(s))} {m}" for m, s in sides.items()]
    return " and ".join([", ".join(words[:-1]), words[-1]]) if len(words) > 1 else words[0] if words else ""


def spots_by_part(spots):
    out = {}
    for s in spots if isinstance(spots, list) else []:
        if isinstance(s, (list, tuple)) and len(s) >= 2 and s[1] in ("left", "right"):
            inf = spot_info(s[0])
            if inf and inf["part"] in HURT_PARTS:
                kind = s[2] if len(s) >= 3 and s[2] in ("muscle", "tendon") else inf["kind"]
                out.setdefault(inf["part"], []).append([inf["name"], s[1], kind])
    return out


def verb(part):
    return "is" if part == "core" else "are"


EFFORT = {"easy": 0.6, "normal": 1.0, "hard": 1.5}

RECOVERY_HALF_LIFE = 20 * 3600
HEAVY_HALF_LIFE = 28 * 3600
LIGHT_HALF_LIFE = 10 * 3600
TENDON_HALF_LIFE = 36 * 3600
REST_LOAD = 1.6
SORE_LOAD = 0.7
TENDON_SORE_LOAD = 0.75


def slider_top(most):
    step = 5 if most <= 40 else 10
    return -(-(most + step) // step) * step


def infer_exercise(name, group=None, muscles=None, tendons=None, timed=None, point=None):
    low = " ".join(str(name or "").lower().split())
    if not muscles:
        if any(w in low for w in ("incline", "bench", "chest", "pec", "fly", "push-up", "pushup", "dip")):
            muscles = _m(chest=1, triceps=0.7, shoulders=0.6)
        elif any(w in low for w in ("shoulder", "press", "overhead", "ohp", "delt", "lateral", "planche", "handstand")):
            muscles = _m(shoulders=1, triceps=0.7, chest=0.4)
        elif any(w in low for w in ("tricep", "skull", "pushdown", "extension")):
            muscles = _m(triceps=1)
        elif any(w in low for w in ("pull", "chin", "row", "lat", "lever", "hang", "climb", "boulder")):
            muscles = _m(lats=1, biceps=0.75, forearms=0.6, traps=0.5)
        elif any(w in low for w in ("curl", "bicep")):
            muscles = _m(biceps=1, forearms=0.5)
        elif any(w in low for w in ("forearm", "wrist", "grip", "pinch", "finger")):
            muscles = _m(forearms=1)
        elif any(w in low for w in ("squat", "lunge", "leg press", "quad", "step-up", "split")):
            muscles = _m(quads=1, glutes=0.8, hamstrings=0.35)
        elif any(w in low for w in ("deadlift", "rdl", "hamstring", "nordic", "hip thrust", "glute", "bridge")):
            muscles = _m(hamstrings=0.9, glutes=1, lower_back=0.5)
        elif any(w in low for w in ("calf", "calves", "heel raise")):
            muscles = _m(calves=1)
        elif any(w in low for w in ("ab", "core", "plank", "hollow", "crunch", "leg raise", "sit-up", "twist", "oblique", "dragon", "dead bug", "flutter", "v-up", "rollout")):
            muscles = _m(abs=1, hip_flexors=0.6, obliques=0.4)
        elif group == "pull":
            muscles = _m(lats=1, biceps=0.7, forearms=0.5)
        elif group == "legs":
            muscles = _m(quads=1, glutes=0.8, hamstrings=0.4)
        elif group == "core":
            muscles = _m(abs=1, obliques=0.5)
        elif group == "cardio":
            muscles = _m(quads=0.6, calves=0.6, abs=0.4)
        else:
            muscles = _m(chest=0.8, shoulders=0.6, triceps=0.6)
    if tendons is None:
        tendons = {}
        if muscles.get("forearms", 0) >= 0.5 or muscles.get("lats", 0) >= 0.8:
            tendons["medial elbow"] = 0.5
            tendons["finger pulleys"] = 0.45
        if muscles.get("shoulders", 0) >= 0.6 or muscles.get("chest", 0) >= 0.8:
            tendons["shoulder tendon"] = 0.5
        if muscles.get("quads", 0) >= 0.7:
            tendons["patellar tendon"] = 0.5
        if muscles.get("calves", 0) >= 0.7:
            tendons["achilles tendon"] = 0.5
    if timed is None:
        timed = any(w in low for w in ("hold", "plank", "hang", "sit", "stand", "lean", "lever"))
    entry = {
        "one": low.rstrip("s") if low.endswith("s") and not low.endswith("ss") else low,
        "scale": 1.5 if timed else 1,
        "timed": bool(timed),
        "muscles": {k: float(v) for k, v in muscles.items() if k in PART_OF or k in SUB_MUSCLES},
        "tendons": {k: float(v) for k, v in tendons.items() if k in TENDONS},
        "point": point or f"Targets {', '.join(list(muscles)[:3])}.",
        "custom": True,
    }
    entry["parts"] = roll_up(entry)
    return entry


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
    loaded = {}
    for name, meta in raw.items():
        if isinstance(name, str) and isinstance(meta, dict):
            key = " ".join(name.lower().split())
            loaded[key] = infer_exercise(
                key,
                group=meta.get("group"),
                muscles=meta.get("muscles"),
                tendons=meta.get("tendons"),
                timed=meta.get("timed"),
                point=meta.get("point"),
            )
    CUSTOM.update(loaded)
    return loaded


def register_custom(name, path=None, group=None, muscles=None, tendons=None, timed=None, point=None):
    key = " ".join(str(name or "").lower().split())
    if not key:
        return None
    entry = infer_exercise(key, group=group, muscles=muscles, tendons=tendons, timed=timed, point=point)
    CUSTOM[key] = entry
    if path is not None:
        p = Path(path)
        existing = {}
        if p.exists():
            try:
                existing = json.loads(p.read_text())
            except (OSError, ValueError):
                existing = {}
        if not isinstance(existing, dict):
            existing = {}
        existing[key] = {
            "muscles": entry["muscles"],
            "tendons": entry["tendons"],
            "timed": entry["timed"],
            "point": entry["point"],
        }
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(existing, indent=2) + "\n")
    return entry


def info(name, raw_meta=None):
    cur = current(name)
    found = EXERCISES.get(cur) or STRETCHES.get(cur) or LOGGED.get(cur) or CUSTOM.get(cur.lower() if isinstance(cur, str) else "")
    if found:
        return found
    if isinstance(raw_meta, dict) and raw_meta.get("muscles"):
        return infer_exercise(cur, muscles=raw_meta.get("muscles"), tendons=raw_meta.get("tendons"), timed=raw_meta.get("timed"), point=raw_meta.get("point"))
    return infer_exercise(cur)


def muscle_summary(name, limit=4):
    e = info(name)
    muscles = sorted(e.get("muscles", {}).items(), key=lambda kv: -kv[1])
    return ", ".join(m for m, _ in muscles[:limit])


def point_of(name):
    e = info(name)
    if e.get("point"):
        return e["point"]
    if e.get("stretch"):
        return f"Stretches {muscle_summary(name, 3)} for {GOALS.get(e.get('goal', ''), 'mobility')}."
    ms = muscle_summary(name, 3)
    return f"Hits {ms}." if ms else ""


def squash(text):
    return re.sub(r"[\s-]+", "", str(text or "").lower())


def candidates(text, known=None):
    known = list(known or (*EXERCISES, *STRETCHES, *LOGGED, *CUSTOM))
    key = squash(text)
    exact = [name for name in known if squash(name) == key]
    if exact:
        return exact
    if ALIASES.get(key) in known:
        return [ALIASES[key]]
    return [name for name in known if key and key in squash(name)]


def find(text, known=None):
    hits = candidates(text, known)
    return hits[0] if len(hits) == 1 else None


PUSH_UPS = ("push-ups", "decline push-ups", "wide push-ups", "diamond push-ups", "incline push-ups", "pseudo planche push-ups")


def did(entry):
    if entry.get("result") not in ("done", "manual"):
        return 0
    return entry.get("did", entry.get("reps", 0))


def exercise_of(entry):
    return current(entry.get("exercise", "push-ups"))


def is_stretch(name):
    return name in STRETCHES


def format_weight(weight_kg, weight_kind=None):
    if not isinstance(weight_kg, (int, float)) or isinstance(weight_kg, bool) or weight_kg <= 0:
        return ""
    num = int(weight_kg) if float(weight_kg).is_integer() else round(float(weight_kg), 1)
    if weight_kind == "added":
        return f"+{num} kg"
    if weight_kind == "assisted":
        return f"-{num} kg"
    return f"{num} kg"


def weight_factor(entry):
    w = entry.get("weight_kg")
    if not isinstance(w, (int, float)) or isinstance(w, bool) or w <= 0:
        return 1.0
    if entry.get("weight_kind") == "assisted":
        return max(0.5, 1.0 - min(0.5, w / 80.0))
    return 1.0 + min(1.5, float(w) / 60.0)


def is_heavy_entry(entry):
    if entry.get("result") == "session":
        return entry.get("effort") == "hard" or entry.get("activity") in ("climbing", "bouldering", "weights", "gym")
    w = entry.get("weight_kg")
    if isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0 and entry.get("weight_kind") != "assisted":
        return True
    e = info(exercise_of(entry), entry)
    return bool(e.get("weighted") or entry.get("effort") == "hard")


def unit(exercise, n, per=True):
    e = info(exercise)
    if e.get("stretch"):
        return ("second" if n == 1 else "seconds") + (" each side" if e.get("sided") and per else "")
    word = ("second of " if n == 1 else "seconds of ") + exercise if e.get("timed") else e["one"] if n == 1 else exercise
    return f"{word} {e['per']}" if e.get("per") and per else word


def each_side(exercise):
    per = info(exercise).get("per", "")
    return per.split(" ", 1)[1] if per.startswith("each ") else ""


def describe_set(name, n, weight_kg=None, weight_kind=None):
    base = f"{n} {unit(name, n)}" + (f" of {name}" if is_stretch(name) else "")
    w = format_weight(weight_kg, weight_kind)
    return f"{base} @ {w}" if w else base


def plural(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"


def day_lines(sets):
    totals, counts = {}, {}
    for item in sets:
        name, n = item[0], item[1]
        totals[name] = totals.get(name, 0) + n
        counts[name] = counts.get(name, 0) + 1
    return [describe_set(name, n) + (f" in {plural(counts[name], 'set')}" if counts[name] > 1 else "") for name, n in totals.items()]


WORK_PER_MINUTE = 2
WORK_FULL, STRETCH_FULL = 150, 600


def work_of(e):
    if e.get("result") == "session" and e.get("activity") in ACTIVITIES:
        amount = e.get("minutes", 0) * EFFORT.get(e.get("effort"), 1.0) * WORK_PER_MINUTE
        return {m: amount * w for m, w in ACTIVITIES[e["activity"]]["muscles"].items()}
    n = e.get("did", e.get("reps", 0))
    if e.get("result") in ("done", "manual") and n > 0:
        x = info(exercise_of(e), e)
        if not x.get("stretch"):
            wf = weight_factor(e)
            out = {}
            for m, w in x.get("muscles", {}).items():
                base = SUB_MUSCLES[m]["base"] if m in SUB_MUSCLES else m
                if base in PART_OF:
                    out[base] = out.get(base, 0.0) + n / x["scale"] * w * wf
            return out
    return {}


def stretch_of(e):
    if e.get("result") == "session" and e.get("activity") in ACTIVITIES:
        return {m: e.get("minutes", 0) * 60 * w * 0.3 for m, w in ACTIVITIES[e["activity"]].get("stretch", {}).items()}
    n = e.get("did", e.get("reps", 0))
    x = info(exercise_of(e), e)
    if e.get("result") in ("done", "manual") and n > 0 and x.get("stretch"):
        return {m: n * (2 if x.get("sided") else 1) * w for m, w in x["muscles"].items()}
    return {}


def muscle_week(entries, now, days=7):
    work, stretch = dict.fromkeys(MUSCLES, 0.0), dict.fromkeys(MUSCLES, 0.0)
    for e in entries:
        if not 0 <= now - e["ts"] < days * 86400:
            continue
        for m, v in work_of(e).items():
            if m in work:
                work[m] += v
        for m, v in stretch_of(e).items():
            if m in stretch:
                stretch[m] += v
    top_w = max(WORK_FULL, max(work.values()))
    top_s = max(STRETCH_FULL, max(stretch.values()))
    return ({m: v / top_w for m, v in work.items()}, {m: v / top_s for m, v in stretch.items()}, work, stretch)


def neglected(work, count=3):
    return sorted((m for m in MUSCLES if m != "forearms"), key=lambda m: work[m])[:count]


def balance_weight(name, work):
    muscles = info(name).get("muscles", {})
    total = sum(muscles.values())
    if not total:
        return 1.0
    return 1 + 2 * sum(w * (1 - min(1.0, work.get(m, 0.0))) for m, w in muscles.items()) / total


def sessions(entries):
    return [e for e in entries if e.get("result") == "session" and e.get("activity") in ACTIVITIES]


def session_load(e):
    return e.get("minutes", 0) / 60 * EFFORT.get(e.get("effort"), 1.0)


def _decay(age, half_life, max_days=4):
    return 0.5 ** (age / half_life) if 0 <= age < max_days * 86400 else 0.0


def faded(e, now):
    half_life = HEAVY_HALF_LIFE if is_heavy_entry(e) else RECOVERY_HALF_LIFE
    return _decay(now - e["ts"], half_life, 4)


def muscle_loads(entries, now):
    out = dict.fromkeys(MUSCLES, 0.0)
    for e in entries:
        age = now - e.get("ts", 0)
        if not 0 <= age < 4 * 86400:
            continue
        if e.get("result") == "session" and e.get("activity") in ACTIVITIES:
            act = ACTIVITIES[e["activity"]]
            sl = session_load(e)
            fade = faded(e, now)
            for m, w in act["muscles"].items():
                if m in out:
                    out[m] += sl * w * fade
        elif e.get("result") in ("done", "manual") and did(e) > 0:
            x = info(exercise_of(e), e)
            if x.get("stretch"):
                continue
            heavy = is_heavy_entry(e)
            fade = _decay(age, HEAVY_HALF_LIFE if heavy else LIGHT_HALF_LIFE, 3 if heavy else 2)
            base_units = did(e) / x["scale"] * weight_factor(e)
            set_load = (base_units / 100.0) * (1.2 if heavy else 0.55)
            for m, w in x.get("muscles", {}).items():
                base = SUB_MUSCLES[m]["base"] if m in SUB_MUSCLES else m
                if base in out:
                    out[base] += set_load * w * fade
    return out


def tendon_loads(entries, now):
    out = dict.fromkeys(TENDONS, 0.0)
    for e in entries:
        age = now - e.get("ts", 0)
        if not 0 <= age < 5 * 86400:
            continue
        fade = _decay(age, TENDON_HALF_LIFE, 5)
        if e.get("result") == "session" and e.get("activity") in ACTIVITIES:
            sl = session_load(e)
            for t, w in ACTIVITIES[e["activity"]].get("tendons", {}).items():
                if t in out:
                    out[t] += sl * w * fade
        elif e.get("result") in ("done", "manual") and did(e) > 0:
            x = info(exercise_of(e), e)
            if x.get("stretch"):
                continue
            heavy = is_heavy_entry(e)
            base_units = did(e) / x["scale"] * weight_factor(e)
            set_load = (base_units / 130.0) * (1.1 if heavy else 0.35)
            for t, w in x.get("tendons", {}).items():
                if t in out:
                    out[t] += set_load * w * fade
    return out


def loads(entries, now):
    out = dict.fromkeys(PARTS, 0.0)
    ml = muscle_loads(entries, now)
    for m, v in ml.items():
        p = PART_OF[m]
        out[p] = max(out[p], v)
    for e in sessions(entries):
        fade = faded(e, now)
        cardio = ACTIVITIES[e["activity"]].get("cardio", 0)
        if cardio:
            out["cardio"] += session_load(e) * cardio * fade
    return out


TRAINING_DAY = 0.5


def training_day(e):
    return session_load(e) * max(ACTIVITIES[e["activity"]]["parts"].values(), default=0) >= TRAINING_DAY


def resting(entries, now):
    load = loads(entries, now)
    part = max(load, key=load.get)
    if load[part] < REST_LOAD:
        return None
    until = now + HEAVY_HALF_LIFE * math.log2(load[part] / REST_LOAD)
    recent = [e for e in sessions(entries) if faded(e, now)]
    heaviest = max(recent, key=lambda e: session_load(e) * faded(e, now), default=None)
    return until, describe(heaviest) if heaviest else "heavy training"


def describe(e):
    mins = e.get("minutes", 0)
    length = f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 and mins % 60 else f"{mins // 60}h" if mins >= 60 else f"{mins} min"
    effort = "" if e.get("effort", "normal") == "normal" else f" {e['effort']}"
    label = e.get("label")
    name = label if isinstance(label, str) and label else ACTIVITIES[e["activity"]]["name"]
    return f"{name.lower()} {length}{effort}"


def sore(exercise, load, t_load=None):
    e = info(exercise)
    for part, w in sorted(e["parts"].items(), key=lambda kv: -kv[1]):
        if w >= 0.7 and load.get(part, 0) >= SORE_LOAD:
            return part
    if t_load:
        for tendon, w in sorted(e.get("tendons", {}).items(), key=lambda kv: -kv[1]):
            if w >= 0.65 and t_load.get(tendon, 0) >= TENDON_SORE_LOAD:
                return TENDONS[tendon]["part"]
    return None


def recovery_factor(exercise, load, t_load=None):
    e = info(exercise)
    worst = max((w * load.get(p, 0) for p, w in e["parts"].items()), default=0)
    if t_load:
        worst = max(worst, max((w * t_load.get(t, 0) for t, w in e.get("tendons", {}).items()), default=0))
    return math.exp(-0.35 * worst)
