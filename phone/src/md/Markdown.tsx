import * as Clipboard from "expo-clipboard";
import { Image } from "expo-image";
import { memo, useMemo, useState } from "react";
import { Linking, ScrollView, Text, View } from "react-native";

import { HtmlView } from "../blocks/HtmlView";
import { fonts, size, sp, tint, useTheme } from "../theme";
import { IconButton, Label, Mono } from "../ui/parts";
import { parseMarkdown, type Align, type MdBlock, type Span } from "./parse";

export function openLink(url: string) {
  if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) Linking.openURL(url).catch(() => undefined);
}

/** Inline spans as one Text, so they wrap together. */
export function Spans({ spans, style, color }: { spans: Span[]; style?: object; color?: string }) {
  const { c } = useTheme();
  return (
    <Text selectable style={[{ fontFamily: fonts.reading, fontSize: size.normal, lineHeight: 24, color: color ?? c.ink }, style]}>
      {spans.map((s, i) => (
        <Text
          key={i}
          onPress={s.link ? () => openLink(s.link!) : undefined}
          style={[
            s.bold ? { fontWeight: "700" } : null,
            s.italic ? { fontStyle: "italic" } : null,
            s.code ? { fontFamily: fonts.mono, fontSize: size.small + 1, backgroundColor: c.sunken } : null,
            s.strike ? { textDecorationLine: "line-through" } : null,
            s.link ? { color: c.accentInk, textDecorationLine: "underline" } : null,
          ]}
        >
          {s.code ? ` ${s.text} ` : s.text}
        </Text>
      ))}
    </Text>
  );
}

export function CodeBlock({ code, lang, title }: { code: string; lang?: string | null; title?: string }) {
  const { c } = useTheme();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    Clipboard.setStringAsync(code).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <View style={{ backgroundColor: c.sunken, borderLeftWidth: 2, borderLeftColor: c.tCode }}>
      <View style={{ flexDirection: "row", alignItems: "center", paddingLeft: sp(3) }}>
        <Label style={{ flex: 1 }} numberOfLines={1}>
          {title || lang || "code"}
        </Label>
        <IconButton icon={copied ? "check" : "copy"} label={copied ? "Copied" : "Copy"} onPress={copy} size={18} color={copied ? c.ok : c.muted} />
      </View>
      <ScrollView horizontal nestedScrollEnabled showsHorizontalScrollIndicator contentContainerStyle={{ paddingHorizontal: sp(3), paddingBottom: sp(3) }}>
        <Mono selectable>{code.replace(/\s+$/, "")}</Mono>
      </ScrollView>
    </View>
  );
}

const textAlign = (a: Align) => (a === "center" ? "center" : a === "right" ? "right" : "left");

/** A table that scrolls sideways. Each column is as wide as its longest cell needs, within limits. */
export function TableView({ header, rows, align }: { header: Span[][]; rows: Span[][][]; align?: Align[] }) {
  const { c } = useTheme();
  const columns = Math.max(header.length, ...rows.map((r) => r.length));
  const widths = Array.from({ length: columns }, (_, col) => {
    const longest = Math.max(
      ...[header, ...rows].map((r) => (r[col] ?? []).map((s) => s.text).join("").length),
      2,
    );
    return Math.min(Math.max(longest * 8 + 24, 72), 280);
  });
  const row = (cells: Span[][], head: boolean, key: string | number) => (
    <View key={key} style={{ flexDirection: "row", borderBottomWidth: 1, borderBottomColor: head ? c.lineStrong : c.line }}>
      {widths.map((w, col) => (
        <View key={col} style={{ width: w, paddingHorizontal: sp(2), paddingVertical: sp(2) }}>
          <Spans
            spans={cells[col] ?? []}
            style={{ fontFamily: head ? fonts.titles : fonts.reading, fontSize: 15, lineHeight: 21, fontWeight: head ? "600" : "400", textAlign: textAlign(align?.[col] ?? "none") }}
          />
        </View>
      ))}
    </View>
  );
  return (
    <ScrollView horizontal nestedScrollEnabled showsHorizontalScrollIndicator style={{ flexGrow: 0 }}>
      <View style={{ borderLeftWidth: 2, borderLeftColor: c.tTable, backgroundColor: c.raised }}>
        {header.length ? row(header, true, "h") : null}
        {rows.map((r, i) => row(r, false, i))}
      </View>
    </ScrollView>
  );
}

function MdView({ block }: { block: MdBlock }) {
  const { c } = useTheme();
  let body: React.ReactNode;
  switch (block.type) {
    case "heading": {
      const px = [24, 21, 18, 16, 16, 16][block.level - 1] ?? 16;
      body = <Spans spans={block.spans} style={{ fontFamily: fonts.titles, fontWeight: "700", fontSize: px, lineHeight: px + 8 }} />;
      break;
    }
    case "paragraph":
      body = <Spans spans={block.spans} />;
      break;
    case "item": {
      const mark = block.checked != null ? null : block.ordered ? `${block.number}.` : "•";
      body = (
        <View style={{ flexDirection: "row", paddingLeft: block.depth * 18, gap: sp(2) }}>
          {block.checked != null ? (
            <View style={{ paddingTop: 4 }}>
              <View
                style={{
                  width: 16,
                  height: 16,
                  borderWidth: 1.5,
                  borderColor: block.checked ? c.ok : c.lineStrong,
                  backgroundColor: block.checked ? tint(c.ok.startsWith("#") ? c.ok : "#63c7a4", 0.25) : "transparent",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {block.checked ? <View style={{ width: 8, height: 8, backgroundColor: c.ok }} /> : null}
              </View>
            </View>
          ) : (
            <Text style={{ fontFamily: fonts.titles, fontSize: size.normal, lineHeight: 24, color: c.accentInk, minWidth: block.ordered ? 22 : 12 }}>{mark}</Text>
          )}
          <View style={{ flex: 1 }}>
            <Spans spans={block.spans} color={block.checked ? c.muted : undefined} style={block.checked ? { textDecorationLine: "line-through" } : undefined} />
          </View>
        </View>
      );
      break;
    }
    case "code":
      body = <CodeBlock code={block.text} lang={block.lang} />;
      break;
    case "table":
      body = <TableView header={block.header} rows={block.rows} align={block.align} />;
      break;
    case "html":
      body = <HtmlView html={block.html} />;
      break;
    case "image":
      body = /^https?:\/\//i.test(block.url) ? (
        <Image source={{ uri: block.url }} style={{ width: "100%", height: 220, backgroundColor: c.sunken }} contentFit="contain" accessibilityLabel={block.alt} />
      ) : (
        <Label>{block.alt || "picture"}</Label>
      );
      break;
    case "rule":
      body = <View style={{ height: 1, backgroundColor: c.line, marginVertical: sp(1) }} />;
      break;
  }
  if (!block.quote) return <>{body}</>;
  return (
    <View style={{ borderLeftWidth: 3, borderLeftColor: c.lineStrong, paddingLeft: sp(3) * block.quote, opacity: 0.92 }}>{body}</View>
  );
}

/** A reply's Markdown, drawn natively. */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  return (
    <View style={{ gap: sp(2.5) }}>
      {blocks.map((b, i) => (
        <MdView key={i} block={b} />
      ))}
    </View>
  );
});
