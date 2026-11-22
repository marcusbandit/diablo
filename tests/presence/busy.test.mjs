// Diablo's busy state in presence/busy.js, run in node: `node --test tests/presence/busy.test.mjs`.
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../../presence/busy.js", import.meta.url), "utf8").replace(/^\.pragma library\s*$/m, "");
const busy = new Function(`${source}; return { isBusy, busyWords, moons };`)();

const anibeam = { id: "thread-1", state: "working", title: "Split the app core into modules" };

test("a working task card alone never makes him busy or gives him moons", () => {
    assert.equal(busy.isBusy([], false), false);
    assert.deepEqual(busy.moons([], [anibeam], false), []);
});

test("his words come from his own turn, never from a task card's title", () => {
    assert.equal(busy.busyWords([]), "working");
    const turn = { words: "reading files", origin: "voice", since: 1 };
    assert.equal(busy.busyWords([turn]), "reading files");
    assert.ok(!busy.busyWords([turn]).includes(anibeam.title));
});

test("while a turn of his runs, the task cards ride along as moons", () => {
    const turn = { words: "thinking", origin: "voice", since: 1 };
    const moons = busy.moons([turn], [anibeam, { id: "thread-2", state: "waiting" }], false);
    assert.deepEqual(moons.map(m => m.id), ["turn:voice:1", "task:thread-1", "task:thread-2"]);
    assert.equal(moons[2].waiting, true);
});

test("an answer the ears hold back still counts as his own", () => {
    assert.equal(busy.isBusy([], true), true);
    assert.deepEqual(busy.moons([], [], true), [{ id: "ears" }]);
});

test("two turns read as the newest with a count", () => {
    const turns = [{ words: "thinking", origin: "a", since: 1 }, { words: "the lights", origin: "b", since: 2 }];
    assert.equal(busy.busyWords(turns), "the lights +1");
});

test("a finished task card in the feed is never a moon", () => {
    const turn = { words: "thinking", origin: "voice", since: 1 };
    const moons = busy.moons([turn], [anibeam, { id: "d", state: "done" }, { id: "f", state: "failed" }], false);
    assert.deepEqual(moons.map(m => m.id), ["turn:voice:1", "task:thread-1"]);
});
