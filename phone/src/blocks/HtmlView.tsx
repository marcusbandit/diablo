import { useMemo } from "react";
import { View } from "react-native";
import { WebView } from "react-native-webview";

import { useTheme, type Colors } from "../theme";

// A page the brain wrote, drawn sandboxed: JavaScript off, no file access, and a content
// security policy that lets nothing load from the network. Pictures and fonts only as data: URLs.
const CSP = "default-src 'none'; img-src data:; media-src data:; font-src data:; style-src 'unsafe-inline'";

function themed(html: string, c: Colors): string {
  const vars = `:root{--bg:${c.bg};--raised:${c.raised};--sunken:${c.sunken};--ink:${c.ink};--muted:${c.muted};--faint:${c.faint};--line:${c.line};--accent:${c.accent};--ok:${c.ok};--warn:${c.warn};--fail:${c.fail};--font-sans:sans-serif;--font-mono:monospace;color-scheme:dark light}
html,body{margin:0;padding:8px;background:var(--bg);color:var(--ink);font-family:var(--font-sans);font-size:15px;line-height:1.45;word-wrap:break-word}
img,svg,canvas,video{max-width:100%;height:auto}table{border-collapse:collapse}td,th{border:1px solid var(--line);padding:4px 6px}`;
  const head = `<meta http-equiv="Content-Security-Policy" content="${CSP}"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${vars}</style>`;
  if (/<head[^>]*>/i.test(html)) return html.replace(/<head[^>]*>/i, (m) => `${m}${head}`);
  if (/<html[^>]*>/i.test(html)) return html.replace(/<html[^>]*>/i, (m) => `${m}<head>${head}</head>`);
  return `<!doctype html><html><head>${head}</head><body>${html}</body></html>`;
}

export function HtmlView({ html, height }: { html: string; height?: number }) {
  const { c } = useTheme();
  const doc = useMemo(() => themed(html, c), [html, c]);
  const h = Math.min(Math.max(height ?? 260, 120), 640);
  return (
    <View style={{ height: h, borderWidth: 1, borderColor: c.line, backgroundColor: c.bg }}>
      <WebView
        source={{ html: doc, baseUrl: "about:blank" }}
        originWhitelist={["about:*"]}
        javaScriptEnabled={false}
        domStorageEnabled={false}
        allowFileAccess={false}
        allowFileAccessFromFileURLs={false}
        allowUniversalAccessFromFileURLs={false}
        thirdPartyCookiesEnabled={false}
        sharedCookiesEnabled={false}
        geolocationEnabled={false}
        setSupportMultipleWindows={false}
        mixedContentMode="never"
        cacheEnabled={false}
        incognito
        nestedScrollEnabled
        onShouldStartLoadWithRequest={(req) => req.url === "about:blank" || req.url.startsWith("data:")}
        style={{ backgroundColor: c.bg }}
      />
    </View>
  );
}
