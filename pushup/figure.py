"""Pixel figures that show how a move goes.

A figure is a skeleton posed by keyframes: where the hip sits, the angle of the torso and head,
and where the hands and feet are. Elbows and knees come from two-bone IK, so a hand stays on the
floor while the shoulder above it moves. Frames are drawn on a small surface without
antialiasing and scaled up whole pixels at a time, so they match the card's Monocraft type.

Coordinates are figure units: x to the right, y up from the floor. Angles are degrees, 0 to the
right and 90 straight up. Side-view figures face right. A figure lying on its back with its head
to the right is a mirror image of that, so its elbows and knees bend the other way: those moves
are marked supine.

Hand and foot targets come in three kinds:
  (x, y)                  a fixed point, for a hand planted on the floor
  ("torso", at, out)      a point `at` of the way up the torso and `out` in front, hands on hips
  ("arm", angle, reach)   swung from the shoulder (or "leg" from the hip): a limb that moves
                          through the air stays straight instead of folding between keyframes
"""

import math

import cairo

W, H = 96, 64  # the canvas, in figure units
GROUND = 4  # how far the floor line sits above the bottom edge

THIGH, SHIN, FOOT = 11, 11, 4
STEP = 1.5  # a foot that moves further than this between keyframes steps instead of sliding
TORSO, NECK, HEAD = 15, 2, 4
UPPER, FORE = 9, 9
ARM, LEG = UPPER + FORE, THIGH + SHIN

FG = (0.933, 0.949, 0.961)  # fg10
FAR = (0.471, 0.514, 0.561)  # fg7
PROP = (0.329, 0.365, 0.404)  # fg6
FLOOR = (0.227, 0.255, 0.286)  # bg5


def rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def polar(p, length, deg):
    a = math.radians(deg)
    return (p[0] + length * math.cos(a), p[1] + length * math.sin(a))


def two_bone(root, target, l1, l2, bend):
    """The middle joint of a limb from root to target. bend +1 swings it counterclockwise of the
    root-to-target line, -1 clockwise. Out of reach, the limb straightens toward the target."""
    dx, dy = target[0] - root[0], target[1] - root[1]
    d = max(abs(l1 - l2) + 1e-3, min(l1 + l2 - 1e-3, math.hypot(dx, dy)))
    base = math.atan2(dy, dx)
    off = math.acos(max(-1.0, min(1.0, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))))
    mid = (root[0] + l1 * math.cos(base + bend * off), root[1] + l1 * math.sin(base + bend * off))
    end = polar(mid, l2, math.degrees(math.atan2(target[1] - mid[1], target[0] - mid[0])))
    return mid, end


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def lerp(a, b, t):
    if isinstance(a, (tuple, list)):
        return tuple(lerp(x, y, t) for x, y in zip(a, b))
    return a + (b - a) * t


def angle_lerp(a, b, t):
    return a + ((b - a + 180) % 360 - 180) * t


# Facing right, an elbow flexes counterclockwise and a knee clockwise; for IK that means
# bend -1 for elbows and +1 for knees. Supine figures are mirrored and take the opposite.
UPRIGHT = {"elbows": (-1, -1), "knees": (1, 1)}
SUPINE = {"elbows": (1, 1), "knees": (-1, -1)}
BASE = {"head": None, "toes": (0, 0), "far": (1.5, 0), "fold": 0}


def pose(supine=False, **kw):
    p = dict(BASE)
    p.update(SUPINE if supine else UPRIGHT)
    p.update(kw)
    return p


def blend(a, b, t, lift=0.45):
    out = {}
    for k in a:
        if k in ("elbows", "knees", "far"):
            out[k] = a[k] if t < 0.5 else b[k]
        elif k == "torso":
            out[k] = angle_lerp(a[k], b[k], t)
        elif k == "head":
            ha = a[k] if a[k] is not None else a["torso"]
            hb = b[k] if b[k] is not None else b["torso"]
            out[k] = angle_lerp(ha, hb, t)
        elif k == "toes":
            out[k] = tuple(angle_lerp(x, y, t) for x, y in zip(a[k], b[k]))
        elif k == "hands":
            out[k] = tuple(blend_target(x, y, t) for x, y in zip(a[k], b[k]))
        elif k == "feet":
            out[k] = tuple(lifted(x, y, t, lift) if lift else blend_target(x, y, t) for x, y in zip(a[k], b[k]))
        else:
            out[k] = lerp(a[k], b[k], t)
    return out


def blend_target(a, b, t):
    if isinstance(a[0], str) and a[0] == b[0]:
        if a[0] in ("arm", "leg"):
            return (a[0], angle_lerp(a[1], b[1], t), lerp(a[2], b[2], t))
        return (a[0], lerp(a[1], b[1], t), lerp(a[2], b[2], t))
    if isinstance(a[0], str) or isinstance(b[0], str):
        # Two kinds, an arm swinging down to a hand planted on the floor: resolve both, then blend.
        return ("mix", a, b, t)
    return lerp(a, b, t)


def lifted(a, b, t, lift=0.45):
    """A foot going from one spot to another steps there: it lifts in an arc on the way instead
    of sliding along the floor. A foot that stays put stays put."""
    if isinstance(a[0], str) or isinstance(b[0], str) or abs(b[0] - a[0]) < STEP:
        return blend_target(a, b, t)
    # Up first, across, then down: the foot leaves and lands straight, never skimming the floor.
    out = lerp(a, b, ease(min(1, max(0, (t - 0.2) / 0.6))))
    return (out[0], out[1] + min(6, lift * abs(b[0] - a[0])) * math.sin(math.pi * t))


def tip(x, angle, y=0):
    """The ankle of a foot whose toe tip rests at (x, y): rising onto the toes pivots the foot
    there, so the tip stays where it was on the floor."""
    return polar((x, y), FOOT, angle + 180)


def resolve(target, root, hip, shoulder, torso):
    """A target as a point. root is the shoulder or hip the limb hangs from."""
    if target[0] == "mix":
        _, a, b, t = target
        return lerp(resolve(a, root, hip, shoulder, torso), resolve(b, root, hip, shoulder, torso), t)
    if isinstance(target[0], str):
        kind, a, b = target
        if kind == "torso":
            return polar(lerp(hip, shoulder, a), b, torso - 90)
        return polar(root, b, a)
    return target


class Move:
    """One move's loop: keyframes as (pose, hold, move) seconds, plus what lights up. glide is for
    moves done in place, a run or a bike, where the feet travel without stepping. lift is how high a
    stepping foot rises for each unit it travels."""

    def __init__(self, frames, work=(), view="side", props=(), plate=False, glide=False, lift=0.45):
        self.frames, self.work, self.view, self.props, self.plate = frames, set(work), view, props, plate
        self.glide, self.lift = glide, lift
        self.period = sum(h + m for _, h, m in frames)

    def at(self, t):
        t %= self.period
        for i, (p, hold, move) in enumerate(self.frames):
            if t < hold:
                return p
            t -= hold
            if t < move:
                return blend(p, self.frames[(i + 1) % len(self.frames)][0], ease(t / move), 0 if self.glide else self.lift)
            t -= move
        return self.frames[0][0]


def skeleton(p, front=False):
    """Joint positions for a pose: the spine, and per side an arm and a leg as lists of points."""
    hip, torso = p["hip"], p["torso"]
    # Seen from the front, a fold toward you shortens the torso instead of tilting it.
    squash = math.cos(math.radians(p["fold"])) if front else 1
    shoulder = polar(hip, TORSO * squash, torso)
    head_angle = p["head"] if p["head"] is not None else torso
    neck = polar(shoulder, NECK * squash, head_angle)
    head = polar(neck, HEAD * max(squash, 0.6), head_angle)
    limbs = {}
    for side in (0, 1):
        # Front view: shoulders and hips spread across the body, square to the spine.
        spread = (-1 if side == 0 else 1) if front else 0
        sh = polar(shoulder, 3 * spread, torso - 90) if front else shoulder
        hp = polar(hip, 2 * spread, torso - 90) if front else hip
        hand = resolve(p["hands"][side], sh, hip, shoulder, torso)
        foot = resolve(p["feet"][side], hp, hip, shoulder, torso)
        elbow, wrist = two_bone(sh, hand, UPPER, FORE, p["elbows"][side])
        knee, ankle = two_bone(hp, foot, THIGH, SHIN, p["knees"][side])
        toe = polar(ankle, FOOT, p["toes"][side])
        limbs[side] = {"arm": [sh, elbow, wrist], "leg": [hp, knee, ankle, toe]}
    return {"hip": hip, "shoulder": shoulder, "neck": neck, "head": head, "limbs": limbs}


def fill_disc(c, cx, cy, r2, reach):
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            if dx * dx + dy * dy <= r2:
                c.rectangle(cx + dx, cy + dy, 1, 1)
    c.fill()


def draw_props(cr, props, to):
    cr.set_source_rgb(*PROP)
    for kind, *a in props:
        if kind in ("chair", "bar"):
            # A chair: seat from x0 to x1 at height h, backrest on the left. A bar is the same
            # without the backrest: a pull-up bar, dip bars, a table edge.
            x0, x1, h = a
            back = ((x0, h, 2, 14),) if kind == "chair" else ()
            for x, y, w, hgt in ((x0, h, x1 - x0, 2), (x0, 0, 2, h), (x1 - 2, 0, 2, h), *back):
                px, py = to((x, y + hgt))
                cr.rectangle(px, py, w, hgt)
        elif kind == "table":  # a top from x0 to x1 at height h, the edge at x1 and a leg at the far end
            x0, x1, h = a
            for x, y, w, hgt in ((x0, h, x1 - x0, 2), (x0, 0, 2, h)):
                px, py = to((x, y + hgt))
                cr.rectangle(px, py, w, hgt)
        elif kind == "box":  # a step from x0 to x1, h high
            x0, x1, h = a
            px, py = to((x0, h))
            cr.rectangle(px, py, x1 - x0, h)
        elif kind == "wall":  # a wall whose face is at x, holds as (x, y) bumps on it
            x, holds = a[0], (a[1] if len(a) > 1 else ())
            px, py = to((x - 3, H))
            cr.rectangle(px, py, 3, H - GROUND)
            for hx, hy in holds:
                px, py = to((x - 1, hy + 1))
                cr.rectangle(px, py, 3, 2)
        elif kind == "overhang":  # a boulder wall leaning out over you: its face from (x0, 0) to (x1, H)
            x0, x1, holds = a
            cr.set_line_width(3)
            cr.move_to(*to((x0 + 1.5, 0)))
            cr.line_to(*to((x1 + 1.5, H)))
            cr.stroke()
            for hx, hy in holds:
                px, py = to((hx - 1, hy + 1))
                cr.rectangle(px, py, 3, 2)
        elif kind == "pad":  # a crash pad from x0 to x1, h thick
            x0, x1, h = a
            px, py = to((x0, h))
            cr.rectangle(px, py, x1 - x0, h)
        elif kind == "bike":  # wheels at x0 and x1, radius r
            x0, x1, r = a
            cr.set_line_width(1)
            for x in (x0, x1):
                px, py = to((x, r))
                cr.arc(px, py, r, 0, 2 * math.pi)
                cr.stroke()
            pts = [(x0, r), (x0 + 13, r), (x0 + 8, 19), (x0, r)]
            cr.move_to(*to(pts[0]))
            for q in pts[1:]:
                cr.line_to(*to(q))
            cr.move_to(*to((x0 + 13, r)))
            cr.line_to(*to((x1 - 4, 20)))
            cr.line_to(*to((x1, r)))
            cr.move_to(*to((x0 + 8, 19)))
            cr.line_to(*to((x1 - 4, 20)))
            cr.stroke()
            for x, y, w in ((x0 + 5, 21, 6), (x1 - 6, 24, 4)):  # saddle, bars
                px, py = to((x, y))
                cr.rectangle(px, py, w, 2)
        elif kind == "water":  # a wavy surface at height h
            h = a[0]
            for x in range(2, W - 2, 6):
                px, py = to((x, h))
                cr.rectangle(px, py, 3, 1)
                cr.rectangle(px + 3, py + 1, 3, 1)
        elif kind == "ball":
            px, py = to(a[0])
            fill_disc(cr, px, py, 5, 2)
        cr.fill()


def draw(cr, move, t, accent, scale=3, floor=True):
    """Draw one frame. cr is a surface W*scale by H*scale."""
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    c = cairo.Context(small)
    c.set_antialias(cairo.ANTIALIAS_NONE)
    c.set_line_cap(cairo.LINE_CAP_ROUND)
    c.set_line_join(cairo.LINE_JOIN_ROUND)

    def to(p):
        return (round(p[0]), round(H - GROUND - p[1]))

    p = move.at(t)
    front = move.view == "front"
    sk = skeleton(p, front)
    hot = rgb(accent)
    hot_far = mix(hot, (0.08, 0.09, 0.1), 0.45)

    if floor:
        c.set_source_rgb(*FLOOR)
        for x in range(4, W - 4, 4):
            c.rectangle(x, H - GROUND, 2, 1)
        c.fill()
    draw_props(c, move.props, to)
    if move.plate:
        # A barbell seen end on: a plate centred between the hands, behind the near arm.
        hands = [sk["limbs"][s]["arm"][2] for s in (0, 1)]
        cx, cy = to(lerp(hands[0], hands[1], 0.5))
        c.set_source_rgb(*PROP)
        fill_disc(c, cx, cy, 27, 5)

    def stroke(points, color, width):
        c.set_source_rgb(*color)
        c.set_line_width(width)
        c.move_to(*to(points[0]))
        for q in points[1:]:
            c.line_to(*to(q))
        c.stroke()

    fx, fy = (0, 0) if front else p["far"]
    near, far = sk["limbs"][0], sk["limbs"][1]

    def lit(part, back=False):
        if part in move.work:
            return hot_far if back else hot
        return FAR if back else FG

    # Far limbs first, so the near ones cross over them.
    if front:
        stroke(far["leg"], lit("legs"), 2)
        stroke(far["arm"], lit("arms"), 2)
    else:
        stroke([(x + fx, y + fy) for x, y in far["leg"]], lit("legs", True), 2)
        stroke([(x + fx, y + fy) for x, y in far["arm"]], lit("arms", True), 2)
    core = lit("core")
    stroke([sk["hip"], sk["shoulder"], sk["neck"]], core, 3)
    if front:
        stroke([near["arm"][0], far["arm"][0]], core, 2)
        stroke([near["leg"][0], far["leg"][0]], core, 2)
    stroke(near["leg"], lit("legs"), 2)
    stroke(near["arm"], lit("arms"), 2)
    hx, hy = to(sk["head"])
    c.set_source_rgb(*FG)
    fill_disc(c, hx, hy, 10, 3)

    cr.save()
    cr.scale(scale, scale)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_NEAREST)
    cr.paint()
    cr.restore()


# Shared poses. Push-up geometry: the body is a straight line from the ankles (y 3, on the toes)
# to the shoulders, 37 units long, with the hands planted under the shoulders.

def plank_line(shoulder_y, ankle=(22, 3)):
    dy = shoulder_y - ankle[1]
    dx = math.sqrt((LEG + TORSO) ** 2 - dy * dy)
    k = LEG / (LEG + TORSO)
    hip = (ankle[0] + k * dx, ankle[1] + k * dy)
    return hip, math.degrees(math.atan2(dy, dx)), ankle[0] + dx


_hip, _tor, _sx = plank_line(ARM)
PUSH_UP_TOP = pose(hip=_hip, torso=_tor, head=_tor + 8, hands=((_sx, 0), (_sx, 0)), feet=((22, 3), (22, 3)),
                   toes=(-55, -55))
_hip, _tor, _ = plank_line(8)
PUSH_UP_LOW = pose(hip=_hip, torso=_tor, head=_tor + 8, hands=((_sx, 0), (_sx, 0)), feet=((22, 3), (22, 3)),
                   toes=(-55, -55))
# Decline: the toes on a box 19 high, so the line from the ankles runs down to the shoulders.
_DECLINE_FEET = (27, 22)
_hip, _tor, _dsx = plank_line(ARM, _DECLINE_FEET)
DECLINE_TOP = pose(hip=_hip, torso=_tor, head=_tor + 8, hands=((_dsx, 0), (_dsx, 0)),
                   feet=(_DECLINE_FEET, _DECLINE_FEET), toes=(-55, -55))
_hip, _tor, _ = plank_line(8, _DECLINE_FEET)
DECLINE_LOW = pose(hip=_hip, torso=_tor, head=_tor + 8, hands=((_dsx, 0), (_dsx, 0)),
                   feet=(_DECLINE_FEET, _DECLINE_FEET), toes=(-55, -55))
_fhip, _ftor, _fsx = plank_line(9)
FOREARM_PLANK = pose(hip=_fhip, torso=_ftor, head=_ftor + 6, hands=((_fsx + 9, 0), (_fsx + 9, 0)),
                     feet=((22, 3), (22, 3)), toes=(-55, -55))

HANG = ("arm", -95, ARM)  # arms hanging at the sides
HIPS = ("torso", 0.12, 2)  # hands on hips


def stand(x=48, hands=(HANG, HANG), **kw):
    return pose(**{**dict(hip=(x, 23), torso=90, hands=hands, feet=((x, 1), (x, 1))), **kw})


def squat_low(x=46, hands=(("arm", 0, ARM), ("arm", 0, ARM))):
    return pose(hip=(x - 9, 12), torso=62, hands=hands, feet=((x, 1), (x, 1)))


def jack(t):
    """Jumping jacks, t from 0 (feet together, arms down) to 1 (feet wide, arms overhead)."""
    return pose(hip=(48, 23 + 2 * t), torso=90, hands=(("arm", -100 - 135 * t, ARM), ("arm", -80 + 135 * t, ARM)),
                feet=((46 - 9 * t, 1 + 2 * t), (50 + 9 * t, 1 + 2 * t)), toes=(180, 0), knees=(-1, 1))


def held(p, **wobble):
    """A hold: the pose, and the same pose eased a little deeper, so it breathes."""
    deeper = dict(p)
    for k, v in wobble.items():
        deeper[k] = v
    return [(p, 1.2, 1.6), (pose(**{k: v for k, v in deeper.items() if k not in ("elbows", "knees")},
                                 elbows=p["elbows"], knees=p["knees"]), 1.4, 1.6)]


def lunge(side):
    """One forward lunge: the front foot steps out and plants, the body sinks between the feet and
    rises, and the foot steps back. The back foot never moves; its heel lifts off the floor."""
    back = tip(48, -50)
    at = lambda front, rear, toes: pose(**{**front, "feet": rear[::1 - 2 * side], "toes": toes[::1 - 2 * side]})
    up = stand(44, hands=(HIPS, HIPS))
    planted = ((66, 1), back)
    toes = (0, -50)
    return [
        (up, 0.25, 0.55),
        (at(pose(hip=(54, 19), torso=90, hands=(HIPS, HIPS)), planted, toes), 0.05, 0.5),
        (at(pose(hip=(56, 12), torso=90, hands=(HIPS, HIPS)), planted, toes), 0.2, 0.5),
        (at(pose(hip=(54, 19), torso=90, hands=(HIPS, HIPS)), planted, toes), 0.05, 0.55),
    ]


class Swing(Move):
    """A move that goes back and forth along one path: at_u(u) is the pose from u=0 to u=1, held
    at each end. Each frame comes straight off the path, so a body rocking on its heels stays
    straight all the way, where a blend between two keyframes would cut the arc and fold the knees.
    It has no keyframes to borrow."""

    def __init__(self, at_u, low, up, top, down, **kw):
        super().__init__([], **kw)
        self.at_u, self.phases = at_u, (low, up, top, down)
        self.period = low + up + top + down

    def at(self, t):
        low, up, top, down = self.phases
        t %= self.period
        if t < low:
            return self.at_u(0)
        t -= low
        if t < up:
            return self.at_u(ease(t / up))
        t -= up
        if t < top:
            return self.at_u(1)
        return self.at_u(1 - ease((t - top) / down))


# A body row: lying on the back under a table, knees bent and feet flat on the floor, the torso
# under the top and the head out past the edge the hands hold. The body stays a straight line from
# knees to shoulders and swings about the knees, from arms long up to the chest at the edge.
# Lying face up, the elbows drop toward the floor and the hips: supine.
_ROW_ANKLE = (30, 2)
_ROW_KNEE = polar(_ROW_ANKLE, SHIN, 65)
_ROW_HAND = (60.5, 25)
_ROW_LOW, _ROW_TOP = -10.2, 13.3  # the body's angle off level, arms long and chest up


def row(u):
    angle = lerp(_ROW_LOW, _ROW_TOP, u)
    hip = polar(_ROW_KNEE, THIGH, angle)
    return pose(supine=True, hip=hip, torso=angle, head=angle - 8 * u, hands=(_ROW_HAND, _ROW_HAND),
                feet=(_ROW_ANKLE, _ROW_ANKLE), toes=(180, 180))


_bulg_chair = ("chair", 16, 30, 14)
_pike_hip = (41, 20)

MOVES = {
    "push-ups": Move([(PUSH_UP_TOP, 0.35, 0.8), (PUSH_UP_LOW, 0.15, 0.6)], work=("arms",)),
    "decline push-ups": Move([(DECLINE_TOP, 0.35, 0.8), (DECLINE_LOW, 0.15, 0.6)], work=("arms",),
                             props=(("box", 16, 31, 19),)),
    "squats": Move([(stand(46), 0.3, 0.9), (squat_low(46), 0.2, 0.8)], work=("legs",)),
    "lunges": Move(lunge(0) + lunge(1), work=("legs",)),
    "bulgarian split squats": Move([
        (pose(hip=(46, 20), torso=86, hands=(HANG, HANG), feet=((57, 1), (28, 15.5)), toes=(0, 185)), 0.3, 0.9),
        (pose(hip=(43, 11), torso=78, hands=(("arm", -100, ARM), ("arm", -100, ARM)), feet=((57, 1), (28, 15.5)),
              toes=(0, 185)), 0.2, 0.8),
    ], work=("legs",), props=(_bulg_chair,)),
    "burpees": Move([
        (stand(46), 0.2, 0.45),
        (pose(hip=(43, 10), torso=38, head=45, hands=((_sx, 0), (_sx, 0)), feet=((46, 1), (46, 1))), 0.05, 0.35),
        (PUSH_UP_TOP, 0.1, 0.35),
        (pose(hip=(43, 10), torso=38, head=45, hands=((_sx, 0), (_sx, 0)), feet=((46, 1), (46, 1))), 0.05, 0.4),
        (pose(hip=(48, 28), torso=90, hands=(("arm", 95, ARM), ("arm", 95, ARM)), feet=((48, 6), (48, 6)),
              toes=(-60, -60)), 0.05, 0.25),
    ], work=("arms", "legs", "core")),
    "squat jumps": Move([
        (stand(46, hands=(("arm", -110, ARM), ("arm", -110, ARM))), 0.15, 0.5),
        (squat_low(46, hands=(("arm", -150, ARM), ("arm", -150, ARM))), 0.1, 0.25),
        (pose(hip=(46, 29), torso=90, hands=(("arm", 80, ARM), ("arm", 80, ARM)), feet=((46, 7), (46, 7)),
              toes=(-60, -60)), 0.08, 0.3),
    ], work=("legs",)),
    "step-ups": Move([
        (stand(42), 0.2, 0.5),
        (pose(hip=(44, 23), torso=88, hands=(HANG, HANG), feet=((56, 11), (42, 1))), 0.1, 0.6),
        (pose(hip=(56, 33), torso=92, hands=(HANG, HANG), feet=((56, 11), (57, 11))), 0.25, 0.5),
        (pose(hip=(44, 23), torso=88, hands=(HANG, HANG), feet=((56, 11), (42, 1))), 0.1, 0.5),
    ], work=("legs",), props=(("box", 50, 68, 10),)),
    "calf raises": Move([
        (stand(48), 0.3, 0.6),
        (stand(48, hip=(49.3, 25.9), feet=(tip(52, -48), tip(52, -48)), toes=(-48, -48)), 0.4, 0.6),
    ], work=("legs",)),
    "sit-ups": Move([
        (pose(supine=True, hip=(48, 3), torso=2, head=4, hands=(("torso", 0.75, -3), ("torso", 0.75, -3)),
              feet=((32, 1), (32, 1))), 0.25, 0.8),
        (pose(supine=True, hip=(48, 3), torso=112, head=100, hands=(("torso", 0.75, -3), ("torso", 0.75, -3)),
              feet=((32, 1), (32, 1))), 0.2, 0.8),
    ], work=("core",)),
    "plank": Move(held(FOREARM_PLANK, hip=(FOREARM_PLANK["hip"][0], FOREARM_PLANK["hip"][1] + 0.6)), work=("core",)),
    "side plank": Move(held(
        pose(hip=(41.4, 13.5), torso=28, head=32, hands=(("arm", 95, ARM), ("arm", -90, ARM)),
             feet=(("leg", 208, LEG), ("leg", 208, LEG)), toes=(118, 118), knees=(-1, 1), elbows=(-1, 1)),
        hip=(41.4, 14.3)), work=("core",), view="front"),
    "hollow hold": Move(held(
        pose(supine=True, hip=(46, 2), torso=12, head=16, hands=(("arm", 10, ARM), ("arm", 10, ARM)),
             feet=(("leg", 170, LEG), ("leg", 170, LEG)), toes=(160, 160)),
        torso=16, feet=(("leg", 165, LEG), ("leg", 165, LEG))), work=("core",)),
    "glute bridges": Move([
        (pose(supine=True, hip=(46, 3), torso=0, head=0, hands=(("arm", 181, ARM), ("arm", 181, ARM)),
              feet=((32, 1), (32, 1))), 0.25, 0.8),
        (pose(supine=True, hip=(45, 13), torso=-40, head=0, hands=(("arm", 178, ARM), ("arm", 178, ARM)),
              feet=((32, 1), (32, 1))), 0.4, 0.8),
    ], work=("legs",)),
    "chair dips": Move([
        (pose(hip=(41, 18), torso=90, hands=((37, 15), (37, 15)), feet=((55, 1), (55, 1))), 0.25, 0.8),
        (pose(hip=(42, 9), torso=88, hands=((37, 15), (37, 15)), feet=((55, 1), (55, 1))), 0.15, 0.8),
    ], work=("arms",), props=(("chair", 22, 38, 14),)),
    "pike push-ups": Move([
        (pose(hip=_pike_hip, torso=-40, head=-50, hands=((60, 0), (60, 0)), feet=((30, 1), (30, 1)), toes=(-30, -30)),
         0.3, 0.8),
        (pose(hip=_pike_hip, torso=-58, head=-75, hands=((60, 0), (60, 0)), feet=((30, 1), (30, 1)), toes=(-30, -30)),
         0.15, 0.7),
    ], work=("arms",)),
    "jumping jacks": Move([(jack(0), 0.05, 0.3), (jack(1), 0.05, 0.3)], work=("arms", "legs"), view="front"),
    "mountain climbers": Move([
        (pose(**{**PUSH_UP_TOP, "feet": ((40, 6), (22, 3)), "toes": (-30, -55)}), 0.02, 0.28),
        (pose(**{**PUSH_UP_TOP, "feet": ((22, 3), (40, 6)), "toes": (-55, -30)}), 0.02, 0.28),
    ], work=("core",), lift=0.22),
    "cossack squats": Move([
        (pose(hip=(48, 18), torso=90, hands=(("torso", 0.7, 0), ("torso", 0.7, 0)), feet=((34, 1), (62, 1)),
              toes=(180, 0), knees=(-1, 1), elbows=(-1, 1)), 0.15, 0.7),
        (pose(hip=(39, 8), torso=80, hands=(("torso", 0.7, 0), ("torso", 0.7, 0)), feet=((34, 1), (62, 2)),
              toes=(180, 80), knees=(-1, 1), elbows=(-1, 1)), 0.2, 0.7),
        (pose(hip=(48, 18), torso=90, hands=(("torso", 0.7, 0), ("torso", 0.7, 0)), feet=((34, 1), (62, 1)),
              toes=(180, 0), knees=(-1, 1), elbows=(-1, 1)), 0.15, 0.7),
        (pose(hip=(57, 8), torso=100, hands=(("torso", 0.7, 0), ("torso", 0.7, 0)), feet=((34, 2), (62, 1)),
              toes=(100, 0), knees=(-1, 1), elbows=(-1, 1)), 0.2, 0.7),
    ], work=("legs",), view="front"),
    "wall sit": Move(held(pose(hip=(29, 12), torso=90, hands=(("arm", -5, ARM), ("arm", -5, ARM)), feet=((41, 1), (41, 1))),
                          hip=(29, 11.4)), work=("legs",), props=(("wall", 26),)),
    "supermen": Move([
        (pose(hip=(44, 3), torso=0, head=4, hands=(("arm", 0, ARM), ("arm", 0, ARM)), feet=(("leg", 180, LEG),) * 2,
              toes=(180, 180)), 0.3, 0.8),
        (pose(hip=(44, 3), torso=12, head=22, hands=(("arm", 22, ARM), ("arm", 22, ARM)), feet=(("leg", 165, LEG),) * 2,
              toes=(170, 170)), 0.6, 0.8),
    ], work=("core",)),
    # Feet on a chair behind take some of the weight; the chin comes over the bar.
    "assisted pull-ups": Move([
        (pose(hip=(47.5, 20.5), torso=92, head=95, hands=((48, 53), (48, 53)), feet=((32, 15.5), (32, 15.5)),
              toes=(185, 185)), 0.3, 0.9),
        (pose(hip=(47.5, 31), torso=92, head=85, hands=((48, 53), (48, 53)), feet=((32, 15.5), (32, 15.5)),
              toes=(185, 185)), 0.25, 0.8),
    ], work=("arms",), props=(("bar", 30, 66, 52), ("chair", 22, 36, 14))),
    # Between two bars, the toes resting on a box in front for help.
    "assisted dips": Move([
        (pose(hip=(47.5, 32), torso=92, hands=((48, 29), (48, 29)), feet=((62, 15), (62, 15)), toes=(-20, -20)),
         0.3, 0.8),
        (pose(hip=(48.5, 24), torso=80, head=82, hands=((48, 29), (48, 29)), feet=((62, 15), (62, 15)),
              toes=(-20, -20)), 0.15, 0.8),
    ], work=("arms",), props=(("bar", 36, 60, 27), ("box", 56, 72, 14))),
    # Under a table, feet flat and knees bent, the body straight from knees to shoulders and the head
    # out past the edge; the chest comes up to it.
    "body rows": Swing(row, 0.3, 0.8, 0.25, 0.8, work=("arms",), props=(("table", 22, 61, 25),)),
    # Sitting with straight legs, hands planted beside the knees: lift the legs off the floor.
    "seated pike ups": Move([
        (pose(hip=(36, 3), torso=84, head=80, hands=((44, 1), (44, 1)), feet=(("leg", -4, LEG), ("leg", -4, LEG)),
              toes=(-8, -8)), 0.3, 0.6),
        (pose(hip=(36, 4), torso=76, head=70, hands=((44, 1), (44, 1)), feet=(("leg", 14, LEG), ("leg", 14, LEG)),
              toes=(8, 8)), 0.35, 0.6),
    ], work=("core", "legs")),
    # Seat down, hands on the floor out by the knees, the straight legs lifted off the floor and held.
    "compression hold": Move(held(
        pose(hip=(36, 3.5), torso=72, head=68, hands=((45.5, 1), (45.5, 1)), feet=(("leg", 8, LEG), ("leg", 8, LEG)),
             toes=(8, 8)),
        torso=70, feet=(("leg", 12, LEG), ("leg", 12, LEG)), toes=(12, 12)), work=("core", "legs")),
    # Up on straight arms between two parallettes, legs held straight out level with the hips.
    "L-sit": Move(held(
        pose(hip=(36, 12.6), torso=80, head=86, hands=((35, 10), (35, 10)), feet=(("leg", 0, LEG), ("leg", 0, LEG)),
             toes=(0, 0)),
        hip=(36, 13.6), torso=81), work=("core", "arms"), props=(("bar", 29, 41, 8),)),
}

# Stretches: holds that ease a little deeper as you breathe out. Side ones show the right side.

_kneel_hip = (46, 12)

STRETCHES = {
    "hip flexor lunge": Move(held(
        pose(hip=_kneel_hip, torso=94, hands=(HIPS, HIPS), feet=((59, 1), (30, 1)), toes=(0, 180)),
        hip=(49, 11), torso=98), work=("legs",)),
    "half split": Move(held(
        pose(hip=(40, 12), torso=45, head=40, hands=((55, 0), (55, 0)), feet=((61, 3), (28, 1)), toes=(80, 180)),
        torso=28, head=20), work=("legs",)),
    "couch stretch": Move(held(
        pose(hip=(36, 13), torso=88, hands=(("arm", -40, ARM), ("arm", -40, ARM)), feet=((51, 1), (24, 12)),
             toes=(0, 95)),
        torso=98, hip=(37, 12)), work=("legs",), props=(("wall", 23),)),
    # David's hard hip flexor lunge: hips pushed down, a hand reaching back to pull the back foot up.
    "lunge with foot grab": Move(held(
        pose(hip=(46, 11), torso=96, hands=((34, 15), HIPS), feet=((60, 1), (33, 15)), toes=(0, 100)),
        hip=(47, 10), torso=100, hands=((34, 16), HIPS)), work=("legs",)),
    "front split": Move(held(
        pose(hip=(46, 9), torso=90, hands=(("arm", -80, 15), ("arm", -80, 15)), feet=(("leg", -18, LEG), ("leg", 198, LEG)),
             toes=(80, 180)),
        hip=(46, 3.5), feet=(("leg", -4, LEG), ("leg", 184, LEG)), hands=(("arm", -70, 9), ("arm", -70, 9))),
        work=("legs",)),
    "butterfly": Move(held(
        pose(hip=(48, 4), torso=90, hands=((47, 3), (49, 3)), feet=((46, 2), (50, 2)), toes=(200, -20),
             knees=(-1, 1), elbows=(-1, 1)),
        fold=40), work=("legs",), view="front"),
    "side lunge": Move([
        (pose(hip=(40, 9), torso=82, hands=(("torso", 0.7, 0), ("torso", 0.7, 0)), feet=((33, 1), (63, 2)),
              toes=(180, 80), knees=(-1, 1), elbows=(-1, 1)), 2.0, 1.4),
        (pose(hip=(56, 9), torso=98, hands=(("torso", 0.7, 0), ("torso", 0.7, 0)), feet=((33, 2), (63, 1)),
              toes=(100, 0), knees=(-1, 1), elbows=(-1, 1)), 2.0, 1.4),
    ], work=("legs",), view="front"),
    "pancake": Move(held(
        pose(hip=(48, 3), torso=90, hands=((42, 3), (54, 3)), feet=(("leg", 184, LEG), ("leg", -4, LEG)),
             toes=(90, 90), knees=(-1, 1), elbows=(-1, 1), fold=25),
        fold=72, hands=((36, 1), (60, 1))), work=("legs", "core"), view="front"),
    "middle split": Move(held(
        pose(hip=(48, 8), torso=90, hands=((44, 0), (52, 0)), feet=(("leg", 198, LEG), ("leg", -18, LEG)),
             toes=(90, 90), knees=(-1, 1), elbows=(-1, 1)),
        hip=(48, 3.5), feet=(("leg", 186, LEG), ("leg", -6, LEG))), work=("legs",), view="front", glide=True),
    "seated pike": Move(held(
        pose(hip=(36, 3), torso=60, head=55, hands=((56, 4), (56, 4)), feet=((58, 2), (58, 2)), toes=(85, 85)),
        torso=22, head=10), work=("legs",)),
    "standing fold": Move([
        (stand(46, feet=((46, 1), (46, 1))), 0.4, 0.9),
        (stand(44, torso=10, head=-10, hands=(("arm", -80, ARM), ("arm", -80, ARM)), feet=((46, 1), (46, 1))), 0, 0.9),
        (stand(40, hip=(40, 22.5), torso=-56, head=-72, hands=((54, 0), (54, 0)), feet=((46, 1), (46, 1))), 2.8, 0.9),
        (stand(44, torso=10, head=-10, hands=(("arm", -80, ARM), ("arm", -80, ARM)), feet=((46, 1), (46, 1))), 0, 0.9),
    ], work=("legs",)),
    "lying hamstring": Move(held(
        pose(supine=True, hip=(40, 2), torso=0, head=0, hands=((43, 19), (43, 19)),
             feet=(("leg", 82, LEG), ("leg", 180, LEG)), toes=(10, 90)),
        feet=(("leg", 100, LEG), ("leg", 180, LEG)), hands=((38, 19), (38, 19))), work=("legs",)),
    "pigeon": Move(held(
        pose(hip=(44, 6), torso=80, head=85, hands=((52, 0), (52, 0)), feet=((46, 1), (22, 2)), toes=(180, 180)),
        torso=30, head=20, hands=((62, 0), (62, 0))), work=("legs",)),
    "deep squat": Move(held(
        pose(hip=(41, 6), torso=72, hands=((52, 17), (52, 17)), feet=((48, 1), (48, 1))),
        hip=(41, 5), torso=76), work=("legs",)),
}


def cycle(steps, period, **base):
    """Keyframes for something that goes round: steps(i) gives a pose's overrides at i/8 of a turn."""
    return [(pose(**{**base, **steps(i / 8)}), 0, period / 8) for i in range(8)]


def pedal(t):
    a = 2 * math.pi * t
    crank = (47, 7)
    return {"feet": ((crank[0] + 5 * math.cos(a), crank[1] + 5 * math.sin(a)),
                     (crank[0] - 5 * math.cos(a), crank[1] - 5 * math.sin(a))),
            "toes": (-10, -10)}


def stroke_arms(t):
    """Front crawl: each arm goes round, straight, half a turn apart."""
    a = 360 * t
    return {"hands": (("arm", -a, ARM), ("arm", 180 - a, ARM)),
            "feet": (("leg", 180 + 6 * math.sin(math.radians(2 * a)), LEG),
                     ("leg", 180 - 6 * math.sin(math.radians(2 * a)), LEG))}


def stride(x, lift, reach, swing, lean, heel=4):
    """One side of a gait: near leg forward, far leg back with the heel up, arms swinging opposite."""
    return pose(hip=(x, 22 + lift), torso=90 - lean, feet=((x + reach, 1 + lift), (x - reach + 2, 1 + heel + 2 * lift)),
                toes=(0, -40), hands=(("arm", -90 - swing, 15), ("arm", -90 + swing, 15)))


def stride_b(*a, **kw):
    p = stride(*a, **kw)
    return pose(**{**p, "feet": p["feet"][::-1], "toes": p["toes"][::-1], "hands": p["hands"][::-1]})


HOLDS = ((66, 47), (66, 37), (66, 27), (66, 13), (66, 5))

def face(y):
    """Where the boulder's overhanging face is at height y."""
    return 76 - 0.25 * y


PAD = 2  # a crash pad's thickness: feet on it stand this much higher
BOULDER = tuple((round(face(y)) - 1, y) for y in (8, 16, 26, 36, 46, 54))

TRAINING = {
    "climbing": Move([
        (pose(hip=(56, 18), torso=97, head=75, hands=((65, 47), (65, 37)), feet=((64, 13), (64, 5)), toes=(20, 20)),
         0.5, 0.9),
        (pose(hip=(57, 21), torso=94, head=70, hands=((65, 37), (65, 47)), feet=((64, 5), (64, 13)), toes=(20, 20)),
         0.5, 0.9),
    ], work=("arms", "core"), props=(("wall", 67, HOLDS),)),
    # Sit start on the pad, pull on and climb the overhang, reach for the top hold, drop off onto
    # the pad and walk back to sit down again.
    "bouldering": Move([
        (pose(hip=(55, 7), torso=70, head=60, hands=(BOULDER[2], BOULDER[1]), feet=((72, 8), (63, PAD + 1)),
              toes=(20, 0)), 0.6, 0.8),
        (pose(hip=(56, 18), torso=85, head=70, hands=(BOULDER[3], BOULDER[2]), feet=((71, 16), (72, 8)),
              toes=(30, 30)), 0.3, 0.8),
        (pose(hip=(56, 26), torso=88, head=75, hands=(BOULDER[4], BOULDER[3]), feet=((68, 26), (71, 16)),
              toes=(30, 30)), 0.2, 0.5),
        (pose(hip=(55, 28), torso=92, head=80, hands=(BOULDER[5], BOULDER[4]), feet=((68, 26), (71, 16)),
              toes=(30, 30)), 0.5, 0.6),
        (pose(hip=(50, 14), torso=80, head=70, hands=(("arm", -60, ARM), ("arm", -50, ARM)),
              feet=((55, PAD + 1), (49, PAD + 1)), toes=(0, 0)), 0.5, 0.8),
    ], work=("arms", "core"), props=(("pad", 30, 82, PAD), ("overhang", round(face(0)), round(face(H)), BOULDER))),
    "weights": Move([
        (pose(hip=(46, 23), torso=90, hands=((47, 12), (47, 12)), feet=((46, 1), (46, 1))), 0.3, 0.9),
        (pose(hip=(36, 15), torso=38, head=30, hands=((49, 5), (49, 5)), feet=((46, 1), (46, 1))), 0.2, 0.9),
    ], work=("legs", "core"), plate=True),
    "running": Move([
        (stride(48, 2, 9, 45, 12, 9), 0, 0.2), (stride(48, 0, 2, 5, 12, 12), 0, 0.12),
        (stride_b(48, 2, 9, 45, 12, 9), 0, 0.2), (stride_b(48, 0, 2, 5, 12, 12), 0, 0.12),
    ], work=("legs",), glide=True),
    "cycling": Move(cycle(pedal, 1.0, hip=(41, 24), torso=52, head=30, hands=((60, 25), (60, 25))),
                    work=("legs",), props=(("bike", 31, 64, 8),), glide=True),
    "swimming": Move(cycle(stroke_arms, 1.6, hip=(42, 11), torso=0, head=-10, toes=(180, 180)),
                     work=("arms",), props=(("water", 15),)),
    "yoga": Move([
        # Down dog to cobra: hands and toes stay where they are, the hips travel between them.
        (pose(hip=(48, 19.5), torso=-42, head=-55, hands=((70, 0), (70, 0)), feet=(tip(40, -10),) * 2,
              toes=(-10, -10)), 1.0, 1.2),
        (pose(hip=(57, 4), torso=55, head=70, hands=((70, 0), (70, 0)), feet=(tip(40, -50),) * 2,
              toes=(-50, -50)), 1.0, 1.2),
    ], work=("core",)),
    "walking": Move([
        (stride(48, 1, 6, 25, 4), 0, 0.4), (stride(48, 0, 1, 3, 4), 0, 0.25),
        (stride_b(48, 1, 6, 25, 4), 0, 0.4), (stride_b(48, 0, 1, 3, 4), 0, 0.25),
    ], work=("legs",), glide=True),
    "football": Move([
        (pose(hip=(44, 23), torso=95, hands=(("arm", -130, ARM), ("arm", -40, ARM)), feet=((46, 1), (32, 12)),
              toes=(0, -60)), 0.2, 0.25),
        (pose(hip=(46, 23), torso=80, hands=(("arm", -120, ARM), ("arm", -50, ARM)), feet=((46, 1), (64, 10)),
              toes=(0, 10)), 0.25, 0.6),
    ], work=("legs",), props=(("ball", (62, 3)),)),
    "other": Move([(jack(0), 0.6, 0.9), (pose(**{**jack(0), "hands": (("arm", -235, ARM), ("arm", 55, ARM))}), 0.8, 0.9)],
                  work=(), view="front"),
}
TRAINING["hiit"] = Move(MOVES["burpees"].frames, work=("arms", "legs", "core"))

# The challenge's stretches are David's versions of these positions.
for _alias, _base in (("front split practice", "front split"), ("middle split practice", "middle split"),
                      ("PNF pancake", "pancake"), ("deep pigeon", "pigeon")):
    STRETCHES[_alias] = STRETCHES[_base]

SUPINE_MOVES = {"body rows", "sit-ups", "hollow hold", "glute bridges", "lying hamstring"}


def every():
    return {**MOVES, **STRETCHES, **TRAINING}


def joint_turns(name, move, samples=24):
    """Every elbow and knee turn across a loop, in degrees, signed as flexion: positive is the way
    a real joint bends, negative is backwards. Side view only; a front view has no such sign."""
    flip = -1 if name in SUPINE_MOVES else 1
    out = []
    for i in range(samples):
        sk = skeleton(move.at(move.period * i / samples))
        for side in (0, 1):
            for limb, sign in (("arm", 1), ("leg", -1)):
                a, b, c = sk["limbs"][side][limb][:3]
                u = math.atan2(b[1] - a[1], b[0] - a[0])
                v = math.atan2(c[1] - b[1], c[0] - b[0])
                turn = math.degrees((v - u + math.pi) % (2 * math.pi) - math.pi)
                out.append((limb, side, i, turn * sign * flip))
    return out


def slides(move, samples=240):
    """How far each foot slides along the floor across a loop: whichever of ankle and toe tip
    bears on the floor, summed over the stretches where it stays there. A heel rising off a
    planted toe swings the ankle, which is not a slide."""
    out, prev = {}, None
    for i in range(samples + 1):
        sk = skeleton(move.at(move.period * i / samples), move.view == "front")
        cur = [(sk["limbs"][s]["leg"][2], sk["limbs"][s]["leg"][3]) for s in (0, 1)]
        for side, points in enumerate(zip(prev or cur, cur)):
            low = [min(a[1], b[1]) for a, b in points]
            for part, (a, b) in zip(("ankle", "toe"), zip(*points)):
                if a[1] < 2 and b[1] < 2 and a[1] <= low[0] + 0.5 and b[1] <= low[1] + 0.5:
                    out[side, part] = out.get((side, part), 0) + abs(b[0] - a[0])
        prev = cur
    return out


def check():
    """Joints that bend backwards anywhere in a loop, by more than a straight limb's wobble, and
    feet that slide along the floor instead of staying planted or stepping."""
    bad = []
    for name, move in every().items():
        if not move.glide:
            for (side, part), far in slides(move).items():
                if far > 1.5:
                    bad.append(f"{name}: {'near' if side == 0 else 'far'} {part} slides {far:.1f} along the floor")
        if move.view == "front":
            continue
        for limb, side, i, turn in joint_turns(name, move):
            if turn < -4:
                bad.append(f"{name}: {'near' if side == 0 else 'far'} {'elbow' if limb == 'arm' else 'knee'} "
                           f"bends backwards {-turn:.0f} deg at frame {i}")
    return bad


def sheet(path, names=None, frames=8, scale=3, accent="#7fb3d4"):
    """Every move as a row of frames across one loop, for checking poses by eye."""
    moves = every()
    names = names or list(moves)
    cw, ch = W * scale, H * scale
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, cw * frames, ch * len(names))
    cr = cairo.Context(surf)
    cr.set_source_rgb(0.094, 0.106, 0.122)
    cr.paint()
    for row, name in enumerate(names):
        move = moves[name]
        for i in range(frames):
            cr.save()
            cr.translate(i * cw, row * ch)
            draw(cr, move, move.period * i / frames, accent, scale)
            cr.set_source_rgb(0.3, 0.3, 0.3)
            cr.rectangle(0, 0, cw, ch)
            cr.set_line_width(1)
            cr.stroke()
            if i == 0:
                cr.set_source_rgb(0.8, 0.8, 0.8)
                cr.move_to(4, 14)
                cr.set_font_size(12)
                cr.show_text(name)
            cr.restore()
    surf.write_to_png(path)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["check"]:
        problems = check()
        print("\n".join(problems) or "every elbow and knee bends the right way, and every foot stays planted")
        sys.exit(1 if problems else 0)
    sheet(sys.argv[1] if len(sys.argv) > 1 else "captures/figures.png", sys.argv[2:] or None)
