.pragma library
// Where Diablo's cards go, worked out from the orb, never from the cursor. Pure geometry, so
// tests/presence/place.test.mjs runs the same code in node (it drops the pragma line above).
// Every rect is {x, y, w, h} in the monitor's own coordinates.

// The card stack beside the orb: below it when there is room, above it when there is not,
// lined up with the orb's left edge, or its right edge when that side runs out of screen.
// originX/originY is the point of the stack nearest the orb's middle, so a card scaled up
// from there grows out of him.
function cardRect(orb, card, screen, edge, gap) {
    edge = edge === undefined ? 12 : edge;
    gap = gap === undefined ? 10 : gap;
    const below = orb.y + orb.h + gap + card.h <= screen.h - edge || orb.y - gap - card.h < edge;
    let y = below ? orb.y + orb.h + gap : orb.y - gap - card.h;
    y = clamp(y, edge, screen.h - card.h - edge);
    const leftAligned = orb.x + card.w <= screen.w - edge;
    let x = leftAligned ? orb.x : orb.x + orb.w - card.w;
    x = clamp(x, edge, screen.w - card.w - edge);
    const cx = orb.x + orb.w / 2;
    return {
        x: x,
        y: y,
        below: below,
        originX: clamp(cx - x, 0, card.w),
        originY: below ? 0 : card.h,
    };
}

// The central card: the middle of the monitor, a share of it, never past a sensible size.
function panelRect(screen, edge) {
    edge = edge === undefined ? 12 : edge;
    const w = Math.min(980, Math.max(320, screen.w * 0.62), screen.w - 2 * edge);
    const h = Math.min(720, Math.max(240, screen.h * 0.72), screen.h - 2 * edge);
    return { x: Math.round((screen.w - w) / 2), y: Math.round((screen.h - h) / 2), w: Math.round(w), h: Math.round(h) };
}

// Whether the pointer is reaching for a card: on it (or within `margin` of it), or travelling
// towards it from close by, its last stretch of travel (from `from` to `cursor`) pointing into
// it give or take 12 degrees. true holds the card and orb still so the card can be read and
// clicked; false lets them follow; null, for a pointer at rest, keeps whatever they were doing.
function reaching(cursor, from, rect, margin, range) {
    margin = margin === undefined ? 16 : margin;
    range = range === undefined ? 360 : range;
    if (!rect || rect.w <= 0 || rect.h <= 0)
        return false;
    const r = { x: rect.x - margin, y: rect.y - margin, w: rect.w + 2 * margin, h: rect.h + 2 * margin };
    if (cursor.x >= r.x && cursor.x <= r.x + r.w && cursor.y >= r.y && cursor.y <= r.y + r.h)
        return true;
    const dx = cursor.x - from.x, dy = cursor.y - from.y;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len < 4)
        return null;
    const spread = 12 * Math.PI / 180;
    const angles = [0, -spread, spread];
    for (let i = 0; i < angles.length; i++) {
        const c = Math.cos(angles[i]), s = Math.sin(angles[i]);
        const t = rayHit(cursor, { x: (dx * c - dy * s) / len, y: (dx * s + dy * c) / len }, r);
        if (t !== null && t <= range)
            return true;
    }
    return false;
}

// How far along a ray from p in direction d it meets the rect; null when it misses.
function rayHit(p, d, r) {
    let near = 0, far = Infinity;
    const axes = [[p.x, d.x, r.x, r.x + r.w], [p.y, d.y, r.y, r.y + r.h]];
    for (let i = 0; i < axes.length; i++) {
        const [o, v, lo, hi] = axes[i];
        if (Math.abs(v) < 1e-9) {
            if (o < lo || o > hi)
                return null;
            continue;
        }
        let a = (lo - o) / v, b = (hi - o) / v;
        if (a > b)
            [a, b] = [b, a];
        near = Math.max(near, a);
        far = Math.min(far, b);
        if (near > far)
            return null;
    }
    return near;
}

// The index of the rect holding the point, else the one nearest it; -1 for no rects.
function nearestRect(p, rects) {
    let best = -1, bestD = Infinity;
    for (let i = 0; i < rects.length; i++) {
        const r = rects[i];
        const dx = Math.max(r.x - p.x, 0, p.x - (r.x + r.w));
        const dy = Math.max(r.y - p.y, 0, p.y - (r.y + r.h));
        const d = dx * dx + dy * dy;
        if (d === 0)
            return i;
        if (d < bestD) {
            bestD = d;
            best = i;
        }
    }
    return best;
}

function clamp(v, lo, hi) {
    return hi < lo ? lo : Math.max(lo, Math.min(hi, v));
}
