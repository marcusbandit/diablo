import type { Block } from "@diablo/hub";
import { memo, useEffect, useRef, useState, type ComponentRef } from "react";
import { ActivityIndicator, Animated, Easing, FlatList, View } from "react-native";

import { BlockList } from "../blocks/Blocks";
import { Markdown } from "../md/Markdown";
import { model, type Turn } from "../model";
import type { Shown } from "../store/store";
import { fonts, sp, useTheme } from "../theme";
import { Icon } from "../ui/Icon";
import { Button, Label, Reading } from "../ui/parts";

type Row = { kind: "message"; key: string; m: Shown } | { kind: "day"; key: string; label: string } | { kind: "turn"; key: string; turn: Turn };

const pad = (n: number) => String(n).padStart(2, "0");
const clock = (ts: number) => {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function dayLabel(ts: number, today = new Date()): string {
  const d = new Date(ts * 1000);
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** Newest first, as the inverted list wants it, with a day line above each day's first message. */
export function rowsFor(messages: Shown[], turn: Turn | undefined): Row[] {
  const rows: Row[] = [];
  if (turn) rows.push({ kind: "turn", key: "turn", turn });
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    rows.push({ kind: "message", key: m.key, m });
    const prev = messages[i - 1];
    const label = dayLabel(m.ts);
    if (!prev || dayLabel(prev.ts) !== label) rows.push({ kind: "day", key: `day-${m.key}`, label });
  }
  return rows;
}

function objectsOf(m: Shown): Block[] {
  const objects = m.meta?.objects;
  return Array.isArray(objects) ? (objects as Block[]).filter((o) => o && typeof o.kind === "string") : [];
}

const Mine = memo(function Mine({ m }: { m: Shown }) {
  const { c } = useTheme();
  return (
    <View style={{ alignItems: "flex-end", gap: sp(1), paddingLeft: sp(10) }}>
      <View style={{ backgroundColor: c.overlay, borderWidth: 1, borderColor: m.state === "failed" ? c.fail : c.line, paddingHorizontal: sp(3.5), paddingVertical: sp(2.5) }}>
        <Reading selectable>{m.text}</Reading>
      </View>
      {m.state === "sent" ? (
        <Label color={c.faint}>{clock(m.ts)}</Label>
      ) : m.state === "queued" ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
          <Icon name="queued" size={14} color={c.faint} />
          <Label color={c.faint}>Queued</Label>
        </View>
      ) : (
        <View style={{ flexDirection: "row", alignItems: "center", gap: sp(2) }}>
          <Label color={c.fail}>Not sent</Label>
          <Button label="Retry" kind="quiet" icon="retry" onPress={() => m.clientId && model.retry(m.clientId)} />
          <Button label="Discard" kind="quiet" onPress={() => m.clientId && model.discard(m.clientId)} />
        </View>
      )}
    </View>
  );
});

const His = memo(function His({ m }: { m: Shown }) {
  const { c } = useTheme();
  const objects = objectsOf(m);
  return (
    <View style={{ gap: sp(3) }}>
      {m.text.trim() ? <Markdown text={m.text} /> : null}
      <BlockList blocks={objects} />
      <Label color={c.faint}>{clock(m.ts)}</Label>
    </View>
  );
});

function Working({ turn }: { turn: Turn }) {
  const { c } = useTheme();
  const pulse = useRef(new Animated.Value(0.4)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 700, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.4, duration: 700, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);
  const text = turn.state === "doing" ? turn.step || turn.tool || "Working" : "Thinking";
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: sp(3), paddingVertical: sp(1) }}>
      <Animated.View style={{ width: 10, height: 10, backgroundColor: c.accent, opacity: pulse }} />
      <Label color={c.muted} numberOfLines={2} style={{ flex: 1 }}>
        {text}
      </Label>
    </View>
  );
}

function Day({ label }: { label: string }) {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: sp(3), paddingVertical: sp(2) }}>
      <View style={{ flex: 1, height: 1, backgroundColor: c.line }} />
      <Label style={{ fontFamily: fonts.brand }}>{label}</Label>
      <View style={{ flex: 1, height: 1, backgroundColor: c.line }} />
    </View>
  );
}

/** A section's conversation, newest at the bottom, scrolled natively. */
export function Messages({ messages, turn, section, footer }: { messages: Shown[]; turn?: Turn; section: string; footer?: React.ReactNode }) {
  const rows = rowsFor(messages, turn);
  const [more, setMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const list = useRef<ComponentRef<typeof FlatList<Row>>>(null);
  useEffect(() => {
    setMore(true);
    list.current?.scrollToOffset({ offset: 0, animated: false });
  }, [section]);
  // His own new line brings the conversation back to the bottom, wherever he had scrolled.
  const newest = messages[messages.length - 1];
  useEffect(() => {
    if (newest?.role === "user") list.current?.scrollToOffset({ offset: 0, animated: true });
  }, [newest?.key, newest?.role]);
  const older = async () => {
    if (!more || loading) return;
    setLoading(true);
    const got = await model.older(section);
    setMore(got);
    setLoading(false);
  };
  return (
    <FlatList
      ref={list}
      inverted
      data={rows}
      keyExtractor={(r) => r.key}
      renderItem={({ item }) =>
        item.kind === "day" ? <Day label={item.label} /> : item.kind === "turn" ? <Working turn={item.turn} /> : item.m.role === "user" ? <Mine m={item.m} /> : <His m={item.m} />
      }
      contentContainerStyle={{ paddingHorizontal: sp(4), paddingVertical: sp(3), gap: sp(4) }}
      onEndReached={older}
      onEndReachedThreshold={0.4}
      ListFooterComponent={loading ? <ActivityIndicator style={{ margin: sp(3) }} /> : undefined}
      ListHeaderComponent={footer ? <>{footer}</> : undefined}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="interactive"
      windowSize={9}
      removeClippedSubviews={false}
    />
  );
}
