.pragma library
// Whether Diablo is busy, what the pill says and which moons circle him. Pure, so
// tests/presence/busy.test.mjs runs the same code in node (it drops the pragma line above).
// Only his own running turns make him busy. Task cards are watched T3 threads and update_task
// work: they are not him, so they never make him busy and never lend him their words.

// The task states that are still running; busy.py also sends finished ones for the task list.
const ACTIVE = ["queued", "working", "waiting"];

// Busy while a turn of his runs, or an answer the ears hold back while the hub was busy a moment ago.
function isBusy(turns, earsBehind) {
    return (turns || []).length > 0 || !!earsBehind;
}

// The pill's words while he is busy: the newest turn's step, with a count of the others.
function busyWords(turns) {
    turns = turns || [];
    if (turns.length === 0)
        return "working";
    const words = turns[turns.length - 1].words || "thinking";
    return turns.length > 1 ? `${words} +${turns.length - 1}` : words;
}

// One moon for each running turn, and for each live task card riding along while he is busy;
// none at all while he is not. A finished card is in the list but never a moon.
function moons(turns, tasks, earsBehind) {
    if (!isBusy(turns, earsBehind))
        return [];
    const out = [];
    for (const t of turns || []) {
        const id = `turn:${t.origin}:${t.since}`;
        if (!out.some(m => m.id === id))
            out.push({ id: id });
    }
    if (out.length === 0)
        out.push({ id: "ears" });
    for (const t of tasks || [])
        if (ACTIVE.includes(t.state))
            out.push({ id: `task:${t.id}`, waiting: t.state === "waiting" });
    return out;
}
