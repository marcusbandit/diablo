import { describe, expect, test } from "bun:test";

import { inline, parseMarkdown } from "../src/md/parse";
import { plain } from "../src/md/plain";

describe("markdown", () => {
  test("reads the blocks he writes", () => {
    const md = [
      "# Plan",
      "",
      "Some **bold** and _italic_ with `code` and [a link](https://example.com).",
      "",
      "- one",
      "  - nested",
      "1. first",
      "- [x] done",
      "- [ ] open",
      "",
      "> quoted line",
      "",
      "| A | B |",
      "|---|--:|",
      "| 1 | 2 |",
      "",
      "```python",
      "print('hi')",
      "```",
      "",
      "---",
    ].join("\n");
    const blocks = parseMarkdown(md);
    expect(blocks.map((b) => b.type)).toEqual(["heading", "paragraph", "item", "item", "item", "item", "item", "paragraph", "table", "code", "rule"]);
    const items = blocks.filter((b) => b.type === "item");
    expect(items.map((b) => (b.type === "item" ? [b.depth, b.ordered, b.checked] : null))).toEqual([
      [0, false, null],
      [1, false, null],
      [0, true, null],
      [0, false, true],
      [0, false, false],
    ]);
    const quote = blocks[7];
    expect(quote.type === "paragraph" && quote.quote).toBe(1);
    const table = blocks[8];
    expect(table.type === "table" && table.align).toEqual(["none", "right"]);
    const code = blocks[9];
    expect(code.type === "code" && [code.lang, code.text]).toEqual(["python", "print('hi')"]);
  });

  test("inline styles, links and html", () => {
    expect(inline("**b** *i* ~~s~~ `c`")).toEqual([
      { text: "b", bold: true },
      { text: " " },
      { text: "i", italic: true },
      { text: " " },
      { text: "s", strike: true },
      { text: " " },
      { text: "c", code: true },
    ]);
    expect(inline("see https://example.com/a.")).toEqual([{ text: "see " }, { text: "https://example.com/a", link: "https://example.com/a" }, { text: "." }]);
    expect(inline("a<br>b <b>x</b>")).toEqual([{ text: "a\nb " }, { text: "x", bold: true }]);
    expect(inline("snake_case_name stays")).toEqual([{ text: "snake_case_name stays" }]);
  });

  test("a reply as a preview line", () => {
    expect(plain("## Done\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```sh\nls\npwd\n```")).toBe("Done\n[table, 1 row: A, B]\n[sh code, 2 lines]");
  });
});
