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

// Where the orb goes once the pointer has rested a moment: "follow" stays beside it, "corner"
// glides to the bottom right corner, out of the way of what is on screen, and "away" fades him
// out over a fullscreen window, a video whose player has hidden the pointer. While Marcus needs
// him (talking to him, a card or the central card up, a passing moment) he stays beside it.
function restSpot(resting, needed, fullscreen) {
    if (!resting || needed)
        return "follow";
    return fullscreen ? "away" : "corner";
}

// The central card: the middle of the monitor, a share of it, never past a sensible size. `want`
// is the size its content asks for, a long list: it grows to that, up to panelRoom, and no
// smaller than its usual size.
function panelRect(screen, edge, want) {
    edge = edge === undefined ? 12 : edge;
    let w = Math.min(980, Math.max(320, screen.w * 0.62), screen.w - 2 * edge);
    let h = Math.min(720, Math.max(240, screen.h * 0.72), screen.h - 2 * edge);
    if (want) {
        const room = panelRoom(screen, edge);
        w = Math.max(w, Math.min(want.w || 0, room.w));
        h = Math.max(h, Math.min(want.h || 0, room.h));
    }
    return { x: Math.round((screen.w - w) / 2), y: Math.round((screen.h - h) / 2), w: Math.round(w), h: Math.round(h) };
}

// The most of the monitor the central card may take when its content asks for more.
function panelRoom(screen, edge) {
    edge = edge === undefined ? 12 : edge;
    return {
        w: Math.round(Math.min(1720, Math.max(320, screen.w * 0.86), screen.w - 2 * edge)),
        h: Math.round(Math.min(1240, Math.max(240, screen.h * 0.86), screen.h - 2 * edge)),
    };
}

// How many columns of `width` fit side by side in `room`, `gap` apart; at least one.
function columnsIn(room, width, gap) {
    return Math.max(1, Math.floor((room + gap) / (width + gap)));
}

// A list's sections in `count` columns, read down each column in order. Every section is a
// block: `pad` above and below, its heading heads[s] high, then its items rows[s]; blocks in a
// column sit `block` apart. Whole sections go into the columns when that keeps the tallest one
// within maxH; past that a section breaks between items and its heading starts the rest in the
// next column. `whole` true or false asks for one of the two only. Returns
// {columns: [[{section, from, to}]], h}, `to` exclusive, h the tallest column.
function listColumns(heads, rows, count, maxH, gaps, whole) {
    const g = Object.assign({ pad: 14, block: 14 }, gaps || {});
    count = Math.max(1, Math.floor(count));
    const sections = [];
    for (let s = 0; s < heads.length; s++) {
        const items = rows[s] || [];
        if (items.length)
            sections.push({ s: s, head: heads[s] || 0, items: items });
    }
    if (!sections.length)
        return { columns: [], h: 0 };
    if (whole === true)
        return wholeSections(sections, count, g);
    if (whole === false)
        return brokenSections(sections, count, g);
    const kept = wholeSections(sections, count, g);
    if (kept.h <= maxH)
        return kept;
    const broken = brokenSections(sections, count, g);
    return broken.h < kept.h ? broken : kept;
}

// listColumns with whole sections only: the split of the blocks into at most `count` runs whose
// tallest run is the shortest, tried against every run height a column could have.
function wholeSections(sections, count, g) {
    const heights = sections.map(x => 2 * g.pad + x.head + sum(x.items, 0, x.items.length));
    const span = (i, j) => sum(heights, i, j) + g.block * (j - i - 1);
    const pack = limit => {
        const columns = [[]];
        let h = 0;
        for (let i = 0; i < sections.length; i++) {
            if (columns[columns.length - 1].length && h + g.block + heights[i] > limit) {
                columns.push([]);
                h = 0;
            }
            const column = columns[columns.length - 1];
            h += (column.length ? g.block : 0) + heights[i];
            column.push({ section: sections[i].s, from: 0, to: sections[i].items.length });
        }
        return columns;
    };
    const limits = [];
    for (let i = 0; i < heights.length; i++)
        for (let j = i + 1; j <= heights.length; j++)
            limits.push(span(i, j));
    limits.sort((a, b) => a - b);
    for (let k = 0; k < limits.length; k++) {
        const columns = pack(limits[k]);
        if (columns.length <= count)
            return { columns: columns, h: tallest(columns, sections, g) };
    }
    const columns = pack(Infinity);
    return { columns: columns, h: tallest(columns, sections, g) };
}

// listColumns with sections free to break between items: the lowest column height at which
// filling each column as far as it goes needs no more than `count` of them.
function brokenSections(sections, count, g) {
    const fill = limit => {
        const columns = [[]];
        let h = 0;
        for (let i = 0; i < sections.length; i++) {
            const x = sections[i];
            let from = 0;
            while (from < x.items.length) {
                const column = columns[columns.length - 1];
                let used = h + (column.length ? g.block : 0) + 2 * g.pad + x.head;
                let to = from;
                while (to < x.items.length && used + x.items[to] <= limit) {
                    used += x.items[to];
                    to++;
                }
                if (to === from) {
                    if (column.length) {
                        columns.push([]);
                        h = 0;
                        continue;
                    }
                    used += x.items[to];
                    to++;
                }
                column.push({ section: x.s, from: from, to: to });
                h = used;
                from = to;
                if (from < x.items.length) {
                    columns.push([]);
                    h = 0;
                }
            }
        }
        if (!columns[columns.length - 1].length)
            columns.pop();
        return columns;
    };
    let lo = 0, hi = tallest(fill(Infinity), sections, g);
    for (let k = 0; k < 40 && hi - lo > 0.5; k++) {
        const mid = (lo + hi) / 2;
        if (fill(mid).length <= count)
            hi = mid;
        else
            lo = mid;
    }
    const columns = fill(hi);
    return { columns: columns, h: tallest(columns, sections, g) };
}

function tallest(columns, sections, g) {
    const bySection = {};
    for (let i = 0; i < sections.length; i++)
        bySection[sections[i].s] = sections[i];
    let most = 0;
    for (let c = 0; c < columns.length; c++) {
        let h = 0;
        for (let p = 0; p < columns[c].length; p++) {
            const piece = columns[c][p], x = bySection[piece.section];
            h += (p ? g.block : 0) + 2 * g.pad + x.head + sum(x.items, piece.from, piece.to);
        }
        most = Math.max(most, h);
    }
    return most;
}

// The columns a list is laid out in, the first that fits: the card's usual height with whole
// sections, in the fewest columns its usual width holds, then wider; the same with sections
// broken between items; then taller, up to the most it may take, scrolling past that.
function listFit(heads, rows, minCols, maxCols, baseH, roomH, gaps) {
    minCols = Math.max(1, Math.floor(minCols));
    maxCols = Math.max(minCols, Math.floor(maxCols));
    const modes = [true, false];
    for (let m = 0; m < modes.length; m++) {
        for (let c = minCols; c <= maxCols; c++) {
            const r = listColumns(heads, rows, c, baseH, gaps, modes[m]);
            if (r.h <= baseH)
                return r;
        }
    }
    return listColumns(heads, rows, maxCols, roomH, gaps);
}

function sum(values, from, to) {
    let total = 0;
    for (let i = from; i < to; i++)
        total += values[i] || 0;
    return total;
}

// Whether the pointer is reaching for a card: on it (or within `margin` of it), or closing in on
// it, its last stretch of travel (from `from` to `cursor`, over `ms` milliseconds) pointing into
// it give or take 12 degrees, from within `range`, and slower than `fast` px/s. A quicker stroke
// is the pointer going somewhere, not homing in on a card beside it. true holds the card and
// orb still so the card can be read and clicked; false lets them follow; null, for a pointer
// at rest, keeps whatever they were doing.
function reaching(cursor, from, ms, rect, margin, range, fast) {
    margin = margin === undefined ? 16 : margin;
    range = range === undefined ? 360 : range;
    fast = fast === undefined ? 900 : fast;
    if (!rect || rect.w <= 0 || rect.h <= 0)
        return false;
    const r = { x: rect.x - margin, y: rect.y - margin, w: rect.w + 2 * margin, h: rect.h + 2 * margin };
    if (cursor.x >= r.x && cursor.x <= r.x + r.w && cursor.y >= r.y && cursor.y <= r.y + r.h)
        return true;
    const dx = cursor.x - from.x, dy = cursor.y - from.y;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len < 3)
        return null;
    if (ms > 0 && len / ms * 1000 > fast)
        return false;
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

// "x,y" as a point, or null for anything else.
function parsePoint(text) {
    const parts = String(text).trim().split(",");
    if (parts.length !== 2 || parts[0].trim() === "" || parts[1].trim() === "")
        return null;
    const x = Number(parts[0]), y = Number(parts[1]);
    return isFinite(x) && isFinite(y) ? { x: x, y: y } : null;
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
