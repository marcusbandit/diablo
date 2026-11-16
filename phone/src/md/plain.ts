import { parseMarkdown, spanText, type MdBlock } from "./parse";

/** A reply as one plain line per block: for previews, where tables and code do not fit. */
export function plain(markdown: string): string {
  return parseMarkdown(markdown)
    .map((b: MdBlock): string | null => {
      switch (b.type) {
        case "heading":
        case "paragraph":
          return spanText(b.spans);
        case "item":
          return `${"  ".repeat(b.depth)}${b.checked === true ? "[x]" : b.checked === false ? "[ ]" : b.ordered ? `${b.number}.` : "•"} ${spanText(b.spans)}`;
        case "code": {
          const n = b.text.replace(/\s+$/, "").split("\n").length;
          return `[${b.lang ? `${b.lang} ` : ""}code, ${n} ${n === 1 ? "line" : "lines"}]`;
        }
        case "table":
          return `[table, ${b.rows.length} ${b.rows.length === 1 ? "row" : "rows"}: ${b.header.map(spanText).join(", ")}]`;
        case "html":
          return b.html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() || null;
        case "image":
          return b.alt ? `[picture: ${b.alt}]` : "[picture]";
        case "rule":
          return null;
      }
    })
    .filter((l): l is string => !!l && l.trim().length > 0)
    .join("\n")
    .trim();
}
