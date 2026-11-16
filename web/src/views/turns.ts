import type { HubEvent } from "@diablo/hub";

export type Turn = { state: "thinking" | "doing"; steps: { step: string; tool: string }[]; since: number };
export type Turns = Record<string, Turn>;

/** The running turns after one hub event. Returns the same object when nothing changed. */
export function applyTurnEvent(turns: Turns, event: HubEvent, now: number): Turns {
  if (event.kind !== "turn") return turns;
  const section = typeof event.section === "string" ? event.section : "";
  if (!section) return turns;
  const state = event.state;
  if (state === "thinking") return { ...turns, [section]: { state: "thinking", steps: [], since: now } };
  if (state === "doing") {
    const was = turns[section] ?? { state: "thinking", steps: [], since: now };
    const step = { step: String(event.step ?? ""), tool: String(event.tool ?? "") };
    return { ...turns, [section]: { state: "doing", steps: [...was.steps, step].slice(-12), since: was.since } };
  }
  if (state === "done" || state === "failed") {
    if (!(section in turns)) return turns;
    const next = { ...turns };
    delete next[section];
    return next;
  }
  return turns;
}

export function turnWords(turn: Turn): string {
  if (turn.state === "thinking" || !turn.steps.length) return "Thinking";
  const last = turn.steps[turn.steps.length - 1];
  return last.tool ? `Using ${last.tool}` : last.step || "Working";
}
