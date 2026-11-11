// The card placement in presence/place.js, run in node: `node --test tests/presence/place.test.mjs`.
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../../presence/place.js", import.meta.url), "utf8").replace(/^\.pragma library\s*$/m, "");
const place = new Function(`${source}; return { cardRect, panelRect, shouldRejoin };`)();

const screen = { w: 2560, h: 1440 };
const card = { w: 300, h: 120 };

test("a card opens just below the orb, lined up with it", () => {
    const orb = { x: 800, y: 400, w: 160, h: 100 };
    const r = place.cardRect(orb, card, screen);
    assert.equal(r.below, true);
    assert.equal(r.x, 800);
    assert.equal(r.y, 510);
});

test("it grows from the point nearest the orb's middle", () => {
    const orb = { x: 800, y: 400, w: 160, h: 100 };
    const r = place.cardRect(orb, card, screen);
    assert.equal(r.originX, 80);
    assert.equal(r.originY, 0);
});

test("near the bottom it opens above the orb and grows down out of it", () => {
    const orb = { x: 800, y: 1300, w: 160, h: 100 };
    const r = place.cardRect(orb, card, screen);
    assert.equal(r.below, false);
    assert.equal(r.y, 1300 - 10 - 120);
    assert.equal(r.originY, 120);
});

test("near the right edge it lines up with the orb's right side", () => {
    const orb = { x: 2380, y: 400, w: 160, h: 100 };
    const r = place.cardRect(orb, card, screen);
    assert.equal(r.x + card.w, 2540);
    assert.ok(r.originX > card.w / 2, "grows from the right part, where the orb is");
});

test("the card never leaves the screen", () => {
    for (const orb of [{ x: 0, y: 0, w: 160, h: 100 }, { x: 2500, y: 1400, w: 160, h: 100 }, { x: -50, y: 700, w: 160, h: 100 }]) {
        const r = place.cardRect(orb, card, screen);
        assert.ok(r.x >= 12 && r.x + card.w <= screen.w - 12, JSON.stringify(r));
        assert.ok(r.y >= 12 && r.y + card.h <= screen.h - 12, JSON.stringify(r));
        assert.ok(r.originX >= 0 && r.originX <= card.w);
    }
});

test("the card ignores the cursor: only the orb moves it", () => {
    const orb = { x: 1000, y: 500, w: 160, h: 100 };
    assert.deepEqual(place.cardRect(orb, card, screen), place.cardRect({ ...orb }, card, screen));
    const moved = place.cardRect({ ...orb, x: 1200 }, card, screen);
    assert.equal(moved.x, 1200);
});

test("the central card sits in the middle and fits the screen", () => {
    const p = place.panelRect(screen);
    assert.equal(p.x * 2 + p.w, screen.w);
    assert.equal(p.y * 2 + p.h, screen.h);
    assert.ok(p.w <= 980 && p.h <= 720);
    const small = place.panelRect({ w: 800, h: 500 });
    assert.ok(small.w <= 776 && small.h <= 476);
});

test("the orb holds still while the cursor is near its cards", () => {
    const rects = [{ x: 800, y: 400, w: 160, h: 100 }, { x: 800, y: 510, w: 300, h: 120 }];
    assert.equal(place.shouldRejoin({ x: 900, y: 560 }, rects), false, "on the card");
    assert.equal(place.shouldRejoin({ x: 780, y: 380 }, rects), false, "just beside the orb");
    assert.equal(place.shouldRejoin({ x: 1600, y: 560 }, rects), true, "well away");
});
