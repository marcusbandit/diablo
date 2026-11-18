.pragma library

const KINDS = ["ring", "underline", "arrow", "label", "spotlight", "highlight", "num", "check", "cross", "node", "edge", "title"];
const POINTS = ["fromPoint", "start", "end", "via"];

function finite(v) {
    return typeof v === "number" && isFinite(v);
}

function boxOf(mark) {
    const b = mark && mark.box;
    if (b && finite(b.x) && finite(b.y) && finite(b.w) && finite(b.h))
        return { x: b.x, y: b.y, w: Math.max(0, b.w), h: Math.max(0, b.h) };
    const p = mark && mark.point;
    if (p && finite(p.x) && finite(p.y))
        return { x: p.x - 12, y: p.y - 12, w: 24, h: 24 };
    return null;
}

function cleanMarks(marks) {
    const out = [];
    for (const m of marks || []) {
        if (!m || KINDS.indexOf(m.kind) < 0)
            continue;
        const box = boxOf(m);
        if (!box)
            continue;
        out.push(Object.assign({}, m, { box: box }));
    }
    return out;
}

function withCaption(beat) {
    const marks = cleanMarks(beat && beat.marks);
    const say = String((beat && beat.say) || "").trim();
    if (beat && beat.caption && say && marks.length > 0)
        marks.push({ kind: "label", caption: true, text: say, box: marks[0].box });
    return marks;
}

function intersects(a, b) {
    return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

function centre(b) {
    return { x: b.x + b.w / 2, y: b.y + b.h / 2 };
}

function inside(p, b, pad) {
    pad = pad || 0;
    return !!p && p.x >= b.x - pad && p.x <= b.x + b.w + pad && p.y >= b.y - pad && p.y <= b.y + b.h + pad;
}

function toLocal(marks, screen) {
    const out = [];
    for (const m of marks || []) {
        const box = { x: m.box.x - screen.x, y: m.box.y - screen.y, w: m.box.w, h: m.box.h };
        const local = { x: 0, y: 0, w: screen.w, h: screen.h };
        const moved = { box: box };
        for (const key of POINTS)
            moved[key] = m[key] ? { x: m[key].x - screen.x, y: m[key].y - screen.y } : null;
        const reach = m.kind === "arrow" && moved.fromPoint ? spanOf(box, moved.fromPoint) : grow(box, 160);
        if (!intersects(reach, local))
            continue;
        out.push(Object.assign({}, m, moved));
    }
    return out;
}

function grow(b, by) {
    return { x: b.x - by, y: b.y - by, w: b.w + 2 * by, h: b.h + 2 * by };
}

function spanOf(b, p) {
    const x = Math.min(b.x, p.x), y = Math.min(b.y, p.y);
    return { x: x, y: y, w: Math.max(b.x + b.w, p.x) - x, h: Math.max(b.y + b.h, p.y) - y };
}

function ringFor(box, pad) {
    pad = pad === undefined ? 8 : pad;
    const hw = Math.max(box.w / 2, 6), hh = Math.max(box.h / 2, 6);
    let rx = Math.min(hw * Math.SQRT2, hw + 40);
    const left = 1 - (hw * hw) / (rx * rx);
    let ry = left > 0.05 ? Math.max(hh * Math.SQRT2, hh / Math.sqrt(left)) : hh * Math.SQRT2;
    ry = Math.min(ry, hh + 60);
    const c = centre(box);
    return { cx: c.x, cy: c.y, rx: rx + pad, ry: ry + pad * 0.8 };
}

function underlineFor(box) {
    const y = box.y + box.h + 4;
    return { x1: box.x - 2, y1: y + 1, cx: box.x + box.w / 2, cy: y + 4, x2: box.x + box.w + 2, y2: y - 1 };
}

function arrowFrom(box, screen, orb, cursor, given) {
    const clear = grow(box, 40);
    const ok = p => !!p && finite(p.x) && finite(p.y) && inside(p, screen, 0) && !inside(p, clear, 0);
    if (given && typeof given === "object" && ok(given))
        return { x: given.x, y: given.y, auto: false };
    if (ok(orb))
        return { x: orb.x, y: orb.y, auto: false, orb: true };
    if (ok(cursor))
        return { x: cursor.x, y: cursor.y, auto: false };
    const c = centre(box);
    const mid = centre(screen);
    const sx = mid.x >= c.x ? 1 : -1, sy = mid.y >= c.y ? 1 : -1;
    const reach = 150;
    return {
        x: clampTo(c.x + sx * (box.w / 2 + reach * 0.8), screen.x + 12, screen.x + screen.w - 12),
        y: clampTo(c.y + sy * (box.h / 2 + reach * 0.6), screen.y + 12, screen.y + screen.h - 12),
        auto: true
    };
}

function edgePoint(from, box, gap) {
    const r = ringFor(box);
    const dx = from.x - r.cx, dy = from.y - r.cy;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const ux = dx / len, uy = dy / len;
    const rx = r.rx + gap, ry = r.ry + gap;
    const t = 1 / Math.sqrt((ux / rx) * (ux / rx) + (uy / ry) * (uy / ry));
    return { x: r.cx + ux * t, y: r.cy + uy * t };
}

function arrowPath(from, box, gap, bend) {
    gap = gap === undefined ? 6 : gap;
    bend = bend === undefined ? 0.18 : bend;
    const end = edgePoint(from, box, gap);
    const dx = end.x - from.x, dy = end.y - from.y;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const nx = -dy / len, ny = dx / len;
    const side = ny <= 0 ? 1 : -1;
    const cx = (from.x + end.x) / 2 + nx * len * bend * side;
    const cy = (from.y + end.y) / 2 + ny * len * bend * side;
    const angle = Math.atan2(end.y - cy, end.x - cx);
    const head = Math.min(16, Math.max(9, len * 0.12));
    const wing = 0.5;
    return {
        x1: from.x, y1: from.y, cx: cx, cy: cy, x2: end.x, y2: end.y, angle: angle, length: len,
        hx1: end.x - head * Math.cos(angle - wing), hy1: end.y - head * Math.sin(angle - wing),
        hx2: end.x - head * Math.cos(angle + wing), hy2: end.y - head * Math.sin(angle + wing)
    };
}

function clearOf(box) {
    const r = ringFor(box);
    return { x: r.cx - r.rx, y: r.cy - r.ry, w: 2 * r.rx, h: 2 * r.ry };
}

const SIDES = { right: ["right", "left", "below", "above"], left: ["left", "right", "below", "above"],
    below: ["below", "above", "right", "left"], above: ["above", "below", "right", "left"] };

function labelSpot(box, size, screen, prefer, gap) {
    gap = gap === undefined ? 8 : gap;
    const order = Array.isArray(prefer) ? prefer : SIDES[prefer] || SIDES.right;
    const c = clearOf(box);
    const spots = {
        right: { x: c.x + c.w + gap, y: box.y + box.h / 2 - size.h / 2 },
        left: { x: c.x - gap - size.w, y: box.y + box.h / 2 - size.h / 2 },
        below: { x: Math.max(c.x, box.x + box.w / 2 - size.w / 2), y: c.y + c.h + gap },
        above: { x: Math.max(c.x, box.x + box.w / 2 - size.w / 2), y: c.y - gap - size.h }
    };
    const fits = s => s.x >= 8 && s.y >= 8 && s.x + size.w <= screen.w - 8 && s.y + size.h <= screen.h - 8;
    for (const side of order) {
        if (fits(spots[side]))
            return { x: spots[side].x, y: spots[side].y, side: side };
    }
    const s = spots[order[0]];
    return { x: clampTo(s.x, 8, screen.w - size.w - 8), y: clampTo(s.y, 8, screen.h - size.h - 8), side: order[0] };
}

function awayFrom(box, p) {
    if (!p)
        return null;
    const c = centre(box);
    const dx = p.x - c.x, dy = p.y - c.y;
    if (Math.abs(dy) >= Math.abs(dx))
        return dy > 0 ? ["above", "right", "left", "below"] : ["below", "right", "left", "above"];
    return dx > 0 ? ["left", "above", "below", "right"] : ["right", "above", "below", "left"];
}

function badgeSpot(box, size, screen) {
    return {
        x: clampTo(box.x - size * 0.55, 4, screen.w - size - 4),
        y: clampTo(box.y - size * 0.55, 4, screen.h - size - 4)
    };
}

function sayMs(say, cps) {
    cps = finite(cps) && cps > 0 ? cps : 15;
    const n = String(say || "").trim().length;
    if (n === 0)
        return 2500;
    return Math.round(Math.min(20000, Math.max(1800, n / cps * 1000 + 500)));
}

function beatAt(show, n) {
    const beats = (show && show.beats) || [];
    for (const b of beats) {
        if (b && b.n === n)
            return b;
    }
    return null;
}

function waitBox(marks) {
    for (const m of marks || []) {
        if (m && m.box && m.kind !== "spotlight")
            return m.box;
    }
    return marks && marks.length ? marks[0].box : null;
}

const IDLE = { show: "", index: -1, since: 0, entered: false, doneAt: 0, over: true, need: -1 };

function advance(show, play, now, pointer, resolved, opts) {
    opts = opts || {};
    const linger = opts.lingerMs === undefined ? 12000 : opts.lingerMs;
    const waitMs = opts.waitMs === undefined ? 60000 : opts.waitMs;
    const stall = opts.stallMs === undefined ? 20000 : opts.stallMs;
    resolved = resolved || {};
    if (!show || !show.show || show.cleared)
        return Object.assign({}, IDLE, { show: show && show.show ? show.show : "" });
    let p = play && play.show === show.show ? Object.assign({}, play) : { show: show.show, index: -1, since: now, entered: false, doneAt: now, over: false, need: -1 };
    if (p.over)
        return p;
    const count = Math.max(1, show.count || 1);
    const ready = n => {
        const b = beatAt(show, n);
        if (!b)
            return null;
        if (!b.lazy)
            return b;
        return resolved[n] || null;
    };
    p.need = -1;
    if (p.index < 0) {
        const first = ready(0);
        if (first)
            return Object.assign(p, { index: 0, since: now, entered: false, doneAt: 0 });
        if (beatAt(show, 0) && beatAt(show, 0).lazy)
            p.need = 0;
        if (now - p.since > stall)
            p.over = true;
        return p;
    }
    const beat = ready(p.index);
    const elapsed = now - p.since;
    let done;
    if (beat && beat.wait === "click") {
        const box = waitBox(cleanMarks(beat.marks));
        const on = !!box && inside(pointer, box, 6);
        const passed = p.entered && !on;
        p.entered = p.entered || on;
        done = (passed && elapsed > 600) || elapsed >= waitMs || !box;
    } else {
        done = elapsed >= sayMs(beat ? beat.say : "", opts.cps);
    }
    if (!done)
        return p;
    if (!p.doneAt)
        p.doneAt = now;
    if (p.index >= count - 1) {
        if (now - p.doneAt >= linger)
            p.over = true;
        return p;
    }
    const next = ready(p.index + 1);
    if (next)
        return Object.assign(p, { index: p.index + 1, since: now, entered: false, doneAt: 0 });
    if (beatAt(show, p.index + 1) && beatAt(show, p.index + 1).lazy)
        p.need = p.index + 1;
    if (now - p.doneAt >= stall)
        p.over = true;
    return p;
}

function screenOf(p, screens) {
    let best = null, bestD = Infinity;
    for (const s of screens || []) {
        const dx = Math.max(s.x - p.x, 0, p.x - (s.x + s.w));
        const dy = Math.max(s.y - p.y, 0, p.y - (s.y + s.h));
        const d = dx * dx + dy * dy;
        if (d < bestD) {
            bestD = d;
            best = s;
        }
    }
    return best;
}

function stage(marks, screens, orb, cursor, orbRadius) {
    orbRadius = orbRadius === undefined ? 56 : orbRadius;
    const out = [];
    for (const m of marks || []) {
        if (m.kind !== "arrow") {
            out.push(m);
            continue;
        }
        const screen = screenOf(centre(m.box), screens) || { x: m.box.x - 2000, y: m.box.y - 2000, w: 4000, h: 4000 };
        const from = arrowFrom(m.box, screen, orb, cursor, m.from);
        let start = { x: from.x, y: from.y };
        if (from.orb) {
            const c = centre(m.box);
            const len = Math.hypot(c.x - from.x, c.y - from.y) || 1;
            start = { x: from.x + (c.x - from.x) / len * orbRadius, y: from.y + (c.y - from.y) / len * orbRadius };
        }
        out.push(Object.assign({}, m, { fromPoint: start }));
    }
    for (let i = 0; i < out.length; i++) {
        const m = out[i];
        if (m.kind !== "label" || m.side)
            continue;
        const arrow = out.find(a => a.kind === "arrow" && a.fromPoint && Math.abs(a.box.x - m.box.x) < 1 && Math.abs(a.box.y - m.box.y) < 1);
        const sides = arrow ? awayFrom(m.box, arrow.fromPoint) : null;
        if (sides)
            out[i] = Object.assign({}, m, { side: sides });
    }
    return out;
}

function nodePath(box, shape) {
    const x = box.x, y = box.y, w = box.w, h = box.h, cx = x + w / 2, cy = y + h / 2;
    if (shape === "diamond")
        return `M ${cx} ${y} L ${x + w} ${cy} L ${cx} ${y + h} L ${x} ${cy} Z`;
    const r = shape === "round" ? h / 2 : Math.min(10, h / 2, w / 2);
    return `M ${cx} ${y} L ${x + w - r} ${y} A ${r} ${r} 0 0 1 ${x + w} ${y + r} L ${x + w} ${y + h - r} `
        + `A ${r} ${r} 0 0 1 ${x + w - r} ${y + h} L ${x + r} ${y + h} A ${r} ${r} 0 0 1 ${x} ${y + h - r} `
        + `L ${x} ${y + r} A ${r} ${r} 0 0 1 ${x + r} ${y} Z`;
}

function edgePath(start, end, via, head) {
    via = via || { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
    const len = Math.hypot(end.x - start.x, end.y - start.y);
    head = head === undefined ? Math.min(17, Math.max(10, len * 0.22)) : head;
    let angle = Math.atan2(end.y - via.y, end.x - via.x);
    if (Math.hypot(end.x - via.x, end.y - via.y) < 1e-6)
        angle = Math.atan2(end.y - start.y, end.x - start.x);
    const wing = 0.46;
    const mid = { x: 0.25 * start.x + 0.5 * via.x + 0.25 * end.x, y: 0.25 * start.y + 0.5 * via.y + 0.25 * end.y };
    return {
        x1: start.x, y1: start.y, cx: via.x, cy: via.y, x2: end.x, y2: end.y, angle: angle, length: len, mx: mid.x, my: mid.y,
        hx1: end.x - head * Math.cos(angle - wing), hy1: end.y - head * Math.sin(angle - wing),
        hx2: end.x - head * Math.cos(angle + wing), hy2: end.y - head * Math.sin(angle + wing)
    };
}

function keepsEarlier(beat) {
    return !!(beat && beat.keep);
}

function clampTo(v, lo, hi) {
    return hi < lo ? lo : Math.max(lo, Math.min(hi, v));
}
