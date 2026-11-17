import type { Outgoing, Store } from "./store";
import type { ChatReply, Message } from "./wire";

/** What a failed send means for the line: it waits, it failed for good, or the hub forgot us. */
export type Verdict = "wait" | "fail" | "unpaired";

/**
 * 0 is no answer at all, 409 the same line still running on the hub, 429 too many at once, 503
 * a standby that takes no turns: the line waits. 401 and 403 mean the hub no longer knows this
 * device. Anything else the hub refused for good.
 */
export function verdict(status: number): Verdict {
  if (status === 0 || status === 409 || status === 429 || status === 503) return "wait";
  if (status === 401 || status === 403) return "unpaired";
  return "fail";
}

/** The two messages a turn stored on the hub, as the store keeps them, so they show at once. */
export function turnMessages(line: Outgoing, reply: ChatReply, now = Date.now() / 1000): Message[] {
  const out: Message[] = [];
  const meta = { section: line.section, client_id: line.clientId };
  if (reply.user_id > 0) out.push({ id: reply.user_id, role: "user", text: line.text, ts: line.created, meta });
  if (reply.id > 0) out.push({ id: reply.id, role: "assistant", text: reply.reply, ts: now, meta: { ...meta, objects: reply.objects } });
  return out;
}

export type Flushed = { sent: number; failed: number; stopped: "empty" | "wait" | "unpaired" };

const status = (error: unknown) => {
  const s = (error as { status?: unknown } | null)?.status;
  return typeof s === "number" ? s : 0;
};

/**
 * Sends the outbox in the order he wrote it, one line at a time. A line the hub refused is
 * marked failed and the next one goes; a line that could not get through stops the run, and
 * it and everything after it wait for the next one.
 */
export async function flush(
  store: Store,
  send: (line: Outgoing) => Promise<ChatReply>,
  hooks: { started?: (line: Outgoing) => void; finished?: (line: Outgoing) => void } = {},
): Promise<Flushed> {
  let sent = 0;
  let failed = 0;
  for (;;) {
    const line = store.outbox.find((l) => l.state === "queued");
    if (!line) return { sent, failed, stopped: "empty" };
    hooks.started?.(line);
    try {
      const reply = await send(line);
      store.addMessages(line.section, turnMessages(line, reply));
      await store.drop(line.clientId);
      sent += 1;
    } catch (error) {
      const v = verdict(status(error));
      if (v !== "fail") return { sent, failed, stopped: v };
      await store.markFailed(line.clientId, error instanceof Error ? error.message : String(error));
      failed += 1;
    } finally {
      hooks.finished?.(line);
    }
  }
}
