.pragma library
// The Tasks section of the central card, worked out from the same task feed the moons read
// (busy.py). Pure, so tests/presence/tasks.test.mjs runs the same code in node (it drops the
// pragma line above). Times are seconds on the wall clock, as the hub stamps them.

// How long a finished card stays in the list: busy.py keeps them as long, and drops them after.
const DONE_KEPT_S = 120;
const FAILED_KEPT_S = 600;
const ORDER = { waiting: 0, working: 1, queued: 2, failed: 3, done: 4 };

function keptFor(state) {
    return state === "failed" ? FAILED_KEPT_S : DONE_KEPT_S;
}

function finished(task) {
    return task.state === "done" || task.state === "failed";
}

// The rows to list: finished ones only while they are fresh, waiting ones first, then working,
// queued, failed and done. Running rows keep their oldest first, finished ones their newest.
function rows(tasks, now) {
    const shown = (tasks || []).filter(t => t && t.id && t.state in ORDER
        && (!finished(t) || (t.finished_at && now - t.finished_at <= keptFor(t.state))));
    return shown.sort((a, b) => {
        const rank = ORDER[a.state] - ORDER[b.state];
        if (rank !== 0)
            return rank;
        if (finished(a))
            return (b.finished_at || 0) - (a.finished_at || 0);
        return (a.started_at || 0) - (b.started_at || 0);
    });
}

function waiting(tasks) {
    return (tasks || []).filter(t => t && t.state === "waiting").length;
}

// Seconds a task has taken: to its finish, or to now while it runs.
function takenSeconds(task, now) {
    if (!task.started_at)
        return 0;
    const end = finished(task) && task.finished_at ? task.finished_at : now;
    return Math.max(0, Math.floor(end - task.started_at));
}

// The time taken in words, with the wording from `words` ({seconds, minutes, hoursMinutes}).
function taken(seconds, words) {
    if (seconds < 60)
        return words.seconds.replace("{s}", seconds);
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60)
        return words.minutes.replace("{m}", minutes);
    return words.hoursMinutes.replace("{h}", Math.floor(minutes / 60)).replace("{m}", minutes % 60);
}
