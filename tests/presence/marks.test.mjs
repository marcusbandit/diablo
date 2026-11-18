import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../../presence/marks.js", import.meta.url), "utf8").replace(/^\.pragma library\s*$/m, "");
const mk = new Function(`${source}; return { clearOf, cleanMarks, withCaption, toLocal, ringFor, underlineFor, arrowFrom, arrowPath, labelSpot, badgeSpot, sayMs, advance, stage, waitBox, nodePath, edgePath, keepsEarlier };`)();

const screen = { x: 0, y: 0, w: 2560, h: 1440 };
const exportBox = { x: 1200, y: 300, w: 80, h: 30 };

function onEllipse(r, p) {
    return ((p.x - r.cx) ** 2) / (r.rx ** 2) + ((p.y - r.cy) ** 2) / (r.ry ** 2);
}

test("a ring goes round every corner of its box", () => {
    for (const box of [exportBox, { x: 10, y: 10, w: 600, h: 18 }, { x: 0, y: 0, w: 20, h: 300 }, { x: 5, y: 5, w: 2, h: 2 }]) {
        const r = mk.ringFor(box);
        for (const corner of [[0, 0], [1, 0], [0, 1], [1, 1]])
            assert.ok(onEllipse(r, { x: box.x + corner[0] * box.w, y: box.y + corner[1] * box.h }) < 1, JSON.stringify(box));
    }
});

test("a long line of text gets a ring that hugs it instead of a huge circle", () => {
    const r = mk.ringFor({ x: 0, y: 0, w: 600, h: 18 });
    assert.ok(r.rx < 600 / 2 + 60);
    assert.ok(r.ry < 80);
});

test("an underline runs the width of the box just below it", () => {
    const u = mk.underlineFor(exportBox);
    assert.ok(u.x1 <= exportBox.x && u.x2 >= exportBox.x + exportBox.w);
    assert.ok(u.y1 > exportBox.y + exportBox.h && u.y1 < exportBox.y + exportBox.h + 10);
});

test("an arrow starts at its point and stops just outside the ring round the box", () => {
    const from = { x: 800, y: 700 };
    const a = mk.arrowPath(from, exportBox, 6);
    assert.equal(a.x1, 800);
    assert.equal(a.y1, 700);
    const r = mk.ringFor(exportBox);
    const tip = onEllipse({ cx: r.cx, cy: r.cy, rx: r.rx + 6, ry: r.ry + 6 }, { x: a.x2, y: a.y2 });
    assert.ok(Math.abs(tip - 1) < 1e-6, "the tip sits on the ring's edge plus the gap");
    assert.ok(onEllipse(r, { x: a.x2, y: a.y2 }) > 1, "never inside the ring");
});

test("the arrowhead points along the curve into the box", () => {
    const a = mk.arrowPath({ x: 100, y: 1000 }, exportBox);
    const toBox = Math.atan2(exportBox.y + 15 - a.cy, exportBox.x + 40 - a.cx);
    assert.ok(Math.abs(a.angle - toBox) < 0.5);
    const mid = { x: (a.hx1 + a.hx2) / 2, y: (a.hy1 + a.hy2) / 2 };
    assert.ok(Math.hypot(mid.x - a.x2, mid.y - a.y2) > 5, "the head trails behind the tip");
});

test("the arrow bends instead of running straight", () => {
    const a = mk.arrowPath({ x: 200, y: 300 }, exportBox);
    const straightY = 300 + (a.cx - 200) * (a.y2 - 300) / (a.x2 - 200);
    assert.ok(Math.abs(a.cy - straightY) > 20);
});

test("an arrow comes from the orb when it is on the same monitor and clear of the box", () => {
    const from = mk.arrowFrom(exportBox, screen, { x: 400, y: 900 }, { x: 380, y: 880 });
    assert.deepEqual([from.x, from.y, from.auto], [400, 900, false]);
});

test("an orb on another monitor or on top of the box hands over to the cursor, then to a spot of its own", () => {
    const other = mk.arrowFrom(exportBox, screen, { x: 3000, y: 200 }, { x: 600, y: 600 });
    assert.deepEqual([other.x, other.y], [600, 600]);
    const auto = mk.arrowFrom(exportBox, screen, { x: 1230, y: 310 }, null);
    assert.equal(auto.auto, true);
    assert.ok(auto.x > exportBox.x + exportBox.w && auto.y > exportBox.y + exportBox.h, "towards the middle of the monitor");
    assert.ok(Math.hypot(auto.x - 1240, auto.y - 315) > 100);
});

test("a given point wins over the orb", () => {
    const from = mk.arrowFrom(exportBox, screen, { x: 400, y: 900 }, null, { x: 2000, y: 1000 });
    assert.deepEqual([from.x, from.y], [2000, 1000]);
});

test("staging gives every arrow its start in global pixels and leaves other marks alone", () => {
    const screens = [{ x: 0, y: 0, w: 2560, h: 1440 }, { x: 2560, y: 0, w: 1920, h: 1080 }];
    const marks = mk.stage([{ kind: "ring", box: exportBox }, { kind: "arrow", box: { x: 3000, y: 500, w: 50, h: 20 } }], screens, { x: 500, y: 500 }, null);
    assert.equal(marks[0].fromPoint, undefined);
    assert.ok(marks[1].fromPoint.x >= 2560, "the orb on the other monitor is not used");
});

test("an arrow from the orb leaves from its rim, not its middle", () => {
    const marks = mk.stage([{ kind: "arrow", box: exportBox }], [screen], { x: 400, y: 900 }, null, 56);
    assert.ok(Math.abs(Math.hypot(marks[0].fromPoint.x - 400, marks[0].fromPoint.y - 900) - 56) < 1e-6);
});

test("a label on a box the arrow points at goes to the side away from the arrow", () => {
    const marks = mk.stage([{ kind: "arrow", box: exportBox }, { kind: "label", text: "here", box: exportBox }], [screen], { x: 1240, y: 1200 }, null);
    assert.equal(marks[1].side[0], "above");
    assert.equal(marks[1].side[3], "below", "the side the arrow comes from is the last resort");
    const spot = mk.labelSpot(exportBox, { w: 60, h: 28 }, screen, marks[1].side);
    assert.equal(spot.side, "above");
    const ring = mk.ringFor(exportBox);
    assert.ok(spot.y + 28 <= ring.cy - ring.ry, "clear of the ring");
});

test("a label sits right of its box, flips left at the edge and goes below for a caption", () => {
    const size = { w: 120, h: 30 };
    assert.equal(mk.labelSpot(exportBox, size, screen).side, "right");
    assert.equal(mk.labelSpot({ x: 2450, y: 300, w: 80, h: 30 }, size, screen).side, "left");
    const caption = mk.labelSpot(exportBox, { w: 300, h: 50 }, screen, "below");
    assert.equal(caption.side, "below");
    assert.ok(caption.y > exportBox.y + exportBox.h);
    const squeezed = mk.labelSpot({ x: 0, y: 0, w: 2560, h: 1440 }, size, screen);
    assert.ok(squeezed.x >= 8 && squeezed.y >= 8 && squeezed.x + size.w <= screen.w - 8);
});

test("a step badge sits on the top left corner and never leaves the monitor", () => {
    const b = mk.badgeSpot(exportBox, 28, screen);
    assert.ok(b.x < exportBox.x && b.y < exportBox.y);
    const corner = mk.badgeSpot({ x: 0, y: 0, w: 40, h: 20 }, 28, screen);
    assert.deepEqual([corner.x, corner.y], [4, 4]);
});

test("marks off this monitor are left out and the rest move into its coordinates", () => {
    const right = { x: 2560, y: 0, w: 1920, h: 1080 };
    const local = mk.toLocal([{ kind: "ring", box: exportBox }, { kind: "ring", box: { x: 3000, y: 100, w: 40, h: 20 } }], right);
    assert.equal(local.length, 1);
    assert.deepEqual(local[0].box, { x: 440, y: 100, w: 40, h: 20 });
    const arrow = mk.toLocal([{ kind: "arrow", box: { x: 3000, y: 100, w: 40, h: 20 }, fromPoint: { x: 2000, y: 600 } }], screen);
    assert.equal(arrow.length, 1, "an arrow crossing onto this monitor is drawn here too");
});

test("a mark without a usable box or kind is dropped; a point becomes a small box", () => {
    const marks = mk.cleanMarks([{ kind: "ring" }, { kind: "zap", box: exportBox }, { kind: "num", n: 2, point: { x: 100, y: 100 } }]);
    assert.equal(marks.length, 1);
    assert.deepEqual(marks[0].box, { x: 88, y: 88, w: 24, h: 24 });
});

test("a caption is asked for, never added on its own", () => {
    assert.equal(mk.withCaption({ say: "Click export.", marks: [{ kind: "ring", box: exportBox }] }).length, 1);
    const marks = mk.withCaption({ say: "Click export.", caption: true, marks: [{ kind: "ring", box: exportBox }] });
    assert.equal(marks[1].kind, "label");
    assert.equal(marks[1].text, "Click export.");
});

test("a beat lasts about as long as its line takes to say", () => {
    assert.equal(mk.sayMs("", 15), 2500);
    assert.equal(mk.sayMs("Hi", 15), 1800);
    assert.equal(mk.sayMs("x".repeat(150), 15), 10500);
    assert.equal(mk.sayMs("x".repeat(150), 30), 5500);
    assert.equal(mk.sayMs("x".repeat(5000), 15), 20000);
});

const twoBeats = {
    show: "s1", count: 2, beats: [
        { n: 0, say: "x".repeat(30), marks: [{ kind: "ring", box: exportBox }] },
        { n: 1, say: "y".repeat(30), marks: [{ kind: "underline", box: exportBox }] }
    ]
};

test("beats play in order, each for its line, then linger and end", () => {
    const o = { cps: 15, lingerMs: 12000 };
    let p = mk.advance(twoBeats, null, 0, null, {}, o);
    assert.equal(p.index, 0);
    p = mk.advance(twoBeats, p, 2000, null, {}, o);
    assert.equal(p.index, 0);
    p = mk.advance(twoBeats, p, 2600, null, {}, o);
    assert.equal(p.index, 1);
    assert.equal(p.since, 2600);
    p = mk.advance(twoBeats, p, 5100, null, {}, o);
    assert.equal(p.index, 1);
    assert.equal(p.over, false);
    p = mk.advance(twoBeats, p, 5100 + 12000, null, {}, o);
    assert.equal(p.over, true);
});

test("a beat that has not arrived yet keeps the last marks up, then gives up", () => {
    const partial = { show: "s2", count: 3, beats: [twoBeats.beats[0]] };
    let p = mk.advance(partial, null, 0, null, {}, {});
    p = mk.advance(partial, p, 3000, null, {}, {});
    assert.equal(p.index, 0);
    assert.equal(p.over, false);
    const more = { ...partial, beats: [...partial.beats, twoBeats.beats[1]] };
    p = mk.advance(more, p, 4000, null, {}, {});
    assert.equal(p.index, 1);
    const stuck = mk.advance(partial, mk.advance(partial, null, 0, null, {}, {}), 2600, null, {}, {});
    assert.equal(mk.advance(partial, stuck, 2600 + 20000, null, {}, {}).over, true);
});

test("a show that is cleared or replaced ends at once", () => {
    const p = mk.advance(twoBeats, null, 0, null, {}, {});
    assert.equal(mk.advance({ ...twoBeats, cleared: true }, p, 100, null, {}, {}).over, true);
    const next = mk.advance({ ...twoBeats, show: "s9" }, p, 100, null, {}, {});
    assert.deepEqual([next.show, next.index], ["s9", 0]);
});

const walk = {
    show: "w1", count: 2, beats: [
        { n: 0, say: "Click file.", wait: "click", marks: [{ kind: "ring", box: { x: 10, y: 10, w: 40, h: 20 } }] },
        { n: 1, say: "Then export.", lazy: true, marks: [{ kind: "ring", target: { text: "Export" } }] }
    ]
};

test("a waiting beat holds until the pointer goes into its box and out again", () => {
    let p = mk.advance(walk, null, 0, { x: 500, y: 500 }, {}, {});
    p = mk.advance(walk, p, 9000, { x: 500, y: 500 }, {}, {});
    assert.equal(p.index, 0, "time alone does not pass a waiting beat");
    p = mk.advance(walk, p, 9100, { x: 30, y: 20 }, {}, {});
    assert.equal(p.entered, true);
    p = mk.advance(walk, p, 9200, { x: 30, y: 20 }, {}, {});
    assert.equal(p.index, 0);
    p = mk.advance(walk, p, 9300, { x: 300, y: 300 }, {}, {});
    assert.equal(p.index, 0, "the next beat still has to be found on screen");
    assert.equal(p.need, 1);
    const found = { 1: { ...walk.beats[1], lazy: false, marks: [{ kind: "ring", box: exportBox }] } };
    p = mk.advance(walk, p, 9400, { x: 300, y: 300 }, found, {});
    assert.deepEqual([p.index, p.need], [1, -1]);
});

test("a waiting beat gives up after its timeout", () => {
    let p = mk.advance(walk, null, 0, null, {}, { waitMs: 60000 });
    p = mk.advance(walk, p, 59000, null, {}, { waitMs: 60000 });
    assert.equal(p.need, -1);
    p = mk.advance(walk, p, 60000, null, {}, { waitMs: 60000 });
    assert.equal(p.need, 1);
});

test("the wait follows the first mark that has a box of its own, not a spotlight", () => {
    const box = mk.waitBox(mk.cleanMarks([{ kind: "spotlight", box: exportBox }, { kind: "ring", box: { x: 1, y: 2, w: 3, h: 4 } }]));
    assert.deepEqual(box, { x: 1, y: 2, w: 3, h: 4 });
});

test("a box node's outline starts at its top middle and stays on its edges", () => {
    const box = { x: 100, y: 200, w: 160, h: 54 };
    const path = mk.nodePath(box, "box");
    assert.ok(path.startsWith("M 180 200"));
    assert.ok(path.trim().endsWith("Z"));
    const nums = path.match(/-?\d+(\.\d+)?/g).map(Number);
    for (let i = 0; i < nums.length; i++)
        assert.ok(nums[i] >= 0 && nums[i] <= 260, path);
    const pts = [...path.matchAll(/[ML] (-?[\d.]+) (-?[\d.]+)/g)].map(m => ({ x: +m[1], y: +m[2] }));
    for (const p of pts) {
        const onX = Math.abs(p.x - box.x) < 1e-6 || Math.abs(p.x - box.x - box.w) < 1e-6;
        const onY = Math.abs(p.y - box.y) < 1e-6 || Math.abs(p.y - box.y - box.h) < 1e-6;
        assert.ok(onX || onY, JSON.stringify(p));
    }
});

test("a round node is a pill and a diamond touches the middle of each side", () => {
    const box = { x: 0, y: 0, w: 200, h: 60 };
    assert.ok(mk.nodePath(box, "round").includes("A 30 30"));
    assert.equal(mk.nodePath(box, "diamond"), "M 100 0 L 200 30 L 100 60 L 0 30 Z");
});

test("an edge ends at its end point with the head trailing back along the curve", () => {
    const e = mk.edgePath({ x: 100, y: 100 }, { x: 100, y: 300 }, null);
    assert.equal(e.x2, 100);
    assert.equal(e.y2, 300);
    assert.ok(Math.abs(e.angle - Math.PI / 2) < 1e-9, "a straight edge points along itself");
    assert.ok(e.hy1 < 300 && e.hy2 < 300);
    assert.ok(Math.abs((e.hx1 + e.hx2) / 2 - 100) < 1e-9);
    assert.equal(e.mx, 100);
    assert.equal(e.my, 200);
    const bent = mk.edgePath({ x: 0, y: 0 }, { x: 200, y: 0 }, { x: 100, y: 80 });
    assert.ok(bent.my > 0 && bent.my < 80, "the label sits on the curve, not at its control point");
    assert.ok(bent.angle < 0, "the head follows the curve into the end");
});

test("diagram pieces survive cleaning and move with their points onto the monitor", () => {
    const marks = mk.cleanMarks([
        { kind: "node", text: "Kernel", box: { x: 2700, y: 300, w: 160, h: 54 } },
        { kind: "edge", start: { x: 2780, y: 354 }, end: { x: 2780, y: 500 }, via: { x: 2780, y: 427 }, box: { x: 2780, y: 354, w: 0, h: 146 } },
        { kind: "title", text: "How", box: { x: 2600, y: 100, w: 300, h: 46 } }
    ]);
    assert.equal(marks.length, 3);
    const local = mk.toLocal(marks, { x: 2560, y: 0, w: 1920, h: 1080 });
    assert.equal(local.length, 3);
    assert.deepEqual(local[1].start, { x: 220, y: 354 });
    assert.deepEqual(local[1].end, { x: 220, y: 500 });
    assert.deepEqual(local[1].via, { x: 220, y: 427 });
    assert.equal(local[0].box.x, 140);
    assert.equal(mk.toLocal(marks, { x: -2560, y: 0, w: 2560, h: 1440 }).length, 0);
});

test("only a keep beat holds on to the beats before it", () => {
    assert.equal(mk.keepsEarlier({ keep: true }), true);
    assert.equal(mk.keepsEarlier({ marks: [] }), false);
    assert.equal(mk.keepsEarlier(null), false);
});

test("a drawing lingers as long as the show asks", () => {
    const show = { show: "d", count: 1, beats: [{ n: 0, say: "One.", keep: true, marks: [] }] };
    let p = mk.advance(show, null, 0, null, {}, {});
    p = mk.advance(show, p, 0, null, {}, {});
    p = mk.advance(show, p, 5000, null, {}, { lingerMs: 45000 });
    p = mk.advance(show, p, 30000, null, {}, { lingerMs: 45000 });
    assert.equal(p.over, false);
    p = mk.advance(show, p, 51000, null, {}, { lingerMs: 45000 });
    assert.equal(p.over, true);
});
