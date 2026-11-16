import type { Block, Message } from "@diablo/hub";

/** Both lists together, each id once (the newer copy wins), oldest first. */
export function mergeMessages(have: Message[], incoming: Message[]): Message[] {
  const byId = new Map<number, Message>();
  for (const m of have) byId.set(m.id, m);
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

/** Drops what an edit withdrew or an answer again replaced. */
export function shown(messages: Message[]): Message[] {
  return messages.filter((m) => !m.meta || m.meta.superseded_by == null);
}

export function blocksOf(message: Message): Block[] {
  const objects = message.meta?.objects;
  if (!Array.isArray(objects)) return [];
  return objects.filter((o): o is Block => !!o && typeof o === "object" && typeof (o as Block).kind === "string");
}

export type Step = { step: string; tool: string };

export function stepsOf(message: Message): Step[] {
  const steps = message.meta?.steps;
  if (!Array.isArray(steps)) return [];
  return steps
    .filter((s) => s && typeof s === "object")
    .map((s) => ({ step: String((s as Step).step ?? ""), tool: String((s as Step).tool ?? "") }));
}

export function clientIdOf(message: Message): string | null {
  const id = message.meta?.client_id;
  return id == null ? null : String(id);
}

export function newClientId(now = Date.now(), random = Math.random()): string {
  return `web-${now.toString(36)}-${Math.floor(random * 0xffffff).toString(36)}`;
}

/** Puts a changed question block into the message that carries it. Unchanged when absent. */
export function patchQuestion(messages: Message[], messageId: number | undefined, question: Block): Message[] {
  const qid = question.id;
  let changed = false;
  const out = messages.map((m) => {
    if (messageId != null && m.id !== messageId) return m;
    const objects = blocksOf(m);
    if (!objects.some((o) => o.kind === "question" && o.id === qid)) return m;
    changed = true;
    return { ...m, meta: { ...m.meta, objects: objects.map((o) => (o.kind === "question" && o.id === qid ? question : o)) } };
  });
  return changed ? out : messages;
}

/** True when the panel of a section shows any of the modules an event names. */
export function panelTouched(panels: string[] | undefined, modules: unknown): boolean {
  if (!panels?.length || !Array.isArray(modules)) return false;
  return modules.some((m) => panels.includes(String(m)));
}
