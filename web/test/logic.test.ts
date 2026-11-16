import { describe, expect, test } from "bun:test";
import type { Message } from "@diablo/hub";
import tokens from "../../design/tokens.json";
import { barLayout } from "../src/blocks/bars";
import { diffLines, sandboxDoc } from "../src/blocks/pure";
import { deviceLabel, takeTokenFromQuery } from "../src/lib/auth";
import { ago, countdown } from "../src/lib/format";
import { blocksOf, mergeMessages, newClientId, panelTouched, patchQuestion, shown, stepsOf } from "../src/lib/messages";
import { parseRoute, routeHash } from "../src/lib/route";
import { tokensCss, type TokenFile } from "../src/lib/tokens";
import { applyTurnEvent, turnWords } from "../src/views/turns";

const msg = (id: number, meta: Message["meta"] = null, text = `m${id}`): Message => ({ id, role: "assistant", text, ts: id, meta });

describe("token bootstrap", () => {
  test("takes ?token= and strips it from the query", () => {
    expect(takeTokenFromQuery("?token=abc&x=1", null)).toEqual({ token: "abc", search: "?x=1" });
    expect(takeTokenFromQuery("?token=abc", null)).toEqual({ token: "abc", search: "" });
  });
  test("a stored token wins over a link's", () => {
    expect(takeTokenFromQuery("?token=other", "mine")).toEqual({ token: "mine", search: "" });
    expect(takeTokenFromQuery("", null)).toEqual({ token: null, search: "" });
  });
  test("names the browser like pair.html does", () => {
    expect(deviceLabel("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")).toBe("Firefox on Linux");
    expect(deviceLabel("Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36")).toBe("Chrome on Android");
  });
});

describe("routes", () => {
  test("round trip", () => {
    for (const r of [{ view: "devices" }, { view: "intro" }, { view: "section", id: "home" }] as const) {
      expect(parseRoute(routeHash(r))).toEqual(r);
    }
  });
  test("anything else is the main section", () => {
    expect(parseRoute("")).toEqual({ view: "section", id: "diablo" });
    expect(parseRoute("#/nowhere")).toEqual({ view: "section", id: "diablo" });
  });
});

describe("messages", () => {
  test("merge keeps each id once, oldest first, newer copy wins", () => {
    const merged = mergeMessages([msg(3), msg(1)], [msg(2), msg(3, null, "new")]);
    expect(merged.map((m) => m.id)).toEqual([1, 2, 3]);
    expect(merged[2].text).toBe("new");
  });
  test("superseded messages are hidden", () => {
    expect(shown([msg(1, { superseded_by: 2 }), msg(2)]).map((m) => m.id)).toEqual([2]);
  });
  test("blocks and steps come from meta and skip junk", () => {
    const m = msg(1, { objects: [{ kind: "list" }, null, { nokind: 1 }, "x"], steps: [{ step: "hue_light: desk", tool: "lights" }] });
    expect(blocksOf(m)).toEqual([{ kind: "list" }]);
    expect(stepsOf(m)).toEqual([{ step: "hue_light: desk", tool: "lights" }]);
    expect(blocksOf(msg(2))).toEqual([]);
  });
  test("a question event patches its block in place", () => {
    const have = [msg(1), msg(2, { objects: [{ kind: "question", id: "q1", state: "pending" }] })];
    const out = patchQuestion(have, 2, { kind: "question", id: "q1", state: "answered" });
    expect(blocksOf(out[1])[0].state).toBe("answered");
    expect(out[0]).toBe(have[0]);
    expect(patchQuestion(have, 2, { kind: "question", id: "other" })).toBe(have);
  });
  test("panel events touch only sections that show the module", () => {
    expect(panelTouched(["lights", "network"], ["lights", "today"])).toBe(true);
    expect(panelTouched(["workout"], ["lights"])).toBe(false);
    expect(panelTouched(undefined, ["lights"])).toBe(false);
  });
  test("client ids are distinct", () => {
    expect(newClientId(1, 0.1)).not.toBe(newClientId(1, 0.2));
    expect(newClientId(1, 0.1)).toStartWith("web-");
  });
});

describe("turns", () => {
  test("thinking, steps, then done", () => {
    let t = applyTurnEvent({}, { kind: "turn", section: "home", state: "thinking" }, 10);
    expect(turnWords(t.home)).toBe("Thinking");
    t = applyTurnEvent(t, { kind: "turn", section: "home", state: "doing", step: "hue_light: desk", tool: "lights" }, 11);
    expect(t.home.steps).toEqual([{ step: "hue_light: desk", tool: "lights" }]);
    expect(t.home.since).toBe(10);
    expect(turnWords(t.home)).toBe("Using lights");
    t = applyTurnEvent(t, { kind: "turn", section: "home", state: "done", id: 5 }, 12);
    expect(t).toEqual({});
  });
  test("other events change nothing", () => {
    const t = {};
    expect(applyTurnEvent(t, { kind: "message", section: "home" }, 1)).toBe(t);
  });
});

describe("format", () => {
  test("countdown and ago", () => {
    expect(countdown(605)).toBe("10:05");
    expect(countdown(-3)).toBe("0:00");
    expect(ago(100, 130)).toBe("now");
    expect(ago(0, 7200)).toBe("2 h ago");
    expect(ago(0, 86400 * 1.5)).toBe("yesterday");
  });
});

describe("blocks", () => {
  test("stacked bars pile up, grouped ones stand from the floor", () => {
    const stacked = barLayout(2, [[1, 3], [1, 1]], true);
    expect(stacked.max).toBe(4);
    expect(stacked.columns[1]).toEqual([{ bottom: 0, height: 0.75 }, { bottom: 0.75, height: 0.25 }]);
    const grouped = barLayout(2, [[1, 3], [2, 1]], false);
    expect(grouped.max).toBe(3);
    expect(grouped.columns[0]).toEqual([{ bottom: 0, height: 1 / 3 }, { bottom: 0, height: 2 / 3 }]);
    expect(barLayout(2, [[0, 0]], true).max).toBe(0);
  });
  test("diff lines are told apart", () => {
    expect(diffLines("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n same\n").map((l) => l.kind)).toEqual(["meta", "meta", "hunk", "del", "add", "ctx"]);
  });
  test("an html page gets the theme and a policy that forbids scripts", () => {
    const style = { getPropertyValue: (n: string) => (n === "--bg" ? "#000" : "") };
    const fragment = sandboxDoc("<p>hi</p>", style);
    expect(fragment).toContain("--bg:#000;");
    expect(fragment).toContain("default-src 'none'");
    expect(fragment).toContain("<body><p>hi</p></body>");
    const full = sandboxDoc("<html><head><title>x</title></head><body>b</body></html>", style);
    expect(full.indexOf("Content-Security-Policy")).toBeLessThan(full.indexOf("<title>"));
  });
});

describe("tokens", () => {
  test("dark on :root, light under the media query and data-theme", () => {
    const css = tokensCss(tokens as unknown as TokenFile);
    expect(css).toContain("--bg: #1e2024;");
    expect(css).toContain("--bg: #f4f3ef;");
    expect(css).toContain("@media (prefers-color-scheme: light)");
    expect(css).toContain(':root[data-theme="light"]');
    expect(css).toContain("--reading-width: 804px;");
    expect(css).toContain("--fail: #f4708f;");
    expect(css).not.toContain("--reading: 804px");
  });
});
