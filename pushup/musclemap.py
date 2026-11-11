"""A body map: front and back, each muscle lit by how much it got.

The body is drawn on a 40 by 84 grid (y down) from polygons, left side only; the right side is a
mirror. Like the figures, it is drawn without antialiasing on a small surface and scaled up whole
pixels at a time, so it sits with the Monocraft type.

Worked muscles glow warm, stretched ones cool. A map takes {muscle: amount from 0 to 1} for each.
"""

import cairo

GW, GH = 40, 84
CX = 20  # the body's midline

from catalog import MUSCLES  # noqa: E402,F401  (the map draws exactly the catalog's muscles)

SKIN = (0.149, 0.169, 0.192)  # bg4: the body where no muscle is drawn
IDLE = (0.227, 0.255, 0.286)  # bg5: a muscle that got nothing
EDGE = (0.094, 0.106, 0.122)  # bg2: the seams between muscles
WORK = (1.0, 0.541, 0.290)  # warm
STRETCH = (0.373, 0.816, 0.753)  # cool
BOTH = (0.94, 0.86, 0.56)  # a muscle that was both worked and stretched leans pale gold
PAIN = (1.0, 0.36, 0.36)  # a spot you said hurts
HOVER = (0.42, 0.46, 0.51)  # the spot under the pointer
PAIN_HOVER = (1.0, 0.58, 0.58)

# Left half only, x from the left edge, y from the top. Shapes overlap in drawing order.
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

# The silhouette, left half: head, neck, torso, arm, leg, as filled shapes.
BODY = [
    [(17, 2), (23, 2), (24.5, 5), (24, 9), (16, 9), (15.5, 5)],  # head (both halves at once)
    [(18, 9), (19.9, 9), (19.9, 13), (18, 13)],  # neck
    [(19.9, 12.5), (13, 13.5), (9, 15), (8, 21), (8.5, 30), (9, 39), (12, 39), (12.8, 30), (13.2, 23),
     (13.5, 37), (13, 44), (13.6, 56), (14, 62), (14.2, 72), (14.6, 79), (18, 79), (18.2, 72),
     (18.6, 62), (19.6, 50), (19.9, 42), (19.9, 12.5)],
    [(10, 39), (12, 39), (12.4, 42.5), (10.2, 43), (9.4, 41)],  # hand
]


def mirror(points):
    return [(2 * CX - x, y) for x, y in points]


def color_for(work, stretch):
    w, s = min(1.0, work), min(1.0, stretch)
    if w <= 0.02 and s <= 0.02:
        return IDLE
    # Mostly one or the other: blend toward it; plenty of both: lean gold.
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
    """Which of the person's sides the drawn half and its mirror are. The front faces you, so its
    drawn (viewer's left) half is their right; the back's drawn half is their left."""
    return ("right", "left") if shapes is FRONT else ("left", "right")


def draw_view(cr, shapes, work, stretch, marks=None):
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    cr.set_source_rgb(*SKIN)
    for poly in BODY:
        for pts in (poly, mirror(poly)):
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.close_path()
            cr.fill()
    marks = marks or {}
    for name, poly in shapes:
        for pts, side in zip((poly, mirror(poly)), sides(shapes)):
            cr.set_source_rgb(*marks.get((name, side), color_for(work.get(name, 0), stretch.get(name, 0))))
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.close_path()
            cr.fill_preserve()
            cr.set_source_rgb(*EDGE)
            cr.set_line_width(0.5)
            cr.stroke()
    if shapes is FRONT:
        # The six-pack's seams, so the abs read as abs.
        cr.set_source_rgb(*EDGE)
        cr.set_line_width(0.5)
        for y in (28, 32.5):
            cr.move_to(16, y)
            cr.line_to(2 * CX - 16, y)
        cr.move_to(CX, 24)
        cr.line_to(CX, 38)
        cr.stroke()


RES = 2  # pixels per grid unit on the small surface: fine enough for the seams to read


def draw(cr, work=None, stretch=None, scale=4, gap=6, marks=None):
    """Front and back side by side, gap grid units apart. scale is screen pixels per grid unit,
    best a multiple of RES. Size: size(scale, gap). marks colours single spots, {(muscle, side): rgb}."""
    work, stretch = work or {}, stretch or {}
    w = 2 * GW + gap
    res = RES if scale % RES == 0 else scale  # odd scales draw at full size, no stretching
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * res, GH * res)
    c = cairo.Context(small)
    c.scale(res, res)
    draw_view(c, FRONT, work, stretch, marks)
    c.translate(GW + gap, 0)
    draw_view(c, BACK, work, stretch, marks)
    cr.save()
    cr.scale(scale / res, scale / res)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_NEAREST)
    cr.paint()
    cr.restore()


def inside(pts, x, y):
    """Even-odd point in polygon."""
    hit = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def hit(x, y, gap=6):
    """The (muscle, side) at a point on the map, in grid units, or None. The shape drawn last is
    on top, so it is asked first."""
    shapes, x = (FRONT, x) if x < GW else (BACK, x - GW - gap) if x >= GW + gap else (None, x)
    for name, poly in reversed(shapes or []):
        for pts, side in zip((poly, mirror(poly)), sides(shapes)):
            if inside(pts, x, y):
                return name, side
    return None


def size(scale=4, gap=6):
    return (2 * GW + gap) * scale, GH * scale


if __name__ == "__main__":
    import sys
    sw, sh = size(4)
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, sw * 3 + 40, sh + 20)
    cr = cairo.Context(surf)
    cr.set_source_rgb(0.094, 0.106, 0.122)
    cr.paint()
    demos = [({m: 1 for m in MUSCLES}, {}), ({"chest": 1, "triceps": 0.8, "shoulders": 0.6, "abs": 0.4}, {}),
             ({"quads": 0.5}, {"hamstrings": 1, "hip flexors": 0.9, "adductors": 0.6, "calves": 0.4, "quads": 0.5})]
    for i, (w, s) in enumerate(demos):
        cr.save()
        cr.translate(10 + i * (sw + 10), 10)
        draw(cr, w, s, 4)
        cr.restore()
    surf.write_to_png(sys.argv[1] if len(sys.argv) > 1 else "captures/musclemap.png")
