import type { ChatReply, Message } from "@diablo/hub";

import { now, type Outgoing, type Store } from "../store/store";

/** What a failed send means for the line that was sent. */
export type Verdict = "keep" | "fail" | "unpaired";

/**
 * 0 is no answer at all, 409 is the same line still running on the hub, 503 a standby that
 * takes no turns: the line waits. 401 and 403 mean the hub no longer knows this phone. Anything
 * else the hub refused for good, so the line is marked and the next one goes.
 */
export function verdict(status: number): Verdict {
  if (status === 0 || status === 409 || status === 503 || status === 429) return "keep";
  if (status === 401 || status === 403) return "unpaired";
  return "fail";
}

/** The hub's two messages for a turn that answered, as the store keeps them. */
export function turnMessages(sent: Outgoing, reply: ChatReply): Message[] {
  const out: Message[] = [];
  if (reply.userId > 0) {
    out.push({ id: reply.userId, role: "user", text: sent.text, ts: sent.created, meta: { section: sent.section, client_id: sent.clientId } });
  }
  if (reply.id > 0) {
    out.push({ id: reply.id, role: "assistant", text: reply.reply, ts: now(), meta: { section: sent.section, client_id: sent.clientId, objects: reply.objects } });
  }
  return out;
}

export type FlushResult = { sent: number; failed: number; stopped: "empty" | "keep" | "unpaired" };

/**
 * Sends the outbox in the order he wrote it, one line at a time. A line the hub refused is
 * marked and the next goes; a line that could not reach the hub stops the run and waits.
 */
export async function flushOutbox(
  store: Store,
  send: (line: Outgoing) => Promise<ChatReply>,
  hooks: { started?: (line: Outgoing) => void; finished?: (line: Outgoing) => void } = {},
): Promise<FlushResult> {
  let sent = 0;
  let failed = 0;
  for (;;) {
    const next = store.nextQueued();
    if (!next) return { sent, failed, stopped: "empty" };
    hooks.started?.(next);
    try {
      const reply = await send(next);
      store.addMessages(next.section, turnMessages(next, reply));
      store.dropOutgoing(next.clientId);
      sent += 1;
    } catch (error) {
      const status = typeof (error as { status?: unknown }).status === "number" ? (error as { status: number }).status : 0;
      const v = verdict(status);
      if (v !== "fail") return { sent, failed, stopped: v };
      store.markFailed(next.clientId, error instanceof Error ? error.message : String(error));
      failed += 1;
    } finally {
      hooks.finished?.(next);
    }
  }
}
