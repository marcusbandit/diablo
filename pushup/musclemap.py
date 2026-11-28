try:
    import cairo
except ImportError:
    cairo = None

GW, GH = 40, 84
CX = 20
RW, RH = 64, 64

from catalog import MUSCLES, REGIONS, SUB_MUSCLES, TENDONS

SKIN = (0.149, 0.169, 0.192)
IDLE = (0.227, 0.255, 0.286)
EDGE = (0.094, 0.106, 0.122)
WORK = (1.0, 0.541, 0.290)
STRETCH = (0.373, 0.816, 0.753)
BOTH = (0.94, 0.86, 0.56)
PAIN = (1.0, 0.36, 0.36)
HOVER = (0.42, 0.46, 0.51)
PAIN_HOVER = (1.0, 0.58, 0.58)
TENDON_IDLE = (0.28, 0.56, 0.54)
TENDON_HOVER = (0.46, 0.88, 0.82)
TENDON_PAIN = (1.0, 0.32, 0.42)
REGION_TINT = (0.30, 0.36, 0.42)

FRONT = [
    ("shoulders", [(13, 14), (9.5, 15.5), (8.5, 20), (11, 21), (13.5, 18)]),
    ("chest", [(19.5, 15), (13.5, 15), (12.5, 19), (14, 23), (19.5, 23)]),
    ("biceps", [(9, 21), (12.5, 21), (12, 28.5), (9.5, 28.5)]),
    ("forearms", [(9.2, 29.5), (12.2, 29.5), (11.7, 38), (10, 38)]),
    ("abs", [(19.5, 24), (16, 24), (16, 37), (19.5, 38.5)]),
    ("obliques", [(15.2, 23.5), (13.2, 23), (13.6, 30), (14.4, 36), (15.2, 36.5)]),
    ("hip flexors", [(14.2, 37), (18.5, 39.5), (18, 44), (14, 41.5)]),
    ("quads", [(13.5, 42.5), (17.5, 45), (18.5, 50), (17.8, 61), (15, 61.5), (13.8, 53)]),
    ("adductors", [(19.6, 41), (18.6, 44.5), (19.2, 54), (19.8, 53)]),
    ("calves", [(14.6, 65), (17.6, 65), (17.4, 77), (15.4, 77)]),
]
BACK = [
    ("traps", [(19.6, 11.5), (14, 15), (17, 18), (19.6, 26)]),
    ("shoulders", [(13, 14), (9.5, 15.5), (8.5, 20), (11, 21), (13.5, 18)]),
    ("triceps", [(9, 21), (12.5, 21), (12, 28.5), (9.5, 28.5)]),
    ("forearms", [(9.2, 29.5), (12.2, 29.5), (11.7, 38), (10, 38)]),
    ("lats", [(13.8, 18.5), (16.8, 19), (19.4, 27.5), (19.4, 31), (15, 31.5), (13.4, 25)]),
    ("lower back", [(19.6, 31.5), (16, 32), (15.8, 37.5), (19.6, 38)]),
    ("glutes", [(19.6, 38.5), (14.2, 38), (13.6, 43), (15, 46.5), (19.6, 46)]),
    ("hamstrings", [(14, 47.5), (19.4, 47), (18.6, 61), (15, 61)]),
    ("calves", [(14.4, 63.5), (17.8, 63.5), (18, 70), (17.2, 76), (15.6, 76), (14, 69)]),
]

TENDON_FRONT = [
    ("shoulder tendon", (11.5, 16.5)),
    ("biceps tendon", (11.0, 28.8)),
    ("wrist tendons", (11.0, 38.8)),
    ("finger pulleys", (11.0, 42.0)),
    ("hip flexor tendon", (16.2, 40.5)),
    ("patellar tendon", (16.2, 62.5)),
]
TENDON_BACK = [
    ("shoulder tendon", (11.5, 16.5)),
    ("medial elbow", (12.2, 29.0)),
    ("lateral elbow", (9.2, 29.0)),
    ("triceps tendon", (10.7, 27.2)),
    ("hamstring tendon", (16.5, 47.2)),
    ("achilles tendon", (16.2, 77.2)),
]

REGION_OF_MUSCLE = {
    "shoulders": "shoulder", "traps": "shoulder",
    "chest": "torso", "lats": "torso", "lower back": "torso",
    "biceps": "arm", "triceps": "arm", "forearms": "arm",
    "abs": "core", "obliques": "core", "hip flexors": "core", "glutes": "core",
    "quads": "thigh", "hamstrings": "thigh", "adductors": "thigh",
    "calves": "lower_leg",
}

BODY = [
    [(17, 2), (23, 2), (24.5, 5), (24, 9), (16, 9), (15.5, 5)],
    [(18, 9), (19.9, 9), (19.9, 13), (18, 13)],
    [(19.9, 12.5), (13, 13.5), (9, 15), (8, 21), (8.5, 30), (9, 39), (12, 39), (12.8, 30), (13.2, 23),
     (13.5, 37), (13, 44), (13.6, 56), (14, 62), (14.2, 72), (14.6, 79), (18, 79), (18.2, 72),
     (18.6, 62), (19.6, 50), (19.9, 42), (19.9, 12.5)],
    [(10, 39), (12, 39), (12.4, 42.5), (10.2, 43), (9.4, 41)],
]

REGION_SHAPES = {
    "shoulder": {
        "silhouette": [[(18, 8), (46, 8), (54, 18), (56, 36), (52, 56), (38, 56), (34, 38), (18, 42), (12, 24)]],
        "muscles": [
            ("upper traps", [(20, 9), (40, 9), (34, 20), (16, 20)]),
            ("front delts", [(16, 22), (32, 20), (34, 38), (18, 38)]),
            ("side delts", [(33, 20), (46, 20), (48, 40), (35, 40)]),
            ("rear delts", [(47, 22), (55, 24), (53, 42), (48, 42)]),
        ],
        "tendons": [
            ("shoulder tendon", (34, 24), 4.5),
        ],
    },
    "arm": {
        "silhouette": [[(20, 6), (44, 6), (46, 28), (42, 34), (44, 58), (22, 58), (24, 34), (18, 28)]],
        "muscles": [
            ("biceps", [(21, 8), (32, 8), (32, 27), (20, 27)]),
            ("brachialis", [(20, 27), (28, 27), (28, 34), (20, 34)]),
            ("triceps long head", [(33, 8), (43, 8), (43, 26), (33, 26)]),
            ("triceps lateral head", [(35, 16), (45, 16), (45, 29), (35, 29)]),
            ("forearm flexors", [(22, 36), (33, 36), (32, 56), (23, 56)]),
            ("forearm extensors", [(34, 36), (44, 36), (43, 56), (34, 56)]),
        ],
        "tendons": [
            ("biceps tendon", (28, 32), 3.8),
            ("triceps tendon", (38, 29), 3.8),
            ("medial elbow", (22, 34), 3.8),
            ("lateral elbow", (43, 34), 3.8),
        ],
    },
    "hand": {
        "silhouette": [
            [(22, 42), (42, 42), (44, 58), (20, 58)],
            [(18, 24), (46, 24), (44, 42), (20, 42)],
            [(18, 10), (23, 10), (23, 25), (18, 25)],
            [(25, 6), (30, 6), (30, 25), (25, 25)],
            [(32, 7), (37, 7), (37, 25), (32, 25)],
            [(39, 11), (44, 11), (44, 25), (39, 25)],
            [(11, 28), (18, 26), (19, 36), (12, 37)],
        ],
        "muscles": [
            ("forearm flexors", [(22, 44), (32, 44), (32, 57), (21, 57)]),
            ("forearm extensors", [(33, 44), (43, 44), (43, 57), (33, 57)]),
        ],
        "tendons": [
            ("finger pulleys", (31, 18), 5.0),
            ("wrist tendons", (32, 41), 4.8),
        ],
    },
    "torso": {
        "silhouette": [[(12, 8), (52, 8), (54, 28), (48, 56), (16, 56), (10, 28)]],
        "muscles": [
            ("upper chest", [(14, 10), (31, 10), (31, 19), (13, 19)]),
            ("mid chest", [(13, 20), (31, 20), (31, 29), (14, 29)]),
            ("lower chest", [(15, 30), (31, 30), (31, 36), (16, 36)]),
            ("serratus", [(13, 37), (24, 37), (24, 48), (16, 48)]),
            ("mid traps", [(34, 10), (50, 10), (50, 24), (34, 24)]),
            ("lats", [(34, 25), (52, 25), (48, 42), (34, 42)]),
            ("lower back", [(34, 43), (48, 43), (46, 55), (34, 55)]),
        ],
        "tendons": [],
    },
    "core": {
        "silhouette": [[(14, 8), (50, 8), (52, 34), (48, 56), (16, 56), (12, 34)]],
        "muscles": [
            ("upper abs", [(22, 10), (42, 10), (42, 22), (22, 22)]),
            ("lower abs", [(23, 23), (41, 23), (40, 36), (24, 36)]),
            ("obliques", [(14, 12), (21, 12), (22, 35), (14, 34)]),
            ("hip flexors", [(16, 37), (31, 38), (30, 48), (16, 46)]),
            ("glutes", [(33, 38), (49, 38), (47, 54), (33, 54)]),
            ("glute medius", [(43, 30), (51, 30), (51, 38), (43, 38)]),
        ],
        "tendons": [
            ("hip flexor tendon", (24, 42), 4.2),
        ],
    },
    "thigh": {
        "silhouette": [[(18, 6), (46, 6), (48, 36), (44, 58), (20, 58), (16, 36)]],
        "muscles": [
            ("quads", [(18, 8), (32, 8), (33, 44), (20, 44)]),
            ("adductors", [(33, 8), (40, 8), (39, 36), (33, 36)]),
            ("hamstrings", [(40, 10), (47, 10), (45, 44), (39, 44)]),
        ],
        "tendons": [
            ("patellar tendon", (27, 49), 4.8),
            ("hamstring tendon", (42, 46), 4.2),
        ],
    },
    "lower_leg": {
        "silhouette": [[(20, 8), (44, 8), (46, 28), (40, 52), (44, 58), (20, 58), (22, 50), (18, 28)]],
        "muscles": [
            ("tibialis", [(20, 10), (30, 10), (30, 44), (22, 44)]),
            ("calves", [(31, 10), (44, 10), (42, 40), (31, 40)]),
        ],
        "tendons": [
            ("achilles tendon", (37, 48), 4.8),
        ],
    },
}


def mirror(points):
    return [(2 * CX - x, y) for x, y in points]


def color_for(work, stretch):
    w, s = min(1.0, work), min(1.0, stretch)
    if w <= 0.02 and s <= 0.02:
        return IDLE
    if w > 0.02 and s > 0.02:
        tint = BOTH
        amount = max(w, s)
    elif w >= s:
        tint, amount = WORK, w
    else:
        tint, amount = STRETCH, s
    k = 0.25 + 0.75 * amount
    return tuple(a + (b - a) * k for a, b in zip(IDLE, tint))


def sides(shapes):
    return ("right", "left") if shapes is FRONT else ("left", "right")


def diamond(x, y, r=2.0):
    return [(x, y - r), (x + r, y), (x, y + r), (x - r, y)]


def draw_poly(cr, pts, fill, stroke=EDGE, lw=0.5):
    cr.set_source_rgb(*fill)
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.close_path()
    if stroke is not None:
        cr.fill_preserve()
        cr.set_source_rgb(*stroke)
        cr.set_line_width(lw)
        cr.stroke()
    else:
        cr.fill()


def draw_view(cr, shapes, work, stretch, marks=None, tendons=None, tendon_marks=None, dim_muscles=False):
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    cr.set_source_rgb(*SKIN)
    for poly in BODY:
        for pts in (poly, mirror(poly)):
            draw_poly(cr, pts, SKIN, stroke=None)
    marks = marks or {}
    for name, poly in shapes:
        for pts, side in zip((poly, mirror(poly)), sides(shapes)):
            col = marks.get((name, side), IDLE if dim_muscles else color_for(work.get(name, 0), stretch.get(name, 0)))
            draw_poly(cr, pts, col, EDGE, 0.5)
    if shapes is FRONT:
        cr.set_source_rgb(*EDGE)
        cr.set_line_width(0.5)
        for y in (28, 32.5):
            cr.move_to(16, y)
            cr.line_to(2 * CX - 16, y)
        cr.move_to(CX, 24)
        cr.line_to(CX, 38)
        cr.stroke()
    if tendons:
        tendon_marks = tendon_marks or {}
        view_sides = ("right", "left") if shapes is FRONT else ("left", "right")
        for tname, (tx, ty) in tendons:
            for pt, side in zip(((tx, ty), (2 * CX - tx, ty)), view_sides):
                col = tendon_marks.get((tname, side), TENDON_IDLE)
                draw_poly(cr, diamond(pt[0], pt[1], 1.9), col, EDGE, 0.5)


RES = 2


def draw(cr, work=None, stretch=None, scale=4, gap=6, marks=None, layer="muscles", tendon_marks=None):
    work, stretch = work or {}, stretch or {}
    w = 2 * GW + gap
    res = RES if scale % RES == 0 else scale
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * res, GH * res)
    c = cairo.Context(small)
    c.scale(res, res)
    show_tendons = layer == "tendons" or bool(tendon_marks)
    draw_view(c, FRONT, work, stretch, marks, TENDON_FRONT if show_tendons else None, tendon_marks, dim_muscles=(layer == "tendons"))
    c.translate(GW + gap, 0)
    draw_view(c, BACK, work, stretch, marks, TENDON_BACK if show_tendons else None, tendon_marks, dim_muscles=(layer == "tendons"))
    cr.save()
    cr.scale(scale / res, scale / res)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_NEAREST)
    cr.paint()
    cr.restore()


def inside(pts, x, y):
    hit_flag = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit_flag = not hit_flag
    return hit_flag


def hit(x, y, gap=6, layer="muscles"):
    is_front = x < GW
    is_back = x >= GW + gap
    if not (is_front or is_back):
        return None
    lx = x if is_front else x - GW - gap
    if layer == "tendons":
        tlist = TENDON_FRONT if is_front else TENDON_BACK
        view_sides = ("right", "left") if is_front else ("left", "right")
        best = None
        for tname, (tx, ty) in tlist:
            for (px, py), side in zip(((tx, ty), (2 * CX - tx, ty)), view_sides):
                d = ((lx - px) ** 2 + (y - py) ** 2) ** 0.5
                if d <= 2.8 and (best is None or d < best[0]):
                    best = (d, (tname, side, "tendon"))
        if best:
            return best[1]
    shapes = FRONT if is_front else BACK
    for name, poly in reversed(shapes):
        for pts, side in zip((poly, mirror(poly)), sides(shapes)):
            if inside(pts, lx, y):
                return name, side
    if is_front:
        for pts, side in zip((BODY[3], mirror(BODY[3])), ("right", "left")):
            if inside(pts, lx, y):
                return ("finger pulleys", side, "tendon") if layer == "tendons" else ("forearms", side)
    return None


def region_at(x, y, gap=6):
    is_front = x < GW
    is_back = x >= GW + gap
    if not (is_front or is_back):
        return None
    lx = x if is_front else x - GW - gap
    view_sides = ("right", "left") if is_front else ("left", "right")
    for pts, side in zip((BODY[3], mirror(BODY[3])), view_sides):
        if inside(pts, lx, y):
            return "hand", side
    found = hit(x, y, gap=gap, layer="muscles")
    if not found:
        return None
    name, side = found[0], found[1]
    return REGION_OF_MUSCLE.get(name, "torso"), side


def region_items(region):
    r = REGIONS.get(region)
    if not r:
        return []
    out = []
    for m in r["muscles"]:
        out.append((m, SUB_MUSCLES[m]["label"], "muscle"))
    for t in r["tendons"]:
        out.append((t, TENDONS[t]["label"], "tendon"))
    return out


def draw_region(cr, region, side, scale=4, picked=(), hot=None):
    spec = REGION_SHAPES.get(region)
    if not spec:
        return
    picked_set = {(s[0], s[1], s[2] if len(s) > 2 else "muscle") for s in picked}
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, RW * RES, RH * RES)
    c = cairo.Context(small)
    c.scale(RES, RES)
    c.set_antialias(cairo.ANTIALIAS_NONE)
    for poly in spec["silhouette"]:
        draw_poly(c, poly, SKIN, stroke=None)
    for name, poly in spec["muscles"]:
        is_p = (name, side, "muscle") in picked_set
        is_h = hot == (name, side, "muscle")
        col = PAIN_HOVER if (is_p and is_h) else PAIN if is_p else HOVER if is_h else IDLE
        draw_poly(c, poly, col, EDGE, 0.7)
    for name, (tx, ty), rad in spec["tendons"]:
        is_p = (name, side, "tendon") in picked_set
        is_h = hot == (name, side, "tendon")
        col = PAIN_HOVER if (is_p and is_h) else TENDON_PAIN if is_p else TENDON_HOVER if is_h else TENDON_IDLE
        draw_poly(c, diamond(tx, ty, rad), col, EDGE, 0.8)
    cr.save()
    cr.scale(scale / RES, scale / RES)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_NEAREST)
    cr.paint()
    cr.restore()


def hit_region(region, side, x, y):
    spec = REGION_SHAPES.get(region)
    if not spec:
        return None
    for name, (tx, ty), rad in reversed(spec["tendons"]):
        if abs(x - tx) + abs(y - ty) <= rad + 1.2:
            return (name, side, "tendon")
    for name, poly in reversed(spec["muscles"]):
        if inside(poly, x, y):
            return (name, side, "muscle")
    return None


def size(scale=4, gap=6):
    return (2 * GW + gap) * scale, GH * scale


def region_size(scale=4):
    return RW * scale, RH * scale
