import math

try:
    import cairo
except ImportError:
    cairo = None

W, H = 96, 64
GROUND = 6

THIGH, SHIN, FOOT = 11, 11, 4
STEP = 1.5
TORSO, NECK, HEAD = 15, 2, 4
UPPER, FORE = 9, 9
ARM, LEG = UPPER + FORE, THIGH + SHIN
SHOULDER_HALF = 4.2
HIP_HALF = 2.8

FG = (0.933, 0.949, 0.961)
FAR = (0.520, 0.565, 0.615)
PROP = (0.329, 0.365, 0.404)
PROP_TOP = (0.415, 0.455, 0.502)
FLOOR = (0.227, 0.255, 0.286)
SHADOW = (0.145, 0.165, 0.190)

ISO_YAW = math.radians(30)
ISO_PITCH = math.radians(22)
COS_Y, SIN_Y = math.cos(ISO_YAW), math.sin(ISO_YAW)
COS_P, SIN_P = math.cos(ISO_PITCH), math.sin(ISO_PITCH)


def rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def polar(p, length, deg):
    a = math.radians(deg)
    return (p[0] + length * math.cos(a), p[1] + length * math.sin(a))


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vlen(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def vnorm(a):
    n = vlen(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-6 else (0.0, 1.0, 0.0)


def project(pt, yaw=ISO_YAW, pitch=ISO_PITCH):
    x, y, z = pt[0] - 48.0, pt[1], pt[2]
    cy, sy = (COS_Y, SIN_Y) if yaw == ISO_YAW else (math.cos(yaw), math.sin(yaw))
    cp, sp = (COS_P, SIN_P) if pitch == ISO_PITCH else (math.cos(pitch), math.sin(pitch))
    u = x * cy - z * sy
    depth = x * sy + z * cy
    v = y * cp - depth * sp
    return (48.0 + u, H - GROUND - 6.0 - v), depth


def two_bone(root, target, l1, l2, bend):
    dx, dy = target[0] - root[0], target[1] - root[1]
    d = max(abs(l1 - l2) + 1e-3, min(l1 + l2 - 1e-3, math.hypot(dx, dy)))
    base = math.atan2(dy, dx)
    off = math.acos(max(-1.0, min(1.0, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))))
    mid = (root[0] + l1 * math.cos(base + bend * off), root[1] + l1 * math.sin(base + bend * off))
    end = polar(mid, l2, math.degrees(math.atan2(target[1] - mid[1], target[0] - mid[0])))
    return mid, end


def two_bone_3d(root, target, l1, l2, pole):
    diff = vsub(target, root)
    dist = vlen(diff)
    d = max(abs(l1 - l2) + 1e-3, min(l1 + l2 - 1e-3, dist))
    dir_v = vnorm(diff)
    proj = vdot(pole, dir_v)
    perp = vsub(pole, vscale(dir_v, proj))
    if vlen(perp) < 1e-4:
        perp = (0.0, 1.0, 0.0) if abs(dir_v[1]) < 0.9 else (1.0, 0.0, 0.0)
        perp = vsub(perp, vscale(dir_v, vdot(perp, dir_v)))
    perp = vnorm(perp)
    along = (l1 * l1 + d * d - l2 * l2) / (2.0 * d)
    h = math.sqrt(max(0.0, l1 * l1 - along * along))
    mid = vadd(root, vadd(vscale(dir_v, along), vscale(perp, h)))
    end_dir = vnorm(vsub(target, mid))
    end = vadd(mid, vscale(end_dir, l2))
    return mid, end


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def lerp(a, b, t):
    if isinstance(a, (tuple, list)):
        return tuple(lerp(x, y, t) for x, y in zip(a, b))
    return a + (b - a) * t


def angle_lerp(a, b, t):
    return a + ((b - a + 180) % 360 - 180) * t


UPRIGHT = {"elbows": (-1, -1), "knees": (1, 1)}
SUPINE = {"elbows": (1, 1), "knees": (-1, -1)}
BASE = {
    "head": None, "toes": (0, 0), "far": (1.5, 0), "fold": 0,
    "hand_z": (SHOULDER_HALF, -SHOULDER_HALF),
    "foot_z": (HIP_HALF, -HIP_HALF),
    "elbow_flare": 0.28,
    "knee_flare": 0.12,
    "yaw": None,
}


def pose(supine=False, **kw):
    p = dict(BASE)
    p.update(SUPINE if supine else UPRIGHT)
    p.update(kw)
    return p


def blend(a, b, t, lift=0.45):
    out = {}
    for k in a:
        if k in ("elbows", "knees", "far", "yaw"):
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
        return ("mix", a, b, t)
    return lerp(a, b, t)


def lifted(a, b, t, lift=0.45):
    if isinstance(a[0], str) or isinstance(b[0], str) or abs(b[0] - a[0]) < STEP:
        return blend_target(a, b, t)
    out = lerp(a, b, ease(min(1, max(0, (t - 0.2) / 0.6))))
    return (out[0], out[1] + min(6, lift * abs(b[0] - a[0])) * math.sin(math.pi * t))


def tip(x, angle, y=0):
    return polar((x, y), FOOT, angle + 180)


def resolve(target, root, hip, shoulder, torso):
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
    def __init__(self, frames, work=(), view="side", props=(), plate=False, glide=False, lift=0.45, yaw=None):
        self.frames, self.work, self.view, self.props, self.plate = frames, set(work), view, props, plate
        self.glide, self.lift, self.yaw = glide, lift, yaw
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
    hip, torso = p["hip"], p["torso"]
    squash = math.cos(math.radians(p["fold"])) if front else 1
    shoulder = polar(hip, TORSO * squash, torso)
    head_angle = p["head"] if p["head"] is not None else torso
    neck = polar(shoulder, NECK * squash, head_angle)
    head = polar(neck, HEAD * max(squash, 0.6), head_angle)
    limbs = {}
    for side in (0, 1):
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


def skeleton_3d(p, front=False):
    sk2 = skeleton(p, front)
    if front:
        hip3 = (sk2["hip"][0], sk2["hip"][1], 0.0)
        sh3 = (sk2["shoulder"][0], sk2["shoulder"][1], 0.0)
        neck3 = (sk2["neck"][0], sk2["neck"][1], 0.0)
        head3 = (sk2["head"][0], sk2["head"][1], 0.0)
        limbs3 = {}
        for side in (0, 1):
            arm2 = sk2["limbs"][side]["arm"]
            leg2 = sk2["limbs"][side]["leg"]
            limbs3[side] = {
                "arm": [(q[0], q[1], 0.0) for q in arm2],
                "leg": [(q[0], q[1], 0.0) for q in leg2],
            }
        return {"hip": hip3, "shoulder": sh3, "neck": neck3, "head": head3, "limbs": limbs3}

    hip3 = (sk2["hip"][0], sk2["hip"][1], 0.0)
    sh3 = (sk2["shoulder"][0], sk2["shoulder"][1], 0.0)
    neck3 = (sk2["neck"][0], sk2["neck"][1], 0.0)
    head3 = (sk2["head"][0], sk2["head"][1], 0.0)
    hand_z = p.get("hand_z", (SHOULDER_HALF, -SHOULDER_HALF))
    foot_z = p.get("foot_z", (HIP_HALF, -HIP_HALF))
    ef = float(p.get("elbow_flare", 0.28))
    kf = float(p.get("knee_flare", 0.12))
    limbs3 = {}
    for side in (0, 1):
        z_sign = 1.0 if side == 0 else -1.0
        sh_s = (sh3[0], sh3[1], SHOULDER_HALF * z_sign)
        hp_s = (hip3[0], hip3[1], HIP_HALF * z_sign)
        arm2 = sk2["limbs"][side]["arm"]
        leg2 = sk2["limbs"][side]["leg"]
        hand3 = (arm2[2][0], arm2[2][1], hand_z[side])
        foot3 = (leg2[2][0], leg2[2][1], foot_z[side])
        toe3 = (leg2[3][0], leg2[3][1], foot_z[side])

        e_mid2 = lerp(arm2[0], arm2[2], 0.5)
        e_vec2 = (arm2[1][0] - e_mid2[0], arm2[1][1] - e_mid2[1])
        if math.hypot(*e_vec2) < 1e-3:
            ang = math.atan2(arm2[2][1] - arm2[0][1], arm2[2][0] - arm2[0][0]) + p["elbows"][side] * math.pi / 2
            e_vec2 = (math.cos(ang), math.sin(ang))
        el_len = max(1.0, math.hypot(*e_vec2))
        e_pole = (e_vec2[0] / el_len, e_vec2[1] / el_len, z_sign * ef)
        elbow3, wrist3 = two_bone_3d(sh_s, hand3, UPPER, FORE, e_pole)

        k_mid2 = lerp(leg2[0], leg2[2], 0.5)
        k_vec2 = (leg2[1][0] - k_mid2[0], leg2[1][1] - k_mid2[1])
        if math.hypot(*k_vec2) < 1e-3:
            ang = math.atan2(leg2[2][1] - leg2[0][1], leg2[2][0] - leg2[0][0]) + p["knees"][side] * math.pi / 2
            k_vec2 = (math.cos(ang), math.sin(ang))
        kn_len = max(1.0, math.hypot(*k_vec2))
        k_pole = (k_vec2[0] / kn_len, k_vec2[1] / kn_len, z_sign * kf)
        knee3, ankle3 = two_bone_3d(hp_s, foot3, THIGH, SHIN, k_pole)

        limbs3[side] = {"arm": [sh_s, elbow3, wrist3], "leg": [hp_s, knee3, ankle3, toe3]}
    return {"hip": hip3, "shoulder": sh3, "neck": neck3, "head": head3, "limbs": limbs3}


def fill_disc(c, cx, cy, r2, reach):
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            if dx * dx + dy * dy <= r2:
                c.rectangle(cx + dx, cy + dy, 1, 1)
    c.fill()


def draw_box_3d(c, x0, x1, y0, y1, z0, z1, to3):
    def poly(pts, col):
        c.set_source_rgb(*col)
        p0 = to3(pts[0])
        c.move_to(*p0)
        for q in pts[1:]:
            c.line_to(*to3(q))
        c.close_path()
        c.fill()

    poly([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], PROP)
    poly([(x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)], mix(PROP, SHADOW, 0.25))
    poly([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], PROP_TOP)


def draw_floor_3d(c, to3, front=False):
    c.set_source_rgb(*FLOOR)
    if front:
        for x in range(6, W - 6, 4):
            px, py = to3((x, 0, 0))
            c.rectangle(px, py, 2, 1)
        c.fill()
        return
    c.set_line_width(1)
    for z in (-14, 0, 14):
        p0, p1 = to3((16, 0, z)), to3((80, 0, z))
        c.move_to(*p0)
        c.line_to(*p1)
    for x in (16, 48, 80):
        p0, p1 = to3((x, 0, -14)), to3((x, 0, 14))
        c.move_to(*p0)
        c.line_to(*p1)
    c.stroke()


def draw_props_3d(cr, props, to3, front=False):
    cr.set_source_rgb(*PROP)
    for kind, *a in props:
        if kind in ("chair", "bar"):
            x0, x1, h = a
            z0, z1 = (-9, 9) if not front else (-2, 2)
            draw_box_3d(cr, x0, x1, h - 2, h, z0, z1, to3)
            for lx in (x0, x1 - 2):
                for lz in (z0, z1 - 2):
                    draw_box_3d(cr, lx, lx + 2, 0, h - 2, lz, lz + 2, to3)
            if kind == "chair":
                draw_box_3d(cr, x0, x0 + 2, h, h + 13, z0, z1, to3)
        elif kind == "table":
            x0, x1, h = a
            z0, z1 = (-12, 12)
            draw_box_3d(cr, x0, x1, h - 2, h, z0, z1, to3)
            for lz in (z0, z1 - 2):
                draw_box_3d(cr, x0, x0 + 2, 0, h - 2, lz, lz + 2, to3)
        elif kind == "box":
            x0, x1, h = a
            draw_box_3d(cr, x0, x1, 0, h, -10, 10, to3)
        elif kind == "wall":
            x, holds = a[0], (a[1] if len(a) > 1 else ())
            draw_box_3d(cr, x - 3, x, 0, 50, -12, 12, to3)
            cr.set_source_rgb(*PROP_TOP)
            for hx, hy in holds:
                px, py = to3((x, hy, 4))
                cr.rectangle(px - 1, py - 1, 3, 2)
            cr.fill()
        elif kind == "overhang":
            x0, x1, holds = a
            cr.set_source_rgb(*PROP)
            cr.set_line_width(3)
            cr.move_to(*to3((x0 + 1.5, 0, -4)))
            cr.line_to(*to3((x1 + 1.5, H, -4)))
            cr.stroke()
            cr.set_source_rgb(*PROP_TOP)
            for hx, hy in holds:
                px, py = to3((hx, hy, 2))
                cr.rectangle(px - 1, py - 1, 3, 2)
            cr.fill()
        elif kind == "pad":
            x0, x1, h = a
            draw_box_3d(cr, x0, x1, 0, h, -10, 10, to3)
        elif kind == "bike":
            x0, x1, r = a
            cr.set_source_rgb(*PROP)
            cr.set_line_width(1)
            for x in (x0, x1):
                px, py = to3((x, r, 0))
                cr.arc(px, py, r, 0, 2 * math.pi)
                cr.stroke()
            pts = [(x0, r, 0), (x0 + 13, r, 0), (x0 + 8, 19, 0), (x0, r, 0)]
            cr.move_to(*to3(pts[0]))
            for q in pts[1:]:
                cr.line_to(*to3(q))
            cr.move_to(*to3((x0 + 13, r, 0)))
            cr.line_to(*to3((x1 - 4, 20, 0)))
            cr.line_to(*to3((x1, r, 0)))
            cr.move_to(*to3((x0 + 8, 19, 0)))
            cr.line_to(*to3((x1 - 4, 20, 0)))
            cr.stroke()
        elif kind == "water":
            h = a[0]
            cr.set_source_rgb(*PROP)
            for x in range(10, W - 10, 6):
                px, py = to3((x, h, 0))
                cr.rectangle(px, py, 3, 1)
                cr.rectangle(px + 3, py + 1, 3, 1)
            cr.fill()
        elif kind == "ball":
            px, py = to3((a[0][0], a[0][1], 0))
            cr.set_source_rgb(*PROP_TOP)
            fill_disc(cr, px, py, 5, 2)


def draw(cr, move, t, accent, scale=3, floor=True):
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    c = cairo.Context(small)
    c.set_antialias(cairo.ANTIALIAS_NONE)
    c.set_line_cap(cairo.LINE_CAP_ROUND)
    c.set_line_join(cairo.LINE_JOIN_ROUND)

    p = move.at(t)
    front = move.view == "front"
    yaw = move.yaw if move.yaw is not None else (0.0 if front else p.get("yaw") or ISO_YAW)
    pitch = 0.0 if front else ISO_PITCH

    def to3(pt):
        if front:
            return (round(pt[0]), round(H - GROUND - pt[1]))
        (px, py), _ = project(pt, yaw=yaw, pitch=pitch)
        return (round(px), round(py))

    def depth_of(pt):
        if front:
            return 0.0
        return project(pt, yaw=yaw, pitch=pitch)[1]

    sk = skeleton_3d(p, front)
    hot = rgb(accent)
    hot_far = mix(hot, (0.08, 0.09, 0.1), 0.42)

    if floor:
        draw_floor_3d(c, to3, front)
    draw_props_3d(c, move.props, to3, front)

    if not front:
        c.set_source_rgb(*SHADOW)
        for side in (0, 1):
            w_pt = sk["limbs"][side]["arm"][2]
            f_pt = sk["limbs"][side]["leg"][2]
            for pt in (w_pt, f_pt):
                sx, sy = to3((pt[0], 0.0, pt[2]))
                c.rectangle(sx - 1, sy, 3, 1)
        c.fill()

    if move.plate:
        hands = [sk["limbs"][s]["arm"][2] for s in (0, 1)]
        mid = lerp(hands[0], hands[1], 0.5)
        cx, cy = to3(mid)
        c.set_source_rgb(*PROP)
        fill_disc(c, cx, cy, 27, 5)

    def lit(part, back=False):
        if part in move.work:
            return hot_far if back else hot
        return FAR if back else FG

    segments = []

    def add_seg(points, color, width):
        d = sum(depth_of(q) for q in points) / len(points)
        segments.append((d, "line", points, color, width))

    near, far = sk["limbs"][0], sk["limbs"][1]
    core = lit("core")
    for side, limb, back in ((1, far, not front), (0, near, False)):
        add_seg([limb["leg"][0], limb["leg"][1]], lit("legs", back), 2)
        add_seg([limb["leg"][1], limb["leg"][2]], lit("legs", back), 2)
        add_seg([limb["leg"][2], limb["leg"][3]], lit("legs", back), 2)
        add_seg([limb["arm"][0], limb["arm"][1]], lit("arms", back), 2)
        add_seg([limb["arm"][1], limb["arm"][2]], lit("arms", back), 2)

    add_seg([sk["hip"], sk["shoulder"], sk["neck"]], core, 3)
    add_seg([near["arm"][0], far["arm"][0]], core, 2)
    add_seg([near["leg"][0], far["leg"][0]], core, 2)
    segments.append((depth_of(sk["head"]) + 0.5, "head", sk["head"], FG, 3))

    segments.sort(key=lambda item: item[0])
    for _, kind, payload, color, width in segments:
        if kind == "head":
            hx, hy = to3(payload)
            c.set_source_rgb(*color)
            fill_disc(c, hx, hy, 10, 3)
        else:
            c.set_source_rgb(*color)
            c.set_line_width(width)
            c.move_to(*to3(payload[0]))
            for q in payload[1:]:
                c.line_to(*to3(q))
            c.stroke()

    cr.save()
    cr.scale(scale, scale)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_NEAREST)
    cr.paint()
    cr.restore()


def plank_line(shoulder_y, ankle=(22, 3)):
    dy = shoulder_y - ankle[1]
    dx = math.sqrt((LEG + TORSO) ** 2 - dy * dy)
    k = LEG / (LEG + TORSO)
    hip = (ankle[0] + k * dx, ankle[1] + k * dy)
    return hip, math.degrees(math.atan2(dy, dx)), ankle[0] + dx


_hip, _tor, _sx = plank_line(ARM)
PUSH_UP_TOP = pose(hip=_hip, torso=_tor, head=_tor + 8, hands=((_sx, 0), (_sx, 0)), feet=((22, 3), (22, 3)),
                   toes=(-55, -55), hand_z=(5.5, -5.5), foot_z=(2.8, -2.8), elbow_flare=0.35)
_hip, _tor, _ = plank_line(8)
PUSH_UP_LOW = pose(hip=_hip, torso=_tor, head=_tor + 8, hands=((_sx, 0), (_sx, 0)), feet=((22, 3), (22, 3)),
                   toes=(-55, -55), hand_z=(5.5, -5.5), foot_z=(2.8, -2.8), elbow_flare=0.45)

WIDE_PUSH_UP_TOP = pose(**{**PUSH_UP_TOP, "hand_z": (12.5, -12.5), "elbow_flare": 0.95})
WIDE_PUSH_UP_LOW = pose(**{**PUSH_UP_LOW, "hand_z": (12.5, -12.5), "elbow_flare": 1.15})

DIAMOND_PUSH_UP_TOP = pose(**{**PUSH_UP_TOP, "hand_z": (1.3, -1.3), "elbow_flare": 0.12})
DIAMOND_PUSH_UP_LOW = pose(**{**PUSH_UP_LOW, "hand_z": (1.3, -1.3), "elbow_flare": 0.16})

_ih_top, _it_top, _isx = plank_line(30, ankle=(20, 3))
INCLINE_TOP = pose(hip=_ih_top, torso=_it_top, head=_it_top + 6, hands=((_isx + 5, 14), (_isx + 5, 14)),
                   feet=((20, 3), (20, 3)), toes=(-45, -45), hand_z=(5.5, -5.5), elbow_flare=0.35)
_ih_low, _it_low, _ = plank_line(21, ankle=(20, 3))
INCLINE_LOW = pose(hip=_ih_low, torso=_it_low, head=_it_low + 6, hands=((_isx + 5, 14), (_isx + 5, 14)),
                   feet=((20, 3), (20, 3)), toes=(-45, -45), hand_z=(5.5, -5.5), elbow_flare=0.45)

_DECLINE_FEET = (27, 20)
_hip, _tor, _dsx = plank_line(ARM, _DECLINE_FEET)
DECLINE_TOP = pose(hip=_hip, torso=_tor, head=_tor + 8, hands=((_dsx, 0), (_dsx, 0)),
                   feet=(_DECLINE_FEET, _DECLINE_FEET), toes=(-55, -55), hand_z=(5.5, -5.5), elbow_flare=0.38)
_hip, _tor, _ = plank_line(8, _DECLINE_FEET)
DECLINE_LOW = pose(hip=_hip, torso=_tor, head=_tor + 8, hands=((_dsx, 0), (_dsx, 0)),
                   feet=(_DECLINE_FEET, _DECLINE_FEET), toes=(-55, -55), hand_z=(5.5, -5.5), elbow_flare=0.48)

_pl_h, _pl_t, _pl_sx = plank_line(17, ankle=(26, 3))
PLANCHE_LEAN_A = pose(hip=_pl_h, torso=_pl_t, head=_pl_t + 6, hands=((_pl_sx - 5, 0), (_pl_sx - 5, 0)),
                      feet=((26, 3), (26, 3)), toes=(-55, -55), hand_z=(6.0, -6.0), elbow_flare=0.15)
_pl_h2, _pl_t2, _ = plank_line(14.5, ankle=(26, 3))
PLANCHE_LEAN_B = pose(hip=_pl_h2, torso=_pl_t2, head=_pl_t2 + 6, hands=((_pl_sx - 5, 0), (_pl_sx - 5, 0)),
                      feet=((26, 3), (26, 3)), toes=(-55, -55), hand_z=(6.0, -6.0), elbow_flare=0.15)

_pp_h1, _pp_t1, _pp_sx = plank_line(17, ankle=(28, 3))
PPPU_TOP = pose(hip=_pp_h1, torso=_pp_t1, head=_pp_t1 + 6, hands=((_pp_sx - 7, 0), (_pp_sx - 7, 0)),
                feet=((28, 3), (28, 3)), toes=(-55, -55), hand_z=(6.5, -6.5), elbow_flare=0.2)
_pp_h2, _pp_t2, _ = plank_line(7.5, ankle=(28, 3))
PPPU_LOW = pose(hip=_pp_h2, torso=_pp_t2, head=_pp_t2 + 6, hands=((_pp_sx - 7, 0), (_pp_sx - 7, 0)),
                feet=((28, 3), (28, 3)), toes=(-55, -55), hand_z=(6.5, -6.5), elbow_flare=0.25)

_fhip, _ftor, _fsx = plank_line(9)
FOREARM_PLANK = pose(hip=_fhip, torso=_ftor, head=_ftor + 6, hands=((_fsx + 9, 0), (_fsx + 9, 0)),
                     feet=((22, 3), (22, 3)), toes=(-55, -55), hand_z=(4.5, -4.5), elbow_flare=0.15)

HANG = ("arm", -95, ARM)
HIPS = ("torso", 0.12, 2)


def stand(x=48, hands=(HANG, HANG), **kw):
    return pose(**{**dict(hip=(x, 23), torso=90, hands=hands, feet=((x, 1), (x, 1))), **kw})


def squat_low(x=46, hands=(("arm", 0, ARM), ("arm", 0, ARM))):
    return pose(hip=(x - 9, 12), torso=62, hands=hands, feet=((x, 1), (x, 1)), foot_z=(4.5, -4.5), knee_flare=0.35)


def jack(t):
    return pose(hip=(48, 23 + 2 * t), torso=90, hands=(("arm", -100 - 135 * t, ARM), ("arm", -80 + 135 * t, ARM)),
                feet=((46 - 9 * t, 1 + 2 * t), (50 + 9 * t, 1 + 2 * t)), toes=(180, 0), knees=(-1, 1))


def held(p, **wobble):
    deeper = dict(p)
    for k, v in wobble.items():
        deeper[k] = v
    return [(p, 1.2, 1.6), (pose(**{k: v for k, v in deeper.items() if k not in ("elbows", "knees")},
                                 elbows=p["elbows"], knees=p["knees"]), 1.4, 1.6)]


def lunge(side):
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


_ROW_ANKLE = (30, 2)
_ROW_KNEE = polar(_ROW_ANKLE, SHIN, 65)
_ROW_HAND = (60.5, 25)
_ROW_LOW, _ROW_TOP = -10.2, 13.3


def row(u, hand_z=(5.5, -5.5), flare=0.35):
    angle = lerp(_ROW_LOW, _ROW_TOP, u)
    hip = polar(_ROW_KNEE, THIGH, angle)
    return pose(supine=True, hip=hip, torso=angle, head=angle - 8 * u, hands=(_ROW_HAND, _ROW_HAND),
                feet=(_ROW_ANKLE, _ROW_ANKLE), toes=(180, 180), hand_z=hand_z, elbow_flare=flare)


def wide_row(u):
    return row(u, hand_z=(11.5, -11.5), flare=0.95)


def scap_row(u):
    angle = lerp(_ROW_LOW, _ROW_LOW + 5.5, u)
    hip = polar(_ROW_KNEE, THIGH, angle)
    return pose(supine=True, hip=hip, torso=angle, head=angle, hands=(_ROW_HAND, _ROW_HAND),
                feet=(_ROW_ANKLE, _ROW_ANKLE), toes=(180, 180), hand_z=(6.5, -6.5), elbow_flare=0.25)


_bulg_chair = ("chair", 16, 30, 14)
_pike_hip = (41, 20)

MOVES = {
    "push-ups": Move([(PUSH_UP_TOP, 0.35, 0.8), (PUSH_UP_LOW, 0.15, 0.6)], work=("arms",), yaw=math.radians(36)),
    "wide push-ups": Move([(WIDE_PUSH_UP_TOP, 0.35, 0.8), (WIDE_PUSH_UP_LOW, 0.15, 0.6)], work=("arms", "core"), yaw=math.radians(46)),
    "diamond push-ups": Move([(DIAMOND_PUSH_UP_TOP, 0.35, 0.8), (DIAMOND_PUSH_UP_LOW, 0.15, 0.6)], work=("arms",), yaw=math.radians(44)),
    "incline push-ups": Move([(INCLINE_TOP, 0.35, 0.8), (INCLINE_LOW, 0.15, 0.6)], work=("arms",),
                             props=(("box", round(_isx + 1), round(_isx + 16), 14),), yaw=math.radians(34)),
    "decline push-ups": Move([(DECLINE_TOP, 0.35, 0.8), (DECLINE_LOW, 0.15, 0.6)], work=("arms",),
                             props=(("box", 16, 31, 18),), yaw=math.radians(34)),
    "planche lean": Move([(PLANCHE_LEAN_A, 0.8, 1.2), (PLANCHE_LEAN_B, 1.2, 1.2)], work=("arms", "core"), yaw=math.radians(34)),
    "pseudo planche push-ups": Move([(PPPU_TOP, 0.35, 0.85), (PPPU_LOW, 0.2, 0.65)], work=("arms", "core"), yaw=math.radians(38)),
    "frog stand": Move(held(
        pose(hip=(40, 13), torso=16, head=18, hands=((52, 0), (52, 0)),
             feet=((38, 10), (38, 10)), toes=(155, 155), hand_z=(6.5, -6.5), foot_z=(4.5, -4.5), elbow_flare=0.75, knee_flare=0.85),
        hip=(41, 14), torso=12), work=("arms", "core"), yaw=math.radians(38)),
    "tuck planche": Move(held(
        pose(hip=(42, 17), torso=8, head=12, hands=((52, 0), (52, 0)),
             feet=((44, 9), (44, 9)), toes=(145, 145), hand_z=(6.2, -6.2), foot_z=(3.2, -3.2), elbow_flare=0.15, knee_flare=0.25),
        hip=(43, 18.2), torso=5), work=("arms", "core"), yaw=math.radians(36)),
    "squats": Move([(stand(46, foot_z=(4.5, -4.5)), 0.3, 0.9), (squat_low(46), 0.2, 0.8)], work=("legs",)),
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
    "lying leg raises": Move([
        (pose(supine=True, hip=(44, 2), torso=2, head=4, hands=(("arm", 180, 16), ("arm", 180, 16)),
              feet=(("leg", 172, LEG), ("leg", 172, LEG)), toes=(155, 155), hand_z=(7.5, -7.5), foot_z=(2.2, -2.2)), 0.25, 0.85),
        (pose(supine=True, hip=(44, 2), torso=2, head=4, hands=(("arm", 180, 16), ("arm", 180, 16)),
              feet=(("leg", 92, LEG), ("leg", 92, LEG)), toes=(10, 10), hand_z=(7.5, -7.5), foot_z=(2.2, -2.2)), 0.25, 0.85),
    ], work=("core", "legs"), yaw=math.radians(32)),
    "hollow hold": Move(held(
        pose(supine=True, hip=(46, 2), torso=12, head=16, hands=(("arm", 10, ARM), ("arm", 10, ARM)),
             feet=(("leg", 170, LEG), ("leg", 170, LEG)), toes=(160, 160)),
        torso=16, feet=(("leg", 165, LEG), ("leg", 165, LEG))), work=("core",)),
    "hollow rocks": Move([
        (pose(supine=True, hip=(46, 2), torso=8, head=12, hands=(("arm", 6, ARM), ("arm", 6, ARM)),
              feet=(("leg", 162, LEG), ("leg", 162, LEG)), toes=(152, 152)), 0.1, 0.55),
        (pose(supine=True, hip=(46, 2), torso=24, head=28, hands=(("arm", 22, ARM), ("arm", 22, ARM)),
              feet=(("leg", 174, LEG), ("leg", 174, LEG)), toes=(166, 166)), 0.1, 0.55),
    ], work=("core",)),
    "dragon flag negatives": Move([
        (pose(supine=True, hip=(54, 16), torso=-52, head=-5, hands=((66, 3), (66, 3)),
              feet=(("leg", 118, LEG), ("leg", 118, LEG)), toes=(110, 110), hand_z=(5.5, -5.5)), 0.3, 1.4),
        (pose(supine=True, hip=(48, 4), torso=-6, head=0, hands=((66, 3), (66, 3)),
              feet=(("leg", 174, LEG), ("leg", 174, LEG)), toes=(165, 165), hand_z=(5.5, -5.5)), 0.35, 0.9),
    ], work=("core", "arms"), props=(("table", 64, 80, 22),)),
    "dead bugs": Move([
        (pose(supine=True, hip=(46, 2), torso=0, head=2, hands=(("arm", 90, ARM), ("arm", 8, ARM)),
              feet=(("leg", 172, LEG), ("leg", 95, 15)), toes=(160, 180)), 0.2, 0.65),
        (pose(supine=True, hip=(46, 2), torso=0, head=2, hands=(("arm", 8, ARM), ("arm", 90, ARM)),
              feet=(("leg", 95, 15), ("leg", 172, LEG)), toes=(180, 160)), 0.2, 0.65),
    ], work=("core",)),
    "flutter kicks": Move([
        (pose(supine=True, hip=(45, 2), torso=6, head=10, hands=((48, 1), (48, 1)),
              feet=(("leg", 156, LEG), ("leg", 175, LEG)), toes=(150, 170), hand_z=(7.0, -7.0)), 0.05, 0.28),
        (pose(supine=True, hip=(45, 2), torso=6, head=10, hands=((48, 1), (48, 1)),
              feet=(("leg", 175, LEG), ("leg", 156, LEG)), toes=(170, 150), hand_z=(7.0, -7.0)), 0.05, 0.28),
    ], work=("core", "legs")),
    "v-ups": Move([
        (pose(supine=True, hip=(46, 2), torso=6, head=8, hands=(("arm", 8, ARM), ("arm", 8, ARM)),
              feet=(("leg", 174, LEG), ("leg", 174, LEG)), toes=(165, 165)), 0.25, 0.7),
        (pose(supine=True, hip=(46, 3), torso=62, head=58, hands=(("arm", 125, ARM), ("arm", 125, ARM)),
              feet=(("leg", 125, LEG), ("leg", 125, LEG)), toes=(115, 115)), 0.2, 0.7),
    ], work=("core",)),
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
        (pose(hip=_pike_hip, torso=-40, head=-50, hands=((60, 0), (60, 0)), feet=((30, 1), (30, 1)), toes=(-30, -30),
              hand_z=(6.5, -6.5), elbow_flare=0.55), 0.3, 0.8),
        (pose(hip=_pike_hip, torso=-58, head=-75, hands=((60, 0), (60, 0)), feet=((30, 1), (30, 1)), toes=(-30, -30),
              hand_z=(6.5, -6.5), elbow_flare=0.65), 0.15, 0.7),
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
    "assisted pull-ups": Move([
        (pose(hip=(47.5, 20.5), torso=92, head=95, hands=((48, 53), (48, 53)), feet=((32, 15.5), (32, 15.5)),
              toes=(185, 185), hand_z=(8.0, -8.0)), 0.3, 0.9),
        (pose(hip=(47.5, 31), torso=92, head=85, hands=((48, 53), (48, 53)), feet=((32, 15.5), (32, 15.5)),
              toes=(185, 185), hand_z=(8.0, -8.0)), 0.25, 0.8),
    ], work=("arms",), props=(("bar", 30, 66, 52), ("chair", 22, 36, 14))),
    "assisted dips": Move([
        (pose(hip=(47.5, 32), torso=92, hands=((48, 29), (48, 29)), feet=((62, 15), (62, 15)), toes=(-20, -20),
              hand_z=(6.5, -6.5)), 0.3, 0.8),
        (pose(hip=(48.5, 24), torso=80, head=82, hands=((48, 29), (48, 29)), feet=((62, 15), (62, 15)),
              toes=(-20, -20), hand_z=(6.5, -6.5)), 0.15, 0.8),
    ], work=("arms",), props=(("bar", 36, 60, 27), ("box", 56, 72, 14))),
    "inverted rows": Swing(row, 0.3, 0.8, 0.25, 0.8, work=("arms",), props=(("table", 22, 61, 25),), yaw=math.radians(34)),
    "wide inverted rows": Swing(wide_row, 0.3, 0.8, 0.25, 0.8, work=("arms",), props=(("table", 22, 61, 25),), yaw=math.radians(44)),
    "scapular rows": Swing(scap_row, 0.35, 0.65, 0.35, 0.65, work=("arms", "core"), props=(("table", 22, 61, 25),), yaw=math.radians(36)),
    "tuck front lever": Move(held(
        pose(supine=True, hip=(42, 11), torso=2, head=4, hands=((57, 29), (57, 29)),
             feet=((44, 14), (44, 14)), toes=(160, 160), hand_z=(6.5, -6.5), foot_z=(3.0, -3.0)),
        hip=(42, 12), torso=4), work=("arms", "core"), props=(("table", 22, 61, 29),), yaw=math.radians(36)),
    "seated pike ups": Move([
        (pose(hip=(36, 3), torso=84, head=80, hands=((44, 1), (44, 1)), feet=(("leg", -4, LEG), ("leg", -4, LEG)),
              toes=(-8, -8)), 0.3, 0.6),
        (pose(hip=(36, 4), torso=76, head=70, hands=((44, 1), (44, 1)), feet=(("leg", 14, LEG), ("leg", 14, LEG)),
              toes=(8, 8)), 0.35, 0.6),
    ], work=("core", "legs")),
    "compression hold": Move(held(
        pose(hip=(36, 3.5), torso=72, head=68, hands=((45.5, 1), (45.5, 1)), feet=(("leg", 8, LEG), ("leg", 8, LEG)),
             toes=(8, 8)),
        torso=70, feet=(("leg", 12, LEG), ("leg", 12, LEG)), toes=(12, 12)), work=("core", "legs")),
    "L-sit": Move(held(
        pose(hip=(36, 12.6), torso=80, head=86, hands=((35, 10), (35, 10)), feet=(("leg", 0, LEG), ("leg", 0, LEG)),
             toes=(0, 0), hand_z=(6.5, -6.5)),
        hip=(36, 13.6), torso=81), work=("core", "arms"), props=(("bar", 29, 41, 8),)),
}

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
    return [(pose(**{**base, **steps(i / 8)}), 0, period / 8) for i in range(8)]


def pedal(t):
    a = 2 * math.pi * t
    crank = (47, 7)
    return {"feet": ((crank[0] + 5 * math.cos(a), crank[1] + 5 * math.sin(a)),
                     (crank[0] - 5 * math.cos(a), crank[1] - 5 * math.sin(a))),
            "toes": (-10, -10)}


def stroke_arms(t):
    a = 360 * t
    return {"hands": (("arm", -a, ARM), ("arm", 180 - a, ARM)),
            "feet": (("leg", 180 + 6 * math.sin(math.radians(2 * a)), LEG),
                     ("leg", 180 - 6 * math.sin(math.radians(2 * a)), LEG))}


def stride(x, lift, reach, swing, lean, heel=4):
    return pose(hip=(x, 22 + lift), torso=90 - lean, feet=((x + reach, 1 + lift), (x - reach + 2, 1 + heel + 2 * lift)),
                toes=(0, -40), hands=(("arm", -90 - swing, 15), ("arm", -90 + swing, 15)))


def stride_b(*a, **kw):
    p = stride(*a, **kw)
    return pose(**{**p, "feet": p["feet"][::-1], "toes": p["toes"][::-1], "hands": p["hands"][::-1]})


HOLDS = ((66, 47), (66, 37), (66, 27), (66, 13), (66, 5))


def face(y):
    return 76 - 0.25 * y


PAD = 2
BOULDER = tuple((round(face(y)) - 1, y) for y in (8, 16, 26, 36, 46, 54))

TRAINING = {
    "climbing": Move([
        (pose(hip=(56, 18), torso=97, head=75, hands=((65, 47), (65, 37)), feet=((64, 13), (64, 5)), toes=(20, 20)),
         0.5, 0.9),
        (pose(hip=(57, 21), torso=94, head=70, hands=((65, 37), (65, 47)), feet=((64, 5), (64, 13)), toes=(20, 20)),
         0.5, 0.9),
    ], work=("arms", "core"), props=(("wall", 67, HOLDS),)),
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
    "gym": Move([
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

for _alias, _base in (("front split practice", "front split"), ("middle split practice", "middle split"),
                      ("PNF pancake", "pancake"), ("deep pigeon", "pigeon")):
    STRETCHES[_alias] = STRETCHES[_base]

SUPINE_MOVES = {
    "inverted rows", "wide inverted rows", "scapular rows", "tuck front lever",
    "sit-ups", "hollow hold", "hollow rocks", "lying leg raises", "dragon flag negatives",
    "dead bugs", "flutter kicks", "v-ups", "glute bridges", "lying hamstring",
}


def every():
    return {**MOVES, **STRETCHES, **TRAINING}


def joint_turns(name, move, samples=24):
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
