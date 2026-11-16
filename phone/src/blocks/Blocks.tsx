import type { Block } from "@diablo/hub";
import { Image } from "expo-image";
import { memo, useState, type ReactNode } from "react";
import { Alert, Pressable, ScrollView, Switch, Text, View } from "react-native";
import Svg, { Circle } from "react-native-svg";

import { CodeBlock, openLink, TableView } from "../md/Markdown";
import { inline } from "../md/parse";
import { fonts, size, sp, tint, useTheme, validAccent, type Colors } from "../theme";
import { Icon } from "../ui/Icon";
import { Button, Card, CardHead, Field, Label, Mono, Reading, Title } from "../ui/parts";
import { useBlockActions } from "./context";
import { HtmlView } from "./HtmlView";

type B = Block & Record<string, any>;

const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
const num = (v: unknown) => (typeof v === "number" ? v : Number(v) || 0);
const arr = <T = any,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

function Ring({ value, max, color, track, px = 96 }: { value: number; max: number; color: string; track: string; px?: number }) {
  const stroke = 9;
  const r = (px - stroke) / 2;
  const len = 2 * Math.PI * r;
  const frac = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  return (
    <Svg width={px} height={px}>
      <Circle cx={px / 2} cy={px / 2} r={r} stroke={track} strokeWidth={stroke} fill="none" />
      <Circle
        cx={px / 2}
        cy={px / 2}
        r={r}
        stroke={color}
        strokeWidth={stroke}
        fill="none"
        strokeDasharray={`${len * frac} ${len}`}
        strokeLinecap="butt"
        transform={`rotate(-90 ${px / 2} ${px / 2})`}
      />
    </Svg>
  );
}

function Goal({ b }: { b: B }) {
  const { c } = useTheme();
  const value = num(b.value);
  const max = num(b.max);
  const updated = b.updated && typeof b.updated === "object" ? str((b.updated as B).age) : "";
  return (
    <Card>
      <View style={{ flexDirection: "row", gap: sp(4), alignItems: "center" }}>
        <View style={{ alignItems: "center", justifyContent: "center" }}>
          <Ring value={value} max={max} color={c.accent} track={c.line} />
          <View style={{ position: "absolute", alignItems: "center" }}>
            <Text style={{ fontFamily: fonts.titles, fontSize: 24, fontWeight: "700", color: c.ink }}>{value}</Text>
            <Label>of {max}</Label>
          </View>
        </View>
        <View style={{ flex: 1, gap: sp(1.5) }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: sp(2) }}>
            {b.icon ? <Icon name={str(b.icon)} size={18} color={c.accentInk} /> : null}
            <Title style={{ flex: 1 }}>{str(b.title)}</Title>
          </View>
          {b.caption ? <Reading color={c.muted}>{str(b.caption)}</Reading> : null}
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(1.5) }}>
            {arr<B>(b.badges).map((badge, i) => (
              <View key={i} style={{ flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: c.sunken, paddingHorizontal: sp(2), paddingVertical: 3 }}>
                <Icon name={str(badge.icon)} size={14} color={c.muted} />
                <Label>{str(badge.label)}</Label>
              </View>
            ))}
          </View>
          {updated ? <Label color={b.stale ? c.warn : c.faint}>{updated}</Label> : null}
        </View>
      </View>
    </Card>
  );
}

function Stats({ b }: { b: B }) {
  const { c } = useTheme();
  const { open } = useBlockActions();
  return (
    <Card bare={!b.title}>
      <CardHead title={str(b.title)} />
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(2) }}>
        {arr<B>(b.items).map((item, i) => (
          <Pressable
            key={i}
            disabled={!item.open}
            onPress={() => open(str(item.open))}
            style={{ flexGrow: 1, flexBasis: 96, backgroundColor: c.raised, borderWidth: 1, borderColor: c.line, padding: sp(3), gap: 2 }}
          >
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              {item.icon ? <Icon name={str(item.icon)} size={15} color={c.muted} /> : null}
              <Label numberOfLines={1} style={{ flex: 1 }}>
                {str(item.label)}
              </Label>
            </View>
            <Text style={{ fontFamily: fonts.titles, color: c.ink }}>
              <Text style={{ fontSize: 22, fontWeight: "700" }}>{str(item.value)}</Text>
              {item.unit ? <Text style={{ fontSize: size.small, color: c.muted }}>{` ${str(item.unit)}`}</Text> : null}
            </Text>
          </Pressable>
        ))}
      </View>
      {b.caption ? <Label>{str(b.caption)}</Label> : null}
    </Card>
  );
}

function Bar({ frac, color, track }: { frac: number; color: string; track: string }) {
  return (
    <View style={{ height: 8, backgroundColor: track }}>
      <View style={{ width: `${Math.min(Math.max(frac, 0), 1) * 100}%`, height: 8, backgroundColor: color }} />
    </View>
  );
}

function Progress({ b }: { b: B }) {
  const { c } = useTheme();
  const value = num(b.value);
  const max = num(b.max);
  return (
    <Card>
      <View style={{ flexDirection: "row", alignItems: "center", gap: sp(2) }}>
        {b.icon ? <Icon name={str(b.icon)} size={18} color={c.accentInk} /> : null}
        <Title style={{ flex: 1 }}>{str(b.label)}</Title>
        <Label color={c.ink}>
          {value} / {max}
        </Label>
      </View>
      <Bar frac={max > 0 ? value / max : 0} color={c.accent} track={c.line} />
      {b.caption ? <Label>{str(b.caption)}</Label> : null}
    </Card>
  );
}

function toneColor(tone: unknown, c: Colors, fallback: string) {
  switch (tone) {
    case "accent":
      return c.accent;
    case "ok":
      return c.ok;
    case "warn":
      return c.warn;
    case "ink":
      return c.ink;
    default:
      return fallback;
  }
}

function Bars({ b }: { b: B }) {
  const { c } = useTheme();
  const labels = arr<string>(b.labels);
  const series = arr<B>(b.series);
  const palette = [c.accent, c.warn, c.ok, c.ink];
  const totals = labels.map((_, i) => (b.stacked ? series.reduce((s, x) => s + num(arr(x.values)[i]), 0) : Math.max(0, ...series.map((x) => num(arr(x.values)[i])))));
  const top = Math.max(1, ...totals);
  const H = 96;
  return (
    <Card>
      <CardHead title={str(b.title)} icon={b.icon ? str(b.icon) : undefined} />
      <View style={{ flexDirection: "row", alignItems: "flex-end", gap: sp(1.5), height: H + 20 }}>
        {labels.map((label, i) => (
          <View key={i} style={{ flex: 1, alignItems: "center", gap: 4 }}>
            <View style={{ height: H, width: "100%", justifyContent: "flex-end", flexDirection: b.stacked ? "column-reverse" : "row", alignItems: "flex-end", gap: b.stacked ? 0 : 2 }}>
              {series.map((s, k) => {
                const v = num(arr(s.values)[i]);
                return <View key={k} style={{ height: (v / top) * H, flex: b.stacked ? undefined : 1, width: b.stacked ? "100%" : undefined, backgroundColor: toneColor(s.tone, c, palette[k % palette.length]) }} />;
              })}
            </View>
            <Label numberOfLines={1}>{label}</Label>
          </View>
        ))}
      </View>
      {series.length > 1 ? (
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(3) }}>
          {series.map((s, k) => (
            <View key={k} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <View style={{ width: 10, height: 10, backgroundColor: toneColor(s.tone, c, palette[k % palette.length]) }} />
              <Label>{str(s.name)}</Label>
            </View>
          ))}
        </View>
      ) : null}
      {b.caption ? <Label>{str(b.caption)}</Label> : null}
    </Card>
  );
}

function ListRow({ item }: { item: B }) {
  const { c } = useTheme();
  const { say, act } = useBlockActions();
  const [open, setOpen] = useState(false);
  const expands = !!item.url || arr(item.facts).length > 0;
  const press = () => {
    if (expands) setOpen(!open);
    else if (item.title) say(str(item.reply || item.title), false);
  };
  const run = (a: B) => {
    if (a.danger) {
      Alert.alert(str(a.label), str(item.title), [
        { text: "Keep it", style: "cancel" },
        { text: str(a.label), style: "destructive", onPress: () => act(a.act as Record<string, unknown>) },
      ]);
    } else act(a.act as Record<string, unknown>);
  };
  return (
    <View style={{ borderTopWidth: 1, borderTopColor: c.line }}>
      <Pressable onPress={press} android_ripple={{ color: c.line }} style={{ flexDirection: "row", gap: sp(3), paddingVertical: sp(2.5), alignItems: "flex-start" }}>
        <View style={{ flex: 1, gap: 2 }}>
          <Title style={item.important ? { color: c.accentInk } : undefined}>{str(item.title || item.subject)}</Title>
          {item.detail || item.summary ? <Label numberOfLines={open ? undefined : 2}>{str(item.detail || item.summary)}</Label> : null}
        </View>
        {item.trailing || item.date ? <Label color={c.ink}>{str(item.trailing || item.date)}</Label> : null}
        {expands ? <Icon name={open ? "less" : "more"} size={18} color={c.faint} /> : null}
      </Pressable>
      {open ? (
        <View style={{ gap: sp(2), paddingBottom: sp(3) }}>
          {arr<B>(item.facts).map((f, i) => (
            <View key={i} style={{ flexDirection: "row", gap: sp(2) }}>
              <Label style={{ width: 88 }}>{str(f.label)}</Label>
              <Reading style={{ flex: 1, fontSize: 15, lineHeight: 21 }}>{str(f.value)}</Reading>
            </View>
          ))}
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(2) }}>
            {item.url ? <Button label="Open" icon="open" onPress={() => openLink(str(item.url))} /> : null}
            {item.reply ? <Button label="Reply" kind="quiet" onPress={() => say(str(item.reply), false)} /> : null}
          </View>
        </View>
      ) : null}
      {arr<B>(item.actions).length ? (
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(2), paddingBottom: sp(2) }}>
          {arr<B>(item.actions).map((a, i) => (
            <Button key={i} label={str(a.label)} kind={a.danger ? "danger" : "line"} onPress={() => run(a)} />
          ))}
        </View>
      ) : null}
    </View>
  );
}

function List({ b }: { b: B }) {
  const items = arr<B>(b.items);
  return (
    <Card>
      <CardHead title={str(b.title)} icon={b.icon ? str(b.icon) : undefined} />
      {items.length ? items.map((item, i) => <ListRow key={i} item={item} />) : <Label>{str(b.empty) || "Nothing here"}</Label>}
      {b.caption ? <Label>{str(b.caption)}</Label> : null}
    </Card>
  );
}

/** Buttons that say a line, fill the composer, or tap a module. */
export function ActionChips({ items }: { items: B[] }) {
  const { say, act } = useBlockActions();
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: sp(2) }}>
      {items.map((item, i) => (
        <Button
          key={i}
          label={str(item.label)}
          icon={item.icon ? str(item.icon) : undefined}
          onPress={() => {
            if (item.act && typeof item.act === "object") act(item.act as Record<string, unknown>);
            else if (item.say) say(str(item.say), item.send !== false);
          }}
        />
      ))}
    </ScrollView>
  );
}

function Notice({ b }: { b: B }) {
  const { c } = useTheme();
  const { act } = useBlockActions();
  const color = b.tone === "fail" ? c.fail : b.tone === "warn" ? c.warn : b.tone === "ok" ? c.ok : c.muted;
  return (
    <View style={{ borderLeftWidth: 3, borderLeftColor: color, backgroundColor: c.raised, padding: sp(3), gap: sp(2) }}>
      {b.title ? <Title>{str(b.title)}</Title> : null}
      <Reading color={b.tone === "fail" ? c.ink : c.muted}>{str(b.text)}</Reading>
      {b.retry && !b.setup ? (
        <View style={{ flexDirection: "row" }}>
          <Button label="Try again" icon="retry" onPress={() => act({ module: b.module, action: "retry", args: {} })} />
        </View>
      ) : null}
    </View>
  );
}

function ImageBlock({ b }: { b: B }) {
  const { c } = useTheme();
  const { file } = useBlockActions();
  const [ratio, setRatio] = useState(4 / 3);
  const source = b.file ? file(str(b.file)) : /^https?:\/\//i.test(str(b.url)) ? { uri: str(b.url), headers: {} } : null;
  return (
    <Card>
      <CardHead title={str(b.title)} />
      {source ? (
        <Image
          source={source}
          onLoad={(e) => e.source.width > 0 && e.source.height > 0 && setRatio(Math.max(0.5, Math.min(e.source.width / e.source.height, 3)))}
          style={{ width: "100%", aspectRatio: ratio, backgroundColor: c.sunken }}
          contentFit="contain"
          accessibilityLabel={str(b.caption || b.title || "picture")}
        />
      ) : (
        <Label>{str(b.name) || "a picture this phone cannot fetch"}</Label>
      )}
      {b.caption ? <Label>{str(b.caption)}</Label> : null}
    </Card>
  );
}

function Diff({ b }: { b: B }) {
  const { c } = useTheme();
  const lines = str(b.diff).replace(/\s+$/, "").split("\n");
  return (
    <View style={{ backgroundColor: c.sunken, borderLeftWidth: 2, borderLeftColor: c.tGit }}>
      {b.title ? <Label style={{ paddingHorizontal: sp(3), paddingTop: sp(2) }}>{str(b.title)}</Label> : null}
      <ScrollView horizontal nestedScrollEnabled contentContainerStyle={{ paddingVertical: sp(2) }}>
        <View>
          {lines.map((line, i) => {
            const add = line.startsWith("+") && !line.startsWith("+++");
            const del = line.startsWith("-") && !line.startsWith("---");
            const hunk = line.startsWith("@@");
            return (
              <Mono
                key={i}
                style={{
                  paddingHorizontal: sp(3),
                  backgroundColor: add ? tint(c.ok, 0.16) : del ? tint(c.fail, 0.16) : "transparent",
                  color: hunk ? c.accentInk : line.startsWith("+++") || line.startsWith("---") ? c.muted : c.ink,
                }}
              >
                {line || " "}
              </Mono>
            );
          })}
        </View>
      </ScrollView>
    </View>
  );
}

function Question({ b }: { b: B }) {
  const { c } = useTheme();
  const { answer } = useBlockActions();
  const [picked, setPicked] = useState<number[]>([]);
  const [text, setText] = useState("");
  const options = arr<B>(b.options);
  const state = str(b.state) || "pending";
  const pending = state === "pending";
  const answered = b.answer && typeof b.answer === "object" ? (b.answer as B) : null;
  const chosen = new Set(arr<number>(answered?.choices));
  const send = (choices: number[]) => answer(str(b.id), { choices, text: text.trim() || undefined });
  return (
    <Card style={{ borderLeftWidth: 3, borderLeftColor: pending ? c.accent : c.line }}>
      {b.header ? <Label color={c.accentInk}>{str(b.header).toUpperCase()}</Label> : null}
      <Reading>{str(b.question)}</Reading>
      <View style={{ gap: sp(2) }}>
        {options.map((o, i) => {
          const on = pending ? picked.includes(i) : chosen.has(i);
          const recommended = b.recommended === i;
          return (
            <Pressable
              key={i}
              disabled={!pending}
              onPress={() => {
                if (b.multi) setPicked(on ? picked.filter((p) => p !== i) : [...picked, i]);
                else send([i]);
              }}
              android_ripple={{ color: c.line }}
              style={{
                borderWidth: 1,
                borderColor: on ? c.accent : c.lineStrong,
                backgroundColor: on ? tint(c.accent, 0.12) : "transparent",
                padding: sp(3),
                gap: 2,
                opacity: pending || on ? 1 : 0.55,
              }}
            >
              <View style={{ flexDirection: "row", alignItems: "center", gap: sp(2) }}>
                <Text style={{ fontFamily: fonts.mono, color: c.accentInk, fontSize: size.small }}>{i + 1}</Text>
                <Title style={{ flex: 1 }}>{str(o.label)}</Title>
                {recommended && pending ? <Label color={c.accentInk}>recommended</Label> : null}
                {on && !pending ? <Icon name="check" size={18} color={c.ok} /> : null}
              </View>
              {o.description ? <Label>{str(o.description)}</Label> : null}
            </Pressable>
          );
        })}
      </View>
      {pending && b.allow_text ? <Field value={text} onChangeText={setText} placeholder="In your own words" /> : null}
      {pending && (b.multi || (b.allow_text && text.trim())) ? (
        <View style={{ flexDirection: "row" }}>
          <Button label="Send" kind="primary" disabled={!picked.length && !text.trim()} onPress={() => send(picked)} />
        </View>
      ) : null}
      {!pending ? <Label>{state === "answered" ? `Answered${answered?.text ? `: ${str(answered.text)}` : ""}` : state}</Label> : null}
    </Card>
  );
}

function TableBlock({ b }: { b: B }) {
  const head = arr(b.head).map((h) => inline(str(h)));
  const rows = arr<unknown[]>(b.rows).map((r) => arr(r).map((cell) => inline(str(cell))));
  return (
    <View style={{ gap: sp(2) }}>
      {b.title ? <Title>{str(b.title)}</Title> : null}
      <TableView header={head} rows={rows} />
    </View>
  );
}

function Readout({ b }: { b: B }) {
  const { c } = useTheme();
  return (
    <Card>
      <CardHead title={str(b.title)} />
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(4) }}>
        {arr<B>(b.cells).map((cell, i) => (
          <View key={i} style={{ minWidth: 80, gap: 2 }}>
            <Text style={{ fontFamily: fonts.titles, fontSize: 22, fontWeight: "700", color: c.ink }}>{str(cell.v)}</Text>
            <Label>{str(cell.k)}</Label>
          </View>
        ))}
      </View>
    </Card>
  );
}

function Html({ b }: { b: B }) {
  return (
    <View style={{ gap: sp(2) }}>
      {b.title ? <Title>{str(b.title)}</Title> : null}
      <HtmlView html={str(b.html)} height={b.height ? num(b.height) : undefined} />
    </View>
  );
}

function Lamps({ b }: { b: B }) {
  const { c } = useTheme();
  const { act } = useBlockActions();
  return (
    <Card>
      <CardHead title={str(b.title) || "Lamps"} icon="lamp" />
      {arr<B>(b.lamps).map((lamp) => {
        const reachable = lamp.reachable !== false;
        const color = validAccent(str(lamp.color), c.tLights);
        return (
          <View key={str(lamp.id)} style={{ flexDirection: "row", alignItems: "center", gap: sp(3), opacity: reachable ? 1 : 0.5 }}>
            <View style={{ width: 10, height: 10, backgroundColor: lamp.on ? color : c.line }} />
            <View style={{ flex: 1 }}>
              <Title>{str(lamp.name)}</Title>
              <Label>{reachable ? (lamp.on ? `${num(lamp.level)}%` : "off") : "not reachable"}</Label>
            </View>
            <Switch
              value={!!lamp.on}
              disabled={!reachable}
              onValueChange={() => act({ module: "lights", action: "toggle", args: { lamp: lamp.id } })}
              trackColor={{ true: tint(c.accent, 0.5), false: c.line }}
              thumbColor={lamp.on ? c.accent : c.faint}
            />
          </View>
        );
      })}
    </Card>
  );
}

function Scenes({ b }: { b: B }) {
  const { c } = useTheme();
  const { act } = useBlockActions();
  return (
    <Card>
      <CardHead title={str(b.title) || "Scenes"} />
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(2) }}>
        {arr<B>(b.items).map((s) => (
          <Pressable
            key={str(s.id)}
            onPress={() => act({ module: "lights", action: "scene", args: { id: s.id } })}
            android_ripple={{ color: c.line }}
            style={{ flexDirection: "row", alignItems: "center", gap: sp(2), borderWidth: 1, borderColor: c.lineStrong, paddingHorizontal: sp(3), minHeight: 40 }}
          >
            <View style={{ flexDirection: "row" }}>
              {arr<string>(s.colors).slice(0, 3).map((col, i) => (
                <View key={i} style={{ width: 10, height: 18, backgroundColor: validAccent(col, c.line) }} />
              ))}
            </View>
            <Title>{str(s.name)}</Title>
          </Pressable>
        ))}
      </View>
    </Card>
  );
}

function CodeObject({ b }: { b: B }) {
  return <CodeBlock code={str(b.code)} lang={b.lang ? str(b.lang) : null} title={b.title ? str(b.title) : undefined} />;
}

function ActionsBlock({ b }: { b: B }) {
  return <ActionChips items={arr<B>(b.items)} />;
}

/**
 * The renderer for each block kind. A new kind is one component and one line here; a kind this
 * app does not know is skipped, so a newer hub never breaks it.
 */
export const registry: Record<string, (props: { b: B }) => ReactNode> = {
  goal: Goal,
  stats: Stats,
  progress: Progress,
  bars: Bars,
  list: List,
  actions: ActionsBlock,
  notice: Notice,
  image: ImageBlock,
  code: CodeObject,
  diff: Diff,
  question: Question,
  table: TableBlock,
  readout: Readout,
  html: Html,
  lamps: Lamps,
  scenes: Scenes,
};

export const BlockView = memo(function BlockView({ block }: { block: Block }) {
  const Render = registry[block.kind];
  return Render ? <Render b={block as B} /> : null;
});

export function BlockList({ blocks }: { blocks: Block[] }) {
  const known = blocks.filter((b) => b && registry[b.kind]);
  if (!known.length) return null;
  return (
    <View style={{ gap: sp(3) }}>
      {known.map((b, i) => (
        <BlockView key={i} block={b} />
      ))}
    </View>
  );
}
