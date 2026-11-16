import { describe, expect, test } from "bun:test";

import type { Task } from "@diablo/hub";

import { headline, moodOf, shownTasks } from "../src/den";

const task = (id: string, state: Task["state"], extra: Partial<Task> = {}): Task => ({
  id,
  title: id,
  kind: "partner",
  machine: "",
  state,
  step: "",
  started_at: 0,
  finished_at: null,
  seen_at: null,
  updated_at: 1000,
  links: { pr: "", pr_number: null, pr_state: "", issue: "", thread: "" },
  result: "",
  source: "t3",
  ...extra,
});

const base = { link: "online" as const, turns: {}, tasks: [] as Task[], owed: [], moment: null, now: 2_000_000 };

describe("the den", () => {
  test("waiting work comes first, finished work stays until put away", () => {
    const now = 2000;
    const { active, finished } = shownTasks(
      [task("a", "working"), task("b", "waiting"), task("c", "done", { finished_at: 1900 }), task("d", "done", { finished_at: 1900, seen_at: 1950 })],
      now,
    );
    expect(active.map((t) => t.id)).toEqual(["b", "a"]);
    expect(finished.map((t) => t.id)).toEqual(["c"]);
  });

  test("his face follows what he does", () => {
    expect(moodOf(base)).toBe("resting");
    expect(moodOf({ ...base, link: "offline" })).toBe("asleep");
    expect(moodOf({ ...base, turns: { diablo: { state: "thinking", since: 0 } } })).toBe("thinking");
    expect(moodOf({ ...base, owed: [{ kind: "question", id: "q" }] })).toBe("expectant");
    expect(moodOf({ ...base, tasks: [task("a", "working")] })).toBe("proud");
    expect(moodOf({ ...base, moment: { face: "delighted", until: base.now + 10 } })).toBe("delighted");
    expect(moodOf({ ...base, moment: { face: "delighted", until: base.now - 10 } })).toBe("resting");
  });

  test("the line says what needs him first", () => {
    expect(headline(base)).toBe("All quiet. Nothing needs you.");
    expect(headline({ ...base, tasks: [task("a", "working"), task("b", "working")] })).toBe("Two jobs running. Nothing needs you.");
    expect(headline({ ...base, owed: [{ kind: "question", id: "q" }], tasks: [task("a", "working")] })).toBe("One thing needs you. One job running.");
    expect(headline({ ...base, link: "offline" })).toBe("I can't reach the hub. Anything you write waits here.");
  });
});
