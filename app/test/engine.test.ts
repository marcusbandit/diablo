import { afterEach, describe, expect, test } from "bun:test";

import { doing, needsYou } from "../src/engine/derive";
import { memoryDisk, memorySecrets, type Disk, type Secrets } from "../src/engine/disk";
import { Engine, MAIN } from "../src/engine/engine";
import { sse } from "../src/engine/hub";
import { verdict } from "../src/engine/outbox";
import { readPairing } from "../src/engine/pair";
import type { HubEvent } from "../src/engine/wire";
import { mood } from "../src/mood";
import { CODE, FakeHub, question, task, until } from "./fakehub";

const hubs: FakeHub[] = [];
const engines: Engine[] = [];

afterEach(async () => {
  await Promise.all(engines.splice(0).map((e) => e.stop()));
  hubs.splice(0).forEach((h) => h.stop());
});

function fakeHub(): FakeHub {
  const hub = new FakeHub();
  hubs.push(hub);
  return hub;
}

function engine(bases: string[], opts: { disk?: Disk; secrets?: Secrets } = {}): Engine {
  const e = new Engine({
    disk: opts.disk ?? memoryDisk(),
    secrets: opts.secrets ?? memorySecrets("tok-known"),
    fetch: (input, init) => fetch(input, init),
    hubs: bases,
    deviceName: "Test phone",
    source: "phone",
    pollMs: 60_000,
    retryMs: 40,
    streamRetryMs: 40,
    pingS: 5,
    speakMs: 60,
  });
  engines.push(e);
  return e;
}

const texts = (e: Engine, section = MAIN) => (e.state.conversations[section] ?? []).map((l) => `${l.state}:${l.text}`);

describe("the outbox", () => {
  test("a line written offline waits, then goes once the hub is back", async () => {
    const hub = fakeHub();
    hub.down = true;
    const e = engine([hub.base]);
    await e.start();
    await until(() => e.state.link === "offline");
    await e.send("Dim the lights");
    expect(texts(e)).toEqual(["queued:Dim the lights"]);
    hub.down = false;
    await until(() => e.state.link === "online" && texts(e).length === 2);
    expect(texts(e)).toEqual(["sent:Dim the lights", "sent:Heard: Dim the lights"]);
    expect(hub.received.map((r) => r.text)).toEqual(["Dim the lights"]);
  });

  test("lines leave in the order he wrote them", async () => {
    const hub = fakeHub();
    hub.down = true;
    const e = engine([hub.base]);
    await e.start();
    await until(() => e.state.link === "offline");
    for (const line of ["one", "two", "three"]) await e.send(line);
    hub.down = false;
    await until(() => hub.received.length === 3);
    expect(hub.received.map((r) => r.text)).toEqual(["one", "two", "three"]);
  });

  test("a line the hub is still running waits and is sent again with the same client id", async () => {
    const hub = fakeHub();
    hub.chatFails = [409];
    const e = engine([hub.base]);
    await e.start();
    await until(() => e.state.link === "online");
    await e.send("still going");
    await until(() => hub.chatFails.length === 0);
    expect(texts(e)).toEqual(["queued:still going"]);
    e.kick();
    await until(() => hub.received.length === 1);
    await until(() => texts(e).length === 2);
    expect(texts(e)[0]).toBe("sent:still going");
  });

  test("a line the hub refuses for good is marked failed and the next one goes", async () => {
    const hub = fakeHub();
    hub.down = true;
    const e = engine([hub.base]);
    await e.start();
    await until(() => e.state.link === "offline");
    await e.send("bad");
    await e.send("good");
    hub.chatFails = [400];
    hub.down = false;
    await until(() => hub.received.length === 1);
    await until(() => texts(e).length === 3);
    expect(texts(e)).toEqual(["sent:good", "sent:Heard: good", "failed:bad"]);
    const failed = e.state.conversations[MAIN].find((l) => l.state === "failed")!;
    await e.retry(failed.clientId!);
    await until(() => hub.received.length === 2);
    expect(hub.received[1].text).toBe("bad");
  });

  test("the outbox survives a restart", async () => {
    const hub = fakeHub();
    hub.down = true;
    const disk = memoryDisk();
    const first = engine([hub.base], { disk });
    await first.start();
    await first.send("before the crash");
    await first.stop();
    const second = engine([hub.base], { disk });
    await second.start();
    expect(texts(second)).toEqual(["queued:before the crash"]);
    hub.down = false;
    await until(() => hub.received.length === 1);
  });

  test("statuses decide what a failed send means", () => {
    expect([0, 409, 429, 503].map(verdict)).toEqual(["wait", "wait", "wait", "wait"]);
    expect([401, 403].map(verdict)).toEqual(["unpaired", "unpaired"]);
    expect([400, 404, 500].map(verdict)).toEqual(["fail", "fail", "fail"]);
  });
});

describe("the link", () => {
  test("the standby takes over while the Pi does not answer", async () => {
    const pi = fakeHub();
    const standby = fakeHub();
    pi.down = true;
    const e = engine([pi.base, standby.base]);
    await e.start();
    await until(() => e.state.link === "online");
    expect(e.state.hub).toBe(standby.base);
  });

  test("a hub that stopped knowing this device is trouble", async () => {
    const hub = fakeHub();
    const e = engine([hub.base], { secrets: memorySecrets("tok-forgotten") });
    await e.start();
    await until(() => e.state.link === "refused");
    expect(doing(e.state)).toBe("trouble");
    expect(mood(doing(e.state), needsYou(e.state), e.state.link).headline).toBe("The hub stopped knowing this phone, sir.");
  });

  test("no token is unpaired, and a pairing link pairs", async () => {
    const hub = fakeHub();
    const secrets = memorySecrets(null);
    const e = engine([hub.base], { secrets });
    await e.start();
    await until(() => e.state.link === "unpaired");
    await e.pairFrom(`diablo-next://pair?host=${encodeURIComponent(hub.base)}&code=abcd-efgh`);
    await until(() => e.state.link === "online");
    expect(await secrets.get()).toBe("tok-1");
    expect(e.state.device).toEqual({ id: 1, name: "Test phone", hub: hub.base });
  });

  test("a link to a hub the app does not know waits for a yes", async () => {
    const hub = fakeHub();
    const e = engine([], { secrets: memorySecrets(null) });
    await e.start();
    await e.pairFrom(`diablo-next://pair?host=${encodeURIComponent(hub.base)}&code=${CODE}`);
    expect(e.state.pairing.confirm).toEqual({ host: hub.base, code: CODE });
    expect(e.state.device).toBeNull();
    await e.confirmHost(true);
    await until(() => e.state.link === "online");
    expect(e.state.hub).toBe(hub.base);
  });

  test("a wrong code says so", async () => {
    const hub = fakeHub();
    const e = engine([hub.base], { secrets: memorySecrets(null) });
    await e.start();
    await e.pairFrom("WXYZ-WXYZ");
    expect(e.state.pairing.error).toContain("does not know that code");
  });

  test("a dropped stream comes back", async () => {
    const hub = fakeHub();
    const e = engine([hub.base]);
    await e.start();
    await until(() => hub.streaming === 1);
    hub.closeStreams();
    await until(() => hub.streaming === 1);
  });
});

describe("the event stream", () => {
  test("a new message is fetched and his face shows he is talking", async () => {
    const hub = fakeHub();
    hub.say("Earlier.");
    const e = engine([hub.base]);
    await e.start();
    await until(() => hub.streaming === 1 && texts(e).length === 1);
    expect(doing(e.state)).toBe("resting");
    const m = hub.say("The lamp is off.");
    hub.push({ kind: "message", section: MAIN, id: m.id });
    await until(() => texts(e).length === 2);
    expect(doing(e.state)).toBe("speaking");
    await until(() => !e.state.speaking);
  });

  test("a turn anywhere shows as thinking", async () => {
    const hub = fakeHub();
    const e = engine([hub.base]);
    await e.start();
    await until(() => hub.streaming === 1);
    hub.push({ kind: "busy", turns: [{ id: 1, conv: "telegram", origin: "telegram", since: 1, words: "thinking", step: "" }], at: 1 });
    await until(() => doing(e.state) === "thinking");
    hub.push({ kind: "busy", turns: [], at: 2 });
    await until(() => doing(e.state) === "resting");
  });

  test("questions and waiting work count as needing him", async () => {
    const hub = fakeHub();
    hub.questions = [question("q00000001")];
    const e = engine([hub.base]);
    await e.start();
    await until(() => hub.streaming === 1);
    expect(needsYou(e.state)).toBe(1);
    hub.push({ kind: "task", change: "updated", task: task("task-1", "waiting") });
    await until(() => needsYou(e.state) === 2);
    expect(mood("resting", needsYou(e.state)).headline).toBe("Two things need you, sir.");
    hub.push({ kind: "question", question: question("q00000001", { state: "answered" }) });
    await until(() => needsYou(e.state) === 1);
  });

  test("a question from the old web chat is not one he can answer here", async () => {
    const hub = fakeHub();
    hub.questions = [question("q00000002", { conv: "web-abc" })];
    const e = engine([hub.base]);
    await e.start();
    await until(() => e.state.link === "online");
    expect(needsYou(e.state)).toBe(0);
  });

  test("answering takes the question away and fetches the reply", async () => {
    const hub = fakeHub();
    hub.questions = [question("q00000003")];
    const e = engine([hub.base]);
    await e.start();
    await until(() => e.state.link === "online");
    const q = e.state.questions[0];
    expect(await e.answer(q, { choices: [0] })).toBeNull();
    expect(needsYou(e.state)).toBe(0);
    expect(texts(e)).toEqual(["sent:Noted."]);
  });
});

describe("reading the wire", () => {
  test("events split anywhere across chunks", () => {
    const got: HubEvent[] = [];
    const feed = sse((e) => got.push(e));
    feed('retry: 3000\n\ndata: {"kind":"mes');
    feed('sage","section":"diablo"}\n\n: ping\n\ndata: not json\n');
    feed('data: {"kind":"sections"}\r\n');
    expect(got).toEqual([{ kind: "message", section: "diablo" }, { kind: "sections" }]);
  });

  test("pairing links, the hub's page and typed codes", () => {
    expect(readPairing("abcdefgh")).toEqual({ host: null, code: "ABCD-EFGH" });
    expect(readPairing("diablo-next://pair?code=ABCD-EFGH&host=http://198.51.100.7:8755/")).toEqual({ host: "http://198.51.100.7:8755", code: "ABCD-EFGH" });
    expect(readPairing("diablo://pair?code=ABCD-EFGH")).toEqual({ host: null, code: "ABCD-EFGH" });
    expect(readPairing("https://hub.example.com/app/pair.html?code=ABCD-EFGH")).toEqual({ host: "https://hub.example.com", code: "ABCD-EFGH" });
    expect(readPairing("diablo-next://pair?code=ABCD-EFGH&host=javascript:alert(1)")).toBeNull();
    expect(readPairing("ftp://example.com/?code=ABCD-EFGH")).toBeNull();
    expect(readPairing("hello")).toBeNull();
  });
});

describe("the headline and the link", () => {
  test("offline with nothing waiting says the hub is gone", () => {
    expect(mood("resting", 0, "offline").headline).toBe("I can't reach the hub, sir.");
  });

  test("offline, what needs him still outranks the link", () => {
    expect(mood("resting", 1, "offline").headline).toBe("One thing needs you, sir.");
  });

  test("unpaired, the block stays calm whatever it held", () => {
    const m = mood("resting", 2, "unpaired");
    expect(m.headline).toBe("We haven't met on this phone, sir.");
    expect(m.fill).not.toBe(mood("resting", 2).fill);
  });
});
