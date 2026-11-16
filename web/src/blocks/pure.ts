export type DiffLine = { kind: "add" | "del" | "hunk" | "meta" | "ctx"; text: string };

export function diffLines(diff: string): DiffLine[] {
  return diff
    .replace(/\n$/, "")
    .split("\n")
    .map((text) => {
      if (/^(\+\+\+|---|diff --git|index )/.test(text)) return { kind: "meta", text };
      if (text.startsWith("@@")) return { kind: "hunk", text };
      if (text.startsWith("+")) return { kind: "add", text };
      if (text.startsWith("-")) return { kind: "del", text };
      return { kind: "ctx", text };
    });
}

const THEME_VARS = ["bg", "raised", "sunken", "ink", "muted", "faint", "line", "accent", "ok", "warn", "fail"];

/**
 * The page an html block draws, with the app's colours as CSS variables first. It goes into an
 * iframe with sandbox="", so it runs no script and reaches nothing: a CSP says so twice.
 */
export function sandboxDoc(html: string, style: { getPropertyValue(name: string): string }): string {
  const vars = THEME_VARS.map((name) => `--${name}:${style.getPropertyValue(`--${name}`).trim() || "initial"};`).join("");
  const head =
    `<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:">` +
    `<style>:root{${vars}--font-sans:"Source Sans 3",system-ui,sans-serif;--font-mono:ui-monospace,monospace;color-scheme:light dark}` +
    `html,body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font-sans)}</style>`;
  if (/<head[^>]*>/i.test(html)) return html.replace(/<head[^>]*>/i, (m) => m + head);
  if (/<html[^>]*>/i.test(html)) return html.replace(/<html[^>]*>/i, (m) => m + `<head>${head}</head>`);
  return `<!doctype html><html><head>${head}</head><body>${html}</body></html>`;
}
