import type { State } from "../tokens";
import type { EngineState } from "./engine";
import type { Question, Task } from "./wire";

/**
 * What he is doing, for his face and the working line. A hub that refused this device is
 * trouble; any turn the hub runs, from any client, is thinking; a reply that just landed is
 * speaking; him typing is listening.
 */
export function doing(s: EngineState, typing = false): State {
  if (s.link === "refused") return "trouble";
  if (s.sending || s.busy.length || Object.keys(s.turns).length) return "thinking";
  if (s.speaking) return "speaking";
  if (typing) return "listening";
  return "resting";
}

/** What waits on him: questions to answer, then work that stopped for him. */
export function asks(s: EngineState): { questions: Question[]; waiting: Task[] } {
  return { questions: s.questions, waiting: s.tasks.filter((t) => t.state === "waiting") };
}

export function needsYou(s: EngineState): number {
  const a = asks(s);
  return a.questions.length + a.waiting.length;
}
