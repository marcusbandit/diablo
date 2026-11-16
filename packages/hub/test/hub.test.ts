import { describe, expect, test } from "bun:test";
import { Hub, HubError, normalizePairCode, parsePairLink, pickHub, sseParser } from "../src";

describe("sseParser", () => {
  test("joins lines split across chunks and skips keepalives", () => {
    const got: unknown[] = [];
    const feed = sseParser((e) => got.push(e));
    feed(': ping\n\ndata: {"kind":"mes');
    feed('sage","section":"diablo"}\r\n\ndata: not json\n');
    feed('data: {"kind":"sections"}\n');
    expect(got).toEqual([{ kind: "message", section: "diablo" }, { kind: "sections" }]);
  });
});

describe("pairing", () => {
  test("normalizes codes", () => {
    expect(normalizePairCode("abcd efgh")).toBe("ABCD-EFGH");
    expect(normalizePairCode("abc")).toBeNull();
  });
  test("reads app links and hub pages", () => {
    expect(parsePairLink("diablo://pair?host=http%3A%2F%2Fhub.example.com%3A8755&code=ABCDEFGH")).toEqual({
      host: "http://hub.example.com:8755",
      code: "ABCD-EFGH",
    });
    expect(parsePairLink("http://hub.example.com:8755/app/pair.html?code=ABCDEFGH")).toEqual({
      host: "http://hub.example.com:8755",
      code: "ABCD-EFGH",
    });
    expect(parsePairLink("ABCD-EFGH")).toEqual({ host: null, code: "ABCD-EFGH" });
  });
});

describe("Hub", () => {
  const fake = (handler: (url: string, init?: RequestInit) => Response) => (url: string, init?: RequestInit) =>
    Promise.resolve(handler(url, init));

  test("sends the token and reads the detail of an error", async () => {
    let seen: Record<string, string> = {};
    const hub = new Hub("http://hub.example.com", "t0k", {
      fetch: fake((_, init) => {
        seen = init?.headers as Record<string, string>;
        return new Response(JSON.stringify({ detail: "nope" }), { status: 403 });
      }),
    });
    const err = await hub.sections().catch((e) => e);
    expect(seen["X-Diablo-Token"]).toBe("t0k");
    expect(err).toBeInstanceOf(HubError);
    expect(err.status).toBe(403);
    expect(err.message).toBe("nope");
  });

  test("pickHub falls through to the standby", async () => {
    const hub = await pickHub(["http://a.example.com", "http://b.example.com"], null, {
      fetch: fake((url) => (url.startsWith("http://a") ? new Response("", { status: 503 }) : new Response("{}"))),
    });
    expect(hub?.base).toBe("http://b.example.com");
  });

  test("reads the event stream", async () => {
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode('data: {"kind":"turn","section":"diablo","state":"thinking"}\n\n'));
        c.close();
      },
    });
    const hub = new Hub("http://hub.example.com", "t", { fetch: fake(() => new Response(stream)) });
    const got: unknown[] = [];
    await hub.events((e) => got.push(e));
    expect(got).toEqual([{ kind: "turn", section: "diablo", state: "thinking" }]);
  });

  test("reads and writes a setting", async () => {
    const calls: { url: string; method?: string; body?: string }[] = [];
    const hub = new Hub("http://hub.example.com", "t", {
      fetch: fake((url, init) => {
        calls.push({ url, method: init?.method, body: init?.body as string | undefined });
        return new Response(init?.method === "GET" ? JSON.stringify({ key: "speaking_mode", value: "on" }) : '{"ok":true}');
      }),
    });
    expect(await hub.setting("speaking_mode")).toBe("on");
    await hub.putSetting("speaking_mode", "off");
    expect(calls[1]).toEqual({ url: "http://hub.example.com/v1/settings/speaking_mode", method: "PUT", body: '{"value":"off"}' });
  });

  test("answers a question with a client id and waits for the reply", async () => {
    let body = "";
    const hub = new Hub("http://hub.example.com", "t", {
      fetch: fake((_, init) => {
        body = init?.body as string;
        return new Response(JSON.stringify({ reply: "Booked", id: 9 }));
      }),
    });
    expect(await hub.answerAndWait("q1234abcd", { choices: [0] }, "web-1", "web")).toEqual({ reply: "Booked", id: 9 });
    expect(JSON.parse(body)).toEqual({ choices: [0], source: "web", client_id: "web-1" });
  });
});
