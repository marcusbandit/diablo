// A small Markdown reader for Diablo's replies: the block and inline forms he writes, read into
// a flat list of blocks the screens draw natively. Pure, so the tests run it without a phone.

export type Span = {
  text: string;
  bold?: boolean;
  italic?: boolean;
  code?: boolean;
  strike?: boolean;
  link?: string;
};

export type Align = "none" | "left" | "center" | "right";

export type MdBlock =
  | { type: "heading"; level: number; spans: Span[]; quote: number }
  | { type: "paragraph"; spans: Span[]; quote: number }
  | { type: "item"; ordered: boolean; number: number; depth: number; checked: boolean | null; spans: Span[]; quote: number }
  | { type: "code"; lang: string | null; text: string; quote: number }
  | { type: "table"; align: Align[]; header: Span[][]; rows: Span[][][]; quote: number }
  | { type: "html"; html: string; quote: number }
  | { type: "image"; url: string; alt: string; quote: number }
  | { type: "rule"; quote: number };

const fence = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)[^`]*$/;
const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const rule = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const listItem = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const task = /^\[([ xX])\]\s+(.*)$/;
const image = /^!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)$/;
const tableSep = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const htmlBlockStart = /^<(div|table|details|section|figure|center|pre|p|ul|ol|blockquote|svg|style|html|!doctype)\b/i;

function stripQuote(raw: string): [number, string] {
  let depth = 0;
  let s = raw;
  for (;;) {
    const t = s.replace(/^\s+/, "");
    if (!t.startsWith(">") || s.length - t.length > 3) break;
    depth++;
    s = t.slice(1).replace(/^ /, "");
  }
  return [depth, s];
}

function cells(row: string): string[] {
  let t = row.trim();
  if (t.startsWith("|")) t = t.slice(1);
  if (t.endsWith("|") && !t.endsWith("\\|")) t = t.slice(0, -1);
  const out: string[] = [];
  let cur = "";
  for (let k = 0; k < t.length; k++) {
    const c = t[k];
    if (c === "\\" && t[k + 1] === "|") {
      cur += "|";
      k++;
    } else if (c === "|") {
      out.push(cur);
      cur = "";
    } else cur += c;
  }
  out.push(cur);
  return out;
}

export function parseMarkdown(source: string): MdBlock[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const out: MdBlock[] = [];
  let para: string[] = [];
  let paraQuote = 0;
  const flush = () => {
    if (para.length) {
      out.push({ type: "paragraph", spans: inline(para.join("\n")), quote: paraQuote });
      para = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const [quote, line] = stripQuote(lines[i]);

    const open = fence.exec(line);
    if (open) {
      flush();
      const marker = open[1];
      const lang = open[2] || null;
      const body: string[] = [];
      i++;
      while (i < lines.length) {
        const inner = stripQuote(lines[i])[1];
        const t = inner.trim();
        if (t.startsWith(marker) && [...t].every((ch) => ch === marker[0])) break;
        body.push(inner);
        i++;
      }
      i++;
      const text = body.join("\n");
      if (lang?.toLowerCase() === "html" || lang?.toLowerCase() === "svg") out.push({ type: "html", html: text, quote });
      else out.push({ type: "code", lang, text, quote });
      continue;
    }

    if (!line.trim()) {
      flush();
      i++;
      continue;
    }

    const h = heading.exec(line);
    if (h) {
      flush();
      out.push({ type: "heading", level: h[1].length, spans: inline(h[2]), quote });
      i++;
      continue;
    }

    if (rule.test(line) && !(para.length && /^\s*-+\s*$/.test(line))) {
      flush();
      out.push({ type: "rule", quote });
      i++;
      continue;
    }

    const img = image.exec(line.trim());
    if (img) {
      flush();
      out.push({ type: "image", url: img[2], alt: img[1], quote });
      i++;
      continue;
    }

    const next = i + 1 < lines.length ? stripQuote(lines[i + 1])[1] : "";
    if (line.includes("|") && tableSep.test(next) && next.includes("-")) {
      flush();
      const align: Align[] = cells(next).map((c) => {
        const t = c.trim();
        if (t.startsWith(":") && t.endsWith(":")) return "center";
        if (t.endsWith(":")) return "right";
        if (t.startsWith(":")) return "left";
        return "none";
      });
      const header = cells(line).map((c) => inline(c.trim()));
      const rows: Span[][][] = [];
      i += 2;
      while (i < lines.length) {
        const row = stripQuote(lines[i])[1];
        if (!row.trim() || !row.includes("|")) break;
        rows.push(cells(row).map((c) => inline(c.trim())));
        i++;
      }
      out.push({ type: "table", align, header, rows, quote });
      continue;
    }

    const item = listItem.exec(line);
    if (item) {
      flush();
      const indent = item[1].replace(/\t/g, "    ").length;
      const marker = item[2];
      let text = item[3];
      let checked: boolean | null = null;
      const t = task.exec(text);
      if (t) {
        checked = t[1] !== " ";
        text = t[2];
      }
      const ordered = /^\d/.test(marker);
      out.push({
        type: "item",
        ordered,
        number: ordered ? Number(marker.slice(0, -1)) : 0,
        depth: Math.floor(indent / 2),
        checked,
        spans: inline(text),
        quote,
      });
      i++;
      continue;
    }

    if (!para.length && htmlBlockStart.test(line.trimStart())) {
      const body: string[] = [];
      while (i < lines.length && lines[i].trim()) {
        body.push(lines[i]);
        i++;
      }
      out.push({ type: "html", html: body.join("\n"), quote });
      continue;
    }

    const last = out[out.length - 1];
    if (!para.length && last?.type === "item" && /^\s/.test(line)) {
      last.spans = [...last.spans, { text: " " }, ...inline(line.trim())];
    } else {
      if (!para.length) paraQuote = quote;
      else if (quote !== paraQuote) {
        flush();
        paraQuote = quote;
      }
      para.push(line.trim());
    }
    i++;
  }
  flush();
  return out;
}

type Style = Omit<Span, "text">;

const link = /^\[([^\]]+)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/;
const autolink = /^(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/;
const angleLink = /^<(https?:\/\/[^>\s]+)>/;
const htmlTag = /^<(\/?)([a-zA-Z][a-zA-Z0-9]*)(\s[^>]*)?(\/?)>/;
const tagStyle: Record<string, Style> = {
  b: { bold: true },
  strong: { bold: true },
  i: { italic: true },
  em: { italic: true },
  s: { strike: true },
  del: { strike: true },
  strike: { strike: true },
  code: { code: true },
  kbd: { code: true },
  tt: { code: true },
};

const same = (a: Span, b: Span) =>
  !!a.bold === !!b.bold && !!a.italic === !!b.italic && !!a.code === !!b.code && !!a.strike === !!b.strike && a.link === b.link;

function merge(spans: Span[]): Span[] {
  const out: Span[] = [];
  for (const s of spans) {
    const last = out[out.length - 1];
    if (last && same(last, s)) last.text += s.text;
    else out.push({ ...s });
  }
  return out;
}

export function inline(text: string): Span[] {
  const out: Span[] = [];
  parseInline(text, {}, out);
  return merge(out).filter((s) => s.text.length > 0);
}

function findClose(text: string, marker: string, from: number): number {
  for (let k = from; k < text.length; k++) {
    if (text[k] === "\\") {
      k++;
      continue;
    }
    if (text[k] === marker && (marker === "*" || k + 1 >= text.length || !/[A-Za-z0-9\u00C0-\uFFFF]/.test(text[k + 1]))) {
      if (marker === "*" && text.startsWith("**", k)) {
        k++;
        continue;
      }
      return k;
    }
  }
  return -1;
}

function parseInline(text: string, style: Style, out: Span[]) {
  let buf = "";
  const flush = () => {
    if (buf) {
      out.push({ text: buf, ...style });
      buf = "";
    }
  };
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    const rest = text.slice(i);
    if (c === "\\" && i + 1 < text.length && "\\`*_{}[]()#+-.!|<>~".includes(text[i + 1])) {
      buf += text[i + 1];
      i += 2;
    } else if (c === "\\" && text[i + 1] === "\n") {
      buf += "\n";
      i += 2;
    } else if (c === "`") {
      let run = 1;
      while (text[i + run] === "`") run++;
      const marker = "`".repeat(run);
      const end = text.indexOf(marker, i + run);
      if (end > i) {
        flush();
        out.push({ ...style, text: text.slice(i + run, end).replace(/^ (.*) $/, "$1"), code: true });
        i = end + run;
      } else {
        buf += marker;
        i += run;
      }
    } else if (rest.startsWith("**") || rest.startsWith("__")) {
      const marker = rest.slice(0, 2);
      let end = text.indexOf(marker, i + 2);
      while (end > 0 && end + 2 < text.length && text[end + 2] === marker[0]) end++;
      if (end > i + 2) {
        flush();
        parseInline(text.slice(i + 2, end), { ...style, bold: true }, out);
        i = end + 2;
      } else {
        buf += marker;
        i += 2;
      }
    } else if (rest.startsWith("~~")) {
      const end = text.indexOf("~~", i + 2);
      if (end > i + 2) {
        flush();
        parseInline(text.slice(i + 2, end), { ...style, strike: true }, out);
        i = end + 2;
      } else {
        buf += "~~";
        i += 2;
      }
    } else if ((c === "*" || (c === "_" && (i === 0 || !/[A-Za-z0-9\u00C0-\uFFFF]/.test(text[i - 1])))) && text[i + 1] !== " ") {
      const end = findClose(text, c, i + 1);
      if (end > i + 1) {
        flush();
        parseInline(text.slice(i + 1, end), { ...style, italic: true }, out);
        i = end + 1;
      } else {
        buf += c;
        i++;
      }
    } else if (c === "!" && text[i + 1] === "[") {
      const m = link.exec(rest.slice(1));
      if (m) {
        flush();
        out.push({ ...style, text: m[1] || "picture", link: m[2] });
        i += m[0].length + 1;
      } else {
        buf += c;
        i++;
      }
    } else if (c === "[") {
      const m = link.exec(rest);
      if (m) {
        flush();
        parseInline(m[1], { ...style, link: m[2] }, out);
        i += m[0].length;
      } else {
        buf += c;
        i++;
      }
    } else if (c === "<") {
      const a = angleLink.exec(rest);
      const m = a ? null : htmlTag.exec(rest);
      if (a) {
        flush();
        out.push({ ...style, text: a[1], link: a[1] });
        i += a[0].length;
      } else if (m) {
        const name = m[2].toLowerCase();
        if (name === "br") {
          buf += "\n";
          i += m[0].length;
        } else if (!m[1] && tagStyle[name]) {
          const close = text.toLowerCase().indexOf(`</${name}>`, i + m[0].length);
          if (close > 0) {
            flush();
            parseInline(text.slice(i + m[0].length, close), { ...style, ...tagStyle[name] }, out);
            i = close + name.length + 3;
          } else {
            i += m[0].length;
          }
        } else {
          // Other tags are dropped; their text stays.
          i += m[0].length;
        }
      } else {
        buf += c;
        i++;
      }
    } else if ((c === "h" && (i === 0 || /[\s(]/.test(text[i - 1])) && !style.link)) {
      const m = autolink.exec(rest);
      if (m) {
        flush();
        out.push({ ...style, text: m[1], link: m[1] });
        i += m[1].length;
      } else {
        buf += c;
        i++;
      }
    } else if (c === "&") {
      const m = /^&(amp|lt|gt|quot|#39|nbsp);/.exec(rest);
      if (m) {
        buf += { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", nbsp: " " }[m[1]] ?? "";
        i += m[0].length;
      } else {
        buf += c;
        i++;
      }
    } else {
      buf += c;
      i++;
    }
  }
  flush();
}

/** Plain text of some spans. */
export const spanText = (spans: Span[]) => spans.map((s) => s.text).join("");
