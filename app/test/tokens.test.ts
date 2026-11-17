import { describe, expect, test } from "bun:test";

import { mood } from "../src/mood";
import { color, state } from "../src/tokens";

function luminance(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("colours read", () => {
  test("body text and secondary text pass AA on the background", () => {
    expect(contrast(color.text, color.bg)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(color.sub, color.bg)).toBeGreaterThanOrEqual(4.5);
  });

  test("the quietest text stays readable as large or incidental text", () => {
    expect(contrast(color.dim, color.bg)).toBeGreaterThanOrEqual(3);
  });

  test("the block's ink passes AA on both fills", () => {
    expect(contrast(color.calmInk, color.calm)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(color.attentionInk, color.attention)).toBeGreaterThanOrEqual(4.5);
  });

  test("every working line shows against the calm block", () => {
    for (const c of [state.listening, state.thinking, state.speaking, state.trouble]) expect(contrast(c, color.calm)).toBeGreaterThanOrEqual(3);
  });
});

describe("mood", () => {
  test("the fill means attention, not activity", () => {
    expect(mood("thinking", 0).fill).toBe(color.calm);
    expect(mood("speaking", 0).fill).toBe(color.calm);
    expect(mood("resting", 1).fill).toBe(color.attention);
  });

  test("activity shows in the face and the line", () => {
    expect(mood("speaking", 0).face).toBe("talking");
    expect(mood("speaking", 0).line).toBe(state.speaking);
    expect(mood("resting", 0).line).toBeNull();
  });

  test("the headline counts what needs you", () => {
    expect(mood("resting", 0).headline).toBe("All quiet, sir.");
    expect(mood("resting", 1).headline).toBe("One thing needs you, sir.");
    expect(mood("resting", 2).headline).toBe("Two things need you, sir.");
    expect(mood("resting", 9).headline).toBe("9 things need you, sir.");
  });
});
