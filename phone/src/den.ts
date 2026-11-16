import type { Pending, Task } from "@diablo/hub";

import type { Mood } from "./ui/faces";
import type { LinkState, Moment, Turn } from "./model";

export const ACTIVE = new Set(["queued", "working", "waiting"]);
const FINISHED_SHOWN_S = 12 * 3600;

export type DenInput = {
  link: LinkState;
  turns: Record<string, Turn>;
  tasks: Task[];
  owed: Pending[];
  moment: Moment | null;
  typing?: boolean;
  now?: number;
};

/**
 * The cards the den lists: work waiting on Marcus (shown under Needs you), running work, then
 * what finished lately and was not put away.
 */
export function shownTasks(tasks: Task[], now = Date.now() / 1000): { waiting: Task[]; active: Task[]; finished: Task[] } {
  const all = tasks.filter((t) => ACTIVE.has(t.state));
  const order = { waiting: 0, working: 1, queued: 2 } as Record<string, number>;
  all.sort((a, b) => (order[a.state] ?? 3) - (order[b.state] ?? 3) || (b.updated_at ?? 0) - (a.updated_at ?? 0));
  const waiting = all.filter((t) => t.state === "waiting");
  const active = all.filter((t) => t.state !== "waiting");
  const finished = tasks
    .filter((t) => !ACTIVE.has(t.state) && !t.seen_at && now - (t.finished_at ?? t.updated_at ?? 0) < FINISHED_SHOWN_S)
    .sort((a, b) => (b.finished_at ?? 0) - (a.finished_at ?? 0));
  return { waiting, active, finished };
}

/** Which face he wears: a passing feeling first, then what he is doing, then what he waits on. */
export function moodOf(d: DenInput): Mood {
  const now = d.now ?? Date.now();
  if (d.moment && d.moment.until > now) return d.moment.face;
  if (d.link === "offline" || d.link === "unpaired" || d.link === "refused") return "asleep";
  const turns = Object.values(d.turns);
  if (turns.some((t) => t.state === "doing")) return "working";
  if (turns.length) return "thinking";
  if (d.typing) return "listening";
  if (d.owed.length) return "expectant";
  const { waiting, active } = shownTasks(d.tasks, now / 1000);
  if (waiting.length) return "expectant";
  if (active.length) return "proud";
  return "resting";
}

const count = (n: number, one: string, many: string) => (n === 1 ? one : `${n === 2 ? "Two" : n === 3 ? "Three" : n} ${many}`);

/** One line in his voice about where things stand, the first thing on the den. */
export function headline(d: DenInput): string {
  const now = d.now ?? Date.now();
  if (d.moment && d.moment.until > now) {
    if (d.moment.face === "delighted") return "Done. That one landed.";
    if (d.moment.face === "sorry") return "That one broke. I'm on it.";
  }
  if (d.link === "unpaired") return "We haven't met on this phone yet.";
  if (d.link === "refused") return "The hub stopped knowing this phone.";
  if (d.link === "offline") return "I can't reach the hub. Anything you write waits here.";
  if (d.link === "checking") return "Waking up.";
  const turn = Object.values(d.turns)[0];
  if (turn) return turn.state === "doing" ? `${turn.tool || turn.step || "Working on it"}.`.replace(/\.\.$/, ".") : "Thinking.";
  const { waiting, active } = shownTasks(d.tasks, now / 1000);
  const owed = d.owed.length + waiting.length;
  const running = active.length;
  if (owed && running) return `${count(owed, "One thing needs you", "things need you")}. ${count(running, "One job running", "jobs running")}.`;
  if (owed) return `${count(owed, "One thing needs you", "things need you")}.`;
  if (running) return `${count(running, "One job running", "jobs running")}. Nothing needs you.`;
  return "All quiet. Nothing needs you.";
}

/** "4 min", "2 h", "3 d" since a unix time. */
export function since(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "";
  const s = Math.max(0, now - ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}
