import { describe, expect, test } from "bun:test";
import type { ChatReply, Message } from "@diablo/hub";

import { Store } from "../src/store/store";
import { flushOutbox, turnMessages, verdict } from "../src/sync/outbox";
import { memorySql } from "./bun-sql";

const msg = (id: number, role: string, text: string, meta: Record<string, unknown> | null = null): Message => ({ id, role, text, ts: 1_700_000_000 + id, meta });

const reply = (userId: number, id: number, text = "ok"): ChatReply => ({ reply: text, id, userId, section: "diablo", objects: [], threads: [] });

describe("Store", () => {
  test("shows hub messages, then his queued lines, and hides superseded replies", () => {
    const s = new Store(memorySql());
    s.addMessages("diablo", [msg(1, "user", "hi"), msg(2, "assistant", "old answer", { superseded_by: 4 }), msg(4, "assistant", "new answer")]);
    s.enqueue("diablo", "later");
    const shown = s.messages("diablo");
    expect(shown.map((m) => m.text)).toEqual(["hi", "new answer", "later"]);
    expect(shown[2].state).toBe("queued");
  });

  test("a pulled user message with the client id clears the outbox line", () => {
    const s = new Store(memorySql());
    const line = s.enqueue("workout", "log 20 push-ups");
    expect(s.queuedCount()).toBe(1);
    s.addMessages("workout", [msg(10, "user", "log 20 push-ups", { client_id: line.clientId })]);
    expect(s.queuedCount()).toBe(0);
    expect(s.messages("workout").map((m) => m.key)).toEqual(["r10"]);
  });

  test("counts new messages once, and unread replies against what he read", () => {
    const s = new Store(memorySql());
    expect(s.addMessages("home", [msg(1, "user", "a"), msg(2, "assistant", "b")])).toBe(2);
    expect(s.addMessages("home", [msg(2, "assistant", "b edited")])).toBe(0);
    expect(s.messages("home")[1].text).toBe("b edited");
    expect(s.unread("home")).toBe(1);
    s.markRead("home");
    s.addMessages("home", [msg(3, "assistant", "c")]);
    expect(s.unread("home")).toBe(1);
  });

  test("the cursor only moves forward", () => {
    const s = new Store(memorySql());
    expect(s.cursor("diablo")).toBeNull();
    s.setCursor("diablo", 12);
    s.setCursor("diablo", 9);
    expect(s.cursor("diablo")).toBe(12);
  });

  test("patches a question block inside a stored reply", () => {
    const s = new Store(memorySql());
    s.addMessages("diablo", [msg(5, "assistant", "pick one", { objects: [{ kind: "question", id: "q1234abcd", state: "pending" }, { kind: "notice", text: "x" }] })]);
    expect(s.patchQuestion({ kind: "question", id: "q1234abcd", state: "answered" })).toBe(true);
    const objects = s.messages("diablo")[0].meta?.objects as { state?: string }[];
    expect(objects[0].state).toBe("answered");
    expect(objects[1]).toEqual({ kind: "notice", text: "x" });
  });

  test("sections and panels survive as the hub sent them", () => {
    const s = new Store(memorySql());
    s.saveSections([
      { id: "diablo", name: "Diablo", icon: "flame", accent: "#ff4d2e", brief: "", position: 0, hidden: false, deleted: false, updated: 1 },
      { id: "workout", name: "Workout", icon: "dumbbell", accent: "#19b39a", brief: "sets", position: 1, hidden: false, deleted: false, updated: 1, panels: ["workout"] },
    ]);
    expect(s.sections().map((x) => x.id)).toEqual(["diablo", "workout"]);
    s.savePanel("workout", [{ kind: "goal", value: 3, max: 5 }], 100);
    expect(s.panel("workout")).toEqual({ blocks: [{ kind: "goal", value: 3, max: 5 }], generated: 100 });
  });
});

describe("outbox", () => {
  test("what a failed send means", () => {
    expect(verdict(0)).toBe("keep");
    expect(verdict(409)).toBe("keep");
    expect(verdict(503)).toBe("keep");
    expect(verdict(403)).toBe("unpaired");
    expect(verdict(400)).toBe("fail");
    expect(verdict(502)).toBe("fail");
  });

  test("sends in the order he wrote, across sections, and keeps the hub's ids", async () => {
    const s = new Store(memorySql());
    s.enqueue("diablo", "one");
    s.enqueue("workout", "two");
    s.enqueue("diablo", "three");
    const order: string[] = [];
    let id = 100;
    const out = await flushOutbox(s, async (line) => {
      order.push(line.text);
      id += 2;
      return reply(id - 1, id, `re: ${line.text}`);
    });
    expect(out).toEqual({ sent: 3, failed: 0, stopped: "empty" });
    expect(order).toEqual(["one", "two", "three"]);
    expect(s.messages("diablo").map((m) => `${m.id}:${m.text}`)).toEqual(["101:one", "102:re: one", "105:three", "106:re: three"]);
    expect(s.queuedCount()).toBe(0);
  });

  test("no hub stops the run and keeps every line queued, in order", async () => {
    const s = new Store(memorySql());
    s.enqueue("diablo", "one");
    s.enqueue("diablo", "two");
    const out = await flushOutbox(s, async () => {
      throw Object.assign(new Error("cannot reach the hub"), { status: 0 });
    });
    expect(out.stopped).toBe("keep");
    expect(s.outbox().map((o) => [o.text, o.state])).toEqual([
      ["one", "queued"],
      ["two", "queued"],
    ]);
  });

  test("a refused line is marked and the next one still goes; retry queues it again", async () => {
    const s = new Store(memorySql());
    const bad = s.enqueue("diablo", "bad");
    s.enqueue("diablo", "good");
    const out = await flushOutbox(s, async (line) => {
      if (line.text === "bad") throw Object.assign(new Error("no such section"), { status: 404 });
      return reply(7, 8);
    });
    expect(out).toEqual({ sent: 1, failed: 1, stopped: "empty" });
    expect(s.outbox()).toEqual([expect.objectContaining({ clientId: bad.clientId, state: "failed", error: "no such section" })]);
    s.retry(bad.clientId);
    expect(s.nextQueued()?.clientId).toBe(bad.clientId);
  });

  test("a turn's two messages carry the client id", () => {
    const s = new Store(memorySql());
    const line = s.enqueue("home", "lamp off");
    const [user, answer] = turnMessages(line, reply(20, 21, "Off."));
    expect(user).toEqual(expect.objectContaining({ id: 20, role: "user", meta: { section: "home", client_id: line.clientId } }));
    expect(answer).toEqual(expect.objectContaining({ id: 21, role: "assistant", text: "Off." }));
  });
});
