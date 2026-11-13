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

// Whether the orb and its cards should catch up with the cursor: only once it is well clear of
// them, so moving the pointer onto a card never pushes the card away.
function shouldRejoin(cursor, rects, reach) {
    reach = reach === undefined ? 120 : reach;
    for (let i = 0; i < rects.length; i++) {
        const r = rects[i];
        if (!r || r.w <= 0 || r.h <= 0)
            continue;
        const dx = Math.max(r.x - cursor.x, 0, cursor.x - (r.x + r.w));
        const dy = Math.max(r.y - cursor.y, 0, cursor.y - (r.y + r.h));
        if (Math.sqrt(dx * dx + dy * dy) <= reach)
            return false;
    }
    return true;
}

function clamp(v, lo, hi) {
    return hi < lo ? lo : Math.max(lo, Math.min(hi, v));
}
