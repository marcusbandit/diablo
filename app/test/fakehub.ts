// A hub that answers the calls the engine makes, in memory, on a free local port. Every value in
// it is invented.
import type { Server } from "bun";

import type { BusyTurn, Message, Question, Section, Task } from "../src/engine/wire";

export const CODE = "ABCD-EFGH";

export const section = (id: string, name: string, position: number): Section => ({
  id,
  name,
  icon: "flame",
  accent: "#ff4d2e",
  brief: "",
  position,
  hidden: false,
  deleted: false,
  updated: 0,
});

export class FakeHub {
  server: Server<undefined>;
  /** Answers 502 to everything, and closes streams, while true. */
  down = false;
  /** The next chat calls answer with these statuses, one each, instead of running. */
  chatFails: number[] = [];
  tokens = new Set<string>(["tok-known"]);
  sections: Section[] = [section("diablo", "Diablo", 0), section("workout", "Workout", 1)];
  messages: (Message & { section: string })[] = [];
  questions: Question[] = [];
  tasks: Task[] = [];
  busy: BusyTurn[] = [];
  /** Every line chat received, in order, with its client id. */
  received: { text: string; clientId: string }[] = [];
  revoked: number[] = [];
  /** Sections whose messages answer 404, like one deleted between two calls. */
  broken = new Set<string>();
  private nextId = 1;
  private nextDevice = 1;
  private streams = new Set<ReadableStreamDefaultController<Uint8Array>>();
  private seen = new Map<string, { id: number; user_id: number; reply: string }>();

  constructor() {
    this.server = Bun.serve({ port: 0, idleTimeout: 0, fetch: (req) => this.handle(req) });
  }

  get base() {
    return `http://127.0.0.1:${this.server.port}`;
  }

  stop() {
    this.closeStreams();
    this.server.stop(true);
  }

  /** Stores a message as the hub would, and tells the stream. */
  say(text: string, sectionId = "diablo", role = "assistant", meta: Record<string, unknown> = {}): Message {
    const m = { id: this.nextId++, role, text, ts: Date.now() / 1000, meta: { section: sectionId, ...meta }, section: sectionId };
    this.messages.push(m);
    return m;
  }

  push(event: Record<string, unknown>) {
    const data = new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`);
    for (const c of this.streams) c.enqueue(data);
  }

  closeStreams() {
    for (const c of this.streams) {
      try {
        c.close();
      } catch {
        // already closed
      }
    }
    this.streams.clear();
  }

  get streaming() {
    return this.streams.size;
  }

  private json(body: unknown, status = 200) {
    return Response.json(body, { status });
  }

  private async handle(req: Request): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname;
    if (this.down) return new Response("down", { status: 502 });
    if (path === "/v1/health") return this.json({ ok: true, role: "pi" });
    if (path === "/v1/pair" && req.method === "POST") {
      const body = (await req.json()) as { code: string; name: string };
      if (body.code !== CODE) return this.json({ detail: "that code is wrong, used or expired" }, 403);
      const token = `tok-${this.nextDevice}`;
      this.tokens.add(token);
      return this.json({ token, device: { id: this.nextDevice++, name: body.name }, hub: "fake" });
    }
    const token = req.headers.get("X-Diablo-Token") ?? url.searchParams.get("token");
    if (!token || !this.tokens.has(token)) return this.json({ detail: "forbidden" }, 403);

    if (path === "/v1/events") {
      let held: ReadableStreamDefaultController<Uint8Array>;
      const stream = new ReadableStream<Uint8Array>({
        start: (c) => {
          held = c;
          this.streams.add(c);
          c.enqueue(new TextEncoder().encode("retry: 3000\n\n"));
        },
        cancel: () => void this.streams.delete(held),
      });
      return new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
    }
    if (path === "/v1/sections") return this.json({ sections: this.sections });
    const msgs = path.match(/^\/v1\/sections\/([^/]+)\/messages$/);
    if (msgs) {
      const id = decodeURIComponent(msgs[1]);
      if (this.broken.has(id)) return this.json({ detail: "no such section" }, 404);
      const after = Number(url.searchParams.get("after") ?? 0);
      const before = Number(url.searchParams.get("before") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 100);
      let list = this.messages.filter((m) => m.section === id && m.id > after && (before <= 0 || m.id < before));
      list = after > 0 ? list.slice(0, limit) : list.slice(-limit);
      return this.json({ messages: list.map(({ section: _s, ...m }) => m) });
    }
    if (path === "/v1/chat" && req.method === "POST") {
      const body = (await req.json()) as { text: string; section: string; client_id: string };
      const fail = this.chatFails.shift();
      if (fail) return this.json({ detail: `failed with ${fail}` }, fail);
      const again = this.seen.get(body.client_id);
      if (again) return this.json({ ...again, section: body.section, objects: [] });
      this.received.push({ text: body.text, clientId: body.client_id });
      const user = this.say(body.text, body.section, "user", { client_id: body.client_id });
      const reply = this.say(`Heard: ${body.text}`, body.section);
      const out = { id: reply.id, user_id: user.id, reply: reply.text };
      this.seen.set(body.client_id, out);
      return this.json({ ...out, section: body.section, objects: [] });
    }
    if (path === "/v1/questions") return this.json({ questions: this.questions.filter((q) => q.state === "pending") });
    const answer = path.match(/^\/v1\/questions\/([^/]+)\/answer$/);
    if (answer) {
      const q = this.questions.find((x) => x.id === answer[1]);
      if (!q) return this.json({ detail: "no such question" }, 404);
      if (q.state !== "pending") return this.json({ detail: "already answered" }, 409);
      q.state = "answered";
      const reply = this.say("Noted.", q.section ?? "diablo");
      return this.json({ reply: reply.text, id: reply.id, user_id: 0, section: q.section, objects: [] });
    }
    if (path === "/v1/tasks") return this.json({ tasks: this.tasks });
    if (path === "/v1/busy") return this.json({ kind: "busy", turns: this.busy, at: Date.now() / 1000 });
    if (path === "/v1/facts") return this.json({ facts: [{ id: 1, ts: 1, text: "Alex Example likes tea.", tags: [] }] });
    const device = path.match(/^\/v1\/devices\/(\d+)$/);
    if (device && req.method === "DELETE") {
      this.revoked.push(Number(device[1]));
      return this.json({ ok: true });
    }
    return this.json({ detail: "not found" }, 404);
  }
}

export function question(id: string, extra: Partial<Question> = {}): Question {
  return {
    kind: "question",
    id,
    header: "Lamp",
    question: "Dim the desk lamp?",
    options: [{ label: "Yes" }, { label: "No" }],
    recommended: 0,
    multi: false,
    allow_text: false,
    state: "pending",
    section: "diablo",
    message: null,
    conv: "main",
    ...extra,
  };
}

export function task(id: string, state: Task["state"]): Task {
  return {
    id,
    title: "Fix the example",
    kind: "partner",
    machine: "box",
    state,
    step: "",
    started_at: 1,
    finished_at: null,
    seen_at: null,
    updated_at: 1,
    links: { pr: "", pr_number: null, pr_state: "", issue: "", thread: "" },
    result: "",
    source: "tool",
  };
}

/** Waits until `check` holds, or fails after `ms`. */
export async function until(check: () => boolean, ms = 3000): Promise<void> {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error("timed out waiting");
    await new Promise((r) => setTimeout(r, 10));
  }
}
