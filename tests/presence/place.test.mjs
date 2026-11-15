// The card placement in presence/place.js, run in node: `node --test tests/presence/place.test.mjs`.
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../../presence/place.js", import.meta.url), "utf8").replace(/^\.pragma library\s*$/m, "");
const place = new Function(`${source}; return { cardRect, panelRect, panelRoom, columnsIn, listColumns, listFit, reaching, parsePoint, nearestRect, restSpot };`)();

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

// A stroke of `d` over 120 ms: 20 px is about 170 px/s, a slow approach.
const T = 120;

test("a pointer on its card is reaching for it", () => {
    const card = { x: 826, y: 536, w: 300, h: 120 };
    assert.equal(place.reaching({ x: 900, y: 560 }, { x: 900, y: 560 }, T, card), true, "resting on it");
    assert.equal(place.reaching({ x: 815, y: 530 }, { x: 815, y: 530 }, T, card), true, "just off its corner");
    assert.equal(place.reaching({ x: 900, y: 560 }, { x: 600, y: 560 }, T, card), true, "even passing over it fast");
});

test("a pointer at rest beside the card changes nothing", () => {
    const card = { x: 826, y: 536, w: 300, h: 120 };
    assert.equal(place.reaching({ x: 800, y: 400 }, { x: 800, y: 400 }, T, card), null);
    assert.equal(place.reaching({ x: 800, y: 400 }, { x: 801, y: 402 }, T, card), null, "a jitter of a pixel or two");
});

test("closing in on the card holds it, heading anywhere else lets it follow", () => {
    const card = { x: 826, y: 536, w: 300, h: 120 };
    const at = { x: 800, y: 400 };
    const from = (dx, dy) => ({ x: at.x - dx, y: at.y - dy });
    assert.equal(place.reaching(at, from(10, 20), T, card), true, "down and to the right, into it");
    assert.equal(place.reaching(at, from(20, 10), T, card), true, "a shallower line still lands on it");
    assert.equal(place.reaching(at, from(0, 20), T, card), true, "slowly straight down lands on its near corner");
    assert.equal(place.reaching({ x: 700, y: 400 }, { x: 700, y: 380 }, T, card), false, "straight down well left of it misses");
    assert.equal(place.reaching(at, from(20, 0), T, card), false, "straight right passes over it");
    assert.equal(place.reaching(at, from(0, -20), T, card), false, "up, away from it");
    assert.equal(place.reaching(at, from(-20, 0), T, card), false, "left, away from it");
    assert.equal(place.reaching(at, from(-10, 20), T, card), false, "down and to the left, past it");
});

test("a quick stroke towards the card is going somewhere else, so they follow", () => {
    const card = { x: 826, y: 536, w: 300, h: 120 };
    const at = { x: 800, y: 400 };
    assert.equal(place.reaching(at, { x: 800, y: 260 }, T, card), false, "straight down at over 1000 px/s");
    assert.equal(place.reaching(at, { x: 740, y: 280 }, T, card), false, "down and right, fast");
    assert.equal(place.reaching(at, { x: 770, y: 340 }, T, card), true, "the same line slowing down");
});

test("a card too far down the line is not being reached for", () => {
    const card = { x: 826, y: 1200, w: 300, h: 120 };
    assert.equal(place.reaching({ x: 900, y: 400 }, { x: 900, y: 380 }, T, card), false);
    assert.equal(place.reaching({ x: 900, y: 900 }, { x: 900, y: 880 }, T, card), true);
});

test("a pointer past the card and leaving it lets it follow", () => {
    const card = { x: 826, y: 536, w: 300, h: 120 };
    assert.equal(place.reaching({ x: 900, y: 700 }, { x: 900, y: 680 }, T, card), false);
});

test("a cursor written as x,y reads only when both halves are numbers", () => {
    assert.deepEqual(place.parsePoint("1200, 340"), { x: 1200, y: 340 });
    assert.deepEqual(place.parsePoint("-5,7.5\n"), { x: -5, y: 7.5 });
    assert.equal(place.parsePoint(",500"), null);
    assert.equal(place.parsePoint("500,"), null);
    assert.equal(place.parsePoint("foo"), null);
    assert.equal(place.parsePoint("1,2,3"), null);
    assert.equal(place.parsePoint("a,b"), null);
});

test("a point between monitors goes to the nearest one, not the first", () => {
    // a 2560x1440 monitor and a shorter 1920x1080 one beside it, tops aligned
    const rects = [{ x: 0, y: 0, w: 2560, h: 1440 }, { x: 2560, y: 0, w: 1920, h: 1080 }];
    assert.equal(place.nearestRect({ x: 100, y: 100 }, rects), 0, "inside the first");
    assert.equal(place.nearestRect({ x: 3000, y: 500 }, rects), 1, "inside the second");
    assert.equal(place.nearestRect({ x: 3000, y: 1300 }, rects), 1, "dead corner under the shorter monitor");
    assert.equal(place.nearestRect({ x: 2600, y: 1300 }, rects), 0, "past the first monitor edge but nearer it than the shorter one");
    assert.equal(place.nearestRect({ x: 5000, y: -50 }, rects), 1, "past the right edge");
    assert.equal(place.nearestRect({ x: 1, y: 1 }, []), -1, "no screens");
});

test("a moving pointer keeps him beside it, fullscreen or not", () => {
    assert.equal(place.restSpot(false, false, false), "follow");
    assert.equal(place.restSpot(false, false, true), "follow");
});

test("a resting pointer over a fullscreen window fades him away", () => {
    assert.equal(place.restSpot(true, false, true), "away");
});

test("a resting pointer over an ordinary window sends him to the corner", () => {
    assert.equal(place.restSpot(true, false, false), "corner");
});

test("while Marcus needs him he stays beside the resting pointer", () => {
    assert.equal(place.restSpot(true, true, true), "follow");
    assert.equal(place.restSpot(true, true, false), "follow");
});

test("the central card grows to what its content asks for, up to a share of the screen", () => {
    const usual = place.panelRect(screen);
    assert.deepEqual(place.panelRect(screen, 12, { w: 400, h: 300 }), usual);
    const grown = place.panelRect(screen, 12, { w: 1300, h: 900 });
    assert.equal(grown.w, 1300);
    assert.equal(grown.h, 900);
    assert.equal(grown.x * 2 + grown.w, screen.w);
    const room = place.panelRoom(screen);
    const huge = place.panelRect(screen, 12, { w: 9000, h: 9000 });
    assert.equal(huge.w, room.w);
    assert.equal(huge.h, room.h);
    assert.ok(room.h <= screen.h * 0.86 && room.w <= 1720);
});

test("columns are counted from the room they have", () => {
    assert.equal(place.columnsIn(916, 280, 24), 3);
    assert.equal(place.columnsIn(200, 280, 24), 1);
    assert.equal(place.columnsIn(1656, 280, 24), 5);
});

// A packing list: four sections of 4, 5, 9 and 4 items, 30 px a row under a 24 px heading.
const gaps = { pad: 14, block: 14 };
const heads = [24, 24, 24, 24];
const rows = [4, 5, 9, 4].map(n => Array(n).fill(30));

test("a list's whole sections go down the columns in order", () => {
    const r = place.listColumns(heads, rows, 3, 560, gaps);
    assert.equal(r.columns.length, 3);
    const order = r.columns.flat().map(p => p.section);
    assert.deepEqual(order, [0, 1, 2, 3]);
    assert.ok(r.columns.flat().every(p => p.from === 0 && p.to === rows[p.section].length));
    assert.equal(r.h, 2 * 14 + 24 + 4 * 30 + 14 + 2 * 14 + 24 + 5 * 30);
});

test("the tallest column is as short as whole sections allow", () => {
    const r = place.listColumns(heads, rows, 2, 9999, gaps);
    const heights = r.columns.map(c => c.reduce((h, p, i) => h + (i ? 14 : 0) + 28 + 24 + 30 * (p.to - p.from), 0));
    assert.equal(Math.max(...heights), r.h);
    assert.equal(r.h, 28 + 24 + 270 + 14 + 28 + 24 + 120);
});

test("one long section breaks between items when whole it would not fit", () => {
    const long = [Array(27).fill(30)];
    const r = place.listColumns([24], long, 3, 560, gaps);
    assert.equal(r.columns.length, 3);
    assert.deepEqual(r.columns.map(c => c[0].to - c[0].from), [9, 9, 9]);
    assert.deepEqual(r.columns.map(c => c[0].from), [0, 9, 18]);
    assert.equal(r.h, 28 + 24 + 9 * 30);
});

test("a list keeps the card's usual size when it fits there", () => {
    const r = place.listFit(heads, rows, 3, 5, 560, 1000, gaps);
    assert.equal(r.columns.length, 3);
    assert.ok(r.h <= 560);
});

test("a longer list goes wider before it goes taller, and only then scrolls", () => {
    const many = Array(10).fill(24);
    const big = Array(10).fill(0).map(() => Array(10).fill(30));
    const wider = place.listFit(many, big, 3, 5, 1100, 1100, gaps);
    assert.equal(wider.columns.length, 4);
    const taller = place.listFit(many, big, 3, 5, 560, 1100, gaps);
    assert.equal(taller.columns.length, 5);
    assert.ok(taller.h > 560 && taller.h <= 1100);
    const scrolls = place.listFit(many, big, 3, 3, 560, 700, gaps);
    assert.equal(scrolls.columns.length, 3);
    assert.ok(scrolls.h > 700);
});

test("an empty list lays out nothing", () => {
    assert.deepEqual(place.listColumns([], [], 3, 560, gaps), { columns: [], h: 0 });
    assert.deepEqual(place.listColumns([24], [[]], 3, 560, gaps), { columns: [], h: 0 });
});

test("a list takes one more column before it breaks a section", () => {
    const r = place.listFit(heads, rows, 3, 5, 370, 1000, gaps);
    assert.equal(r.columns.length, 4);
    assert.ok(r.columns.flat().every(p => p.from === 0 && p.to === rows[p.section].length));
    assert.ok(r.h <= 370);
});
