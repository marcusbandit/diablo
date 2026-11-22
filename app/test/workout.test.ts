import { afterEach, describe, expect, test } from "bun:test";

import { Engine } from "../src/engine/engine";
import { memoryDisk, memorySecrets } from "../src/engine/disk";
import { amount, type Exercise, logAct, readLogger, searchExercises } from "../src/workout";
import { FakeHub, until } from "./fakehub";
import library from "./exercises.json";

const all = library as Exercise[];
const names = (q: string) => searchExercises(all, q).map((e) => e.name);

describe("exercise search", () => {
  test("a whole name comes first, however it is typed", () => {
    expect(names("pushup")[0]).toBe("Push-ups");
    expect(names("Push-ups")[0]).toBe("Push-ups");
    expect(names("rows")[0]).toBe("Body rows");
    expect(names("pul")[0]).toBe("Pull-ups");
  });

  test("the start of a name or of any word finds it", () => {
    expect(names("bulg")[0]).toBe("Bulgarian split squats");
    expect(names("dead")).toContain("Romanian deadlifts");
    expect(names("dead")).toContain("Dead hang");
  });

  test("slips in spelling are forgiven", () => {
    expect(names("pushpus")).toContain("Push-ups");
    expect(names("burpes")).toContain("Burpees");
  });

  test("nonsense and nothing find nothing", () => {
    expect(names("zzqx")).toEqual([]);
    expect(names("")).toEqual([]);
  });

  test("every name in the list is its own top hit", () => {
    for (const e of all) expect(names(e.name)[0]).toBe(e.name);
  });
});

describe("logging", () => {
  test("one set is a set, more is a list of reps", () => {
    expect(logAct("squats", 1, 12)).toEqual({ module: "workout", action: "set", args: { exercise: "squats", did: 12 } });
    expect(logAct("squats", 3, 10)).toEqual({ module: "workout", action: "sets", args: { exercise: "squats", reps: [10, 10, 10] } });
    expect(amount(45, true)).toBe("45 s");
  });

  test("the logger reads the hub's sets block and ignores a missing one", () => {
    const hub = new FakeHub();
    try {
      const logger = readLogger(hub.workoutPanel())!;
      expect(logger.setsToday).toBe(6);
      expect(logger.today.map((s) => s.name)).toEqual(["Body rows", "Squats"]);
      expect(logger.library.length).toBe(all.length);
      expect(logger.defaults.map((e) => e.id)).not.toContain("squats");
      expect(readLogger([{ kind: "goal" }])).toBeNull();
      expect(readLogger(null)).toBeNull();
    } finally {
      hub.stop();
    }
  });
});

describe("the room against a hub", () => {
  let hub: FakeHub | null = null;
  let engine: Engine | null = null;
  afterEach(async () => {
    await engine?.stop();
    hub?.stop();
  });

  test("three sets of push-ups open a session, one more joins it, done closes it", async () => {
    hub = new FakeHub();
    engine = new Engine({
      disk: memoryDisk(),
      secrets: memorySecrets("tok-known"),
      fetch: (input, init) => fetch(input, init),
      hubs: [hub.base],
      deviceName: "Test phone",
      source: "phone",
      pollMs: 60_000,
      retryMs: 40,
      streamRetryMs: 40,
      pingS: 5,
      speakMs: 60,
    });
    await engine.start();
    const e = engine;
    await until(() => e.state.link === "online");
    const first = await engine.act(logAct("push-ups", 3, 10), "workout");
    if ("error" in first) throw new Error(first.error);
    let logger = readLogger(first.panel)!;
    expect(logger.current?.name).toBe("Push-ups");
    expect(logger.current?.sets).toEqual([10, 10, 10]);
    const more = await engine.act(logAct("push-ups", 1, 8), "workout");
    if ("error" in more) throw new Error(more.error);
    logger = readLogger(more.panel)!;
    expect(logger.current?.sets).toEqual([10, 10, 10, 8]);
    expect(logger.recent[0].id).toBe("push-ups");
    const done = await engine.act({ module: "workout", action: "close", args: {} }, "workout");
    if ("error" in done) throw new Error(done.error);
    expect(readLogger(done.panel)!.current).toBeNull();
  });
});
