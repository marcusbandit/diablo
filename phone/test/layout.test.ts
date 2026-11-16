import { describe, expect, mock, test } from "bun:test";

mock.module("../modules/diablo-posture", () => ({ default: null }));
mock.module("react-native", () => ({ useWindowDimensions: () => ({ width: 0, height: 0 }) }));

const { bent, layoutFor } = await import("../src/posture");

const fold = (state: "flat" | "half", orientation: "vertical" | "horizontal", rect: { left: number; top: number; right: number; bottom: number }) => ({
  state,
  orientation,
  separating: state === "half",
  occlusion: "none" as const,
  ...rect,
});

describe("posture", () => {
  test("bent starts ten degrees off flat, and a shut phone is not bent", () => {
    const f = fold("flat", "vertical", { left: 425, top: 0, right: 425, bottom: 882 });
    expect(bent({ fold: f, angle: 180, hasHinge: true })).toBe(false);
    expect(bent({ fold: f, angle: 165, hasHinge: true })).toBe(true);
    expect(bent({ fold: f, angle: 10, hasHinge: true })).toBe(false);
    expect(bent({ fold: null, angle: 120, hasHinge: true })).toBe(false);
    expect(bent({ fold: fold("half", "vertical", { left: 425, top: 0, right: 425, bottom: 882 }), angle: null, hasHinge: false })).toBe(true);
  });

  test("a phone keeps the sections in a drawer", () => {
    const shut = layoutFor(411, 914, { fold: null, angle: 0, hasHinge: true }, false);
    expect(shut.mode).toBe("phone");
    expect(shut.drawer).toBe(true);
    expect(shut.pane.x).toBeLessThan(0);
    expect(shut.chat).toEqual({ x: 0, y: 0, width: 411, height: 914 });
    expect(layoutFor(411, 914, { fold: null, angle: 0, hasHinge: true }, true).pane.x).toBe(0);
  });

  test("flat and open puts the sections beside the chat", () => {
    const l = layoutFor(851, 882, { fold: fold("flat", "vertical", { left: 425, top: 0, right: 425, bottom: 882 }), angle: 180, hasHinge: true }, false);
    expect(l.mode).toBe("wide");
    expect(l.pane.x).toBe(0);
    expect(l.chat.x).toBe(l.pane.width);
    expect(l.chat.x + l.chat.width).toBe(851);
  });

  test("a book fold keeps both panes off the seam", () => {
    const l = layoutFor(851, 882, { fold: fold("half", "vertical", { left: 420, top: 0, right: 431, bottom: 882 }), angle: 120, hasHinge: true }, false);
    expect(l.mode).toBe("book");
    expect(l.pane.x + l.pane.width).toBeLessThanOrEqual(420);
    expect(l.chat.x).toBeGreaterThanOrEqual(431);
  });

  test("a tabletop fold puts the composer below the hinge", () => {
    const l = layoutFor(882, 851, { fold: fold("half", "horizontal", { left: 0, top: 420, right: 882, bottom: 431 }), angle: 110, hasHinge: true }, false);
    expect(l.mode).toBe("tabletop");
    expect(l.seam).toEqual({ top: 420, bottom: 431 });
    expect(l.pane.y).toBeGreaterThanOrEqual(431);
    expect(l.controlsLeft).toBe(l.pane.width);
  });
});
