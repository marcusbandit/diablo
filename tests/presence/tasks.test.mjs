// The Tasks section's rows in presence/tasks.js, run in node: `node --test tests/presence/tasks.test.mjs`.
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../../presence/tasks.js", import.meta.url), "utf8").replace(/^\.pragma library\s*$/m, "");
const tasks = new Function(`${source}; return { rows, waiting, takenSeconds, taken, DONE_KEPT_S, FAILED_KEPT_S };`)();
const words = { seconds: "{s} s", minutes: "{m} min", hoursMinutes: "{h} h {m} min" };
const now = 100000;

test("waiting rows come first, then working, queued, failed and done", () => {
    const list = [
        { id: "d", state: "done", started_at: 1, finished_at: now - 10 },
        { id: "q", state: "queued", started_at: now - 5 },
        { id: "w2", state: "working", started_at: now - 50 },
        { id: "f", state: "failed", started_at: 1, finished_at: now - 20 },
        { id: "w1", state: "working", started_at: now - 90 },
        { id: "wait", state: "waiting", started_at: now - 10 },
    ];
    assert.deepEqual(tasks.rows(list, now).map(t => t.id), ["wait", "w1", "w2", "q", "f", "d"]);
});

test("finished rows leave after a short while, a failure later than a success", () => {
    const list = [
        { id: "d", state: "done", finished_at: now - tasks.DONE_KEPT_S - 1 },
        { id: "f", state: "failed", finished_at: now - tasks.DONE_KEPT_S - 1 },
        { id: "f2", state: "failed", finished_at: now - tasks.FAILED_KEPT_S - 1 },
        { id: "nofinish", state: "done" },
        { id: "bad", state: "nonsense" },
        null,
    ];
    assert.deepEqual(tasks.rows(list, now).map(t => t.id), ["f"]);
});

test("the header counts the tasks waiting on him", () => {
    assert.equal(tasks.waiting([]), 0);
    assert.equal(tasks.waiting([{ state: "waiting" }, { state: "working" }, { state: "waiting" }]), 2);
});

test("time taken runs to now while working and stops at the finish", () => {
    assert.equal(tasks.takenSeconds({ state: "working", started_at: now - 75 }, now), 75);
    assert.equal(tasks.takenSeconds({ state: "done", started_at: now - 600, finished_at: now - 300 }, now), 300);
    assert.equal(tasks.takenSeconds({ state: "queued" }, now), 0);
    assert.equal(tasks.taken(42, words), "42 s");
    assert.equal(tasks.taken(125, words), "2 min");
    assert.equal(tasks.taken(3 * 3600 + 7 * 60, words), "3 h 7 min");
});
