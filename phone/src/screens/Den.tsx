import type { Block, Task } from "@diablo/hub";
import { router } from "expo-router";
import { memo, useEffect, useMemo, useRef } from "react";
import { Animated, Easing, Linking, Pressable, ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ActionChips, BlockView, registry } from "../blocks/Blocks";
import { BlockActionsContext, type BlockActions } from "../blocks/context";
import { headline, moodOf, shownTasks, since } from "../den";
import { MAIN, model } from "../model";
import { fonts, sp, useTheme, validAccent } from "../theme";
import { useModel } from "../use";
import { Face } from "../ui/Face";
import { Icon } from "../ui/Icon";
import { IconButton, Label, Mark, Title } from "../ui/parts";

/** The thin line that sweeps under work while it runs: the house work indicator, never a spinner. */
function Sweep({ color, width, live }: { color: string; width: number; live: boolean }) {
  const x = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!live) return;
    const loop = Animated.loop(Animated.timing(x, { toValue: 1, duration: 1400, easing: Easing.inOut(Easing.cubic), useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [x, live]);
  const bar = Math.max(48, width * 0.28);
  return (
    <View style={{ height: 2, overflow: "hidden" }}>
      <Animated.View style={{ width: bar, height: 2, backgroundColor: color, transform: [{ translateX: x.interpolate({ inputRange: [0, 1], outputRange: [-bar, width] }) }] }} />
    </View>
  );
}

function Heading({ title, count, color }: { title: string; count?: number; color?: string }) {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: "row", alignItems: "baseline", gap: sp(2), paddingHorizontal: sp(4), paddingTop: sp(6), paddingBottom: sp(2) }}>
      <Title style={{ fontSize: 20 }} color={color}>
        {title}
      </Title>
      {count ? <Title style={{ fontSize: 20 }} color={color ?? c.faint}>{count}</Title> : null}
    </View>
  );
}

const TaskRow = memo(function TaskRow({ t, width, flash, live }: { t: Task; width: number; flash: boolean; live: boolean }) {
  const { c } = useTheme();
  const glow = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!flash) return;
    glow.setValue(1);
    Animated.timing(glow, { toValue: 0, duration: 1600, easing: Easing.out(Easing.quad), useNativeDriver: false }).start();
  }, [flash, glow]);
  const finished = t.state === "done" || t.state === "failed";
  const mark = t.state === "waiting" ? c.accent : t.state === "done" ? c.ok : t.state === "failed" ? c.fail : t.state === "queued" ? c.faint : c.ink;
  const link = t.links.pr || t.links.issue || t.links.thread;
  const step = t.state === "waiting" ? t.step || "Waiting on you" : finished ? t.result || t.step : t.step || (t.state === "queued" ? "Queued" : "Working");
  const age = since(finished ? t.finished_at : t.updated_at);
  const bg = glow.interpolate({ inputRange: [0, 1], outputRange: ["rgba(0,0,0,0)", t.state === "failed" ? "rgba(244,112,143,0.22)" : "rgba(99,199,164,0.22)"] });
  return (
    <Animated.View style={{ backgroundColor: bg }}>
      <Pressable
        onPress={() => (link ? void Linking.openURL(link) : undefined)}
        android_ripple={{ color: c.line }}
        accessibilityRole={link ? "link" : undefined}
        accessibilityLabel={`${t.title}, ${t.state}`}
        style={{ flexDirection: "row", gap: sp(3), paddingHorizontal: sp(4), paddingVertical: sp(3), alignItems: "flex-start" }}
      >
        <View style={{ width: 20, height: 22, alignItems: "center", justifyContent: "center" }}>
          {t.state === "done" ? (
            <Icon name="check" size={18} color={c.ok} />
          ) : t.state === "failed" ? (
            <Icon name="error" size={18} color={c.fail} />
          ) : (
            <View style={{ width: 10, height: 10, backgroundColor: t.state === "queued" ? "transparent" : mark, borderWidth: 1.5, borderColor: mark }} />
          )}
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <Title numberOfLines={2} color={finished ? c.muted : c.ink}>
            {t.title}
          </Title>
          {step ? (
            <Label numberOfLines={2} color={t.state === "waiting" ? c.accentInk : c.muted} style={{ fontSize: 14 }}>
              {step}
            </Label>
          ) : null}
        </View>
        <View style={{ alignItems: "flex-end", gap: 4, paddingTop: 2 }}>
          {t.links.pr_number ? (
            <View style={{ borderWidth: 1, borderColor: c.line, paddingHorizontal: 6, paddingVertical: 1 }}>
              <Label color={c.ink}>#{t.links.pr_number}</Label>
            </View>
          ) : null}
          <Label color={c.faint}>{age}</Label>
        </View>
        {finished ? <IconButton icon="close" label="Put away" onPress={() => model.dismissTask(t.id)} /> : null}
      </Pressable>
      {t.state === "working" ? <Sweep color={c.accent} width={width} live={live} /> : <View style={{ height: 2 }} />}
    </Animated.View>
  );
});

function Owed({ width, live }: { width: number; live: boolean }) {
  const { c } = useTheme();
  const owed = useModel((s) => s.owed);
  const tasks = useModel((s) => s.tasks);
  const { waiting } = useMemo(() => shownTasks(tasks), [tasks]);
  const total = owed.length + waiting.length;
  if (!total) return null;
  return (
    <View>
      <Heading title="Needs you" count={total > 1 ? total : undefined} color={c.accentInk} />
      <View style={{ gap: sp(3), paddingHorizontal: sp(4) }}>
        {owed.map((q) => (
          <View key={q.id} style={{ maxWidth: width }}>
            <BlockView block={q as Block} />
          </View>
        ))}
      </View>
      {waiting.map((t) => (
        <TaskRow key={t.id} t={t} width={width} flash={false} live={live} />
      ))}
    </View>
  );
}

function Work({ width, live }: { width: number; live: boolean }) {
  const tasks = useModel((s) => s.tasks);
  const moment = useModel((s) => s.moment);
  const { active, finished } = useMemo(() => shownTasks(tasks), [tasks]);
  if (!active.length && !finished.length) return null;
  return (
    <View>
      <Heading title="Work" count={active.length || undefined} />
      {active.map((t) => (
        <TaskRow key={t.id} t={t} width={width} flash={false} live={live} />
      ))}
      {finished.map((t) => (
        <TaskRow key={t.id} t={t} width={width} flash={moment?.task === t.id} live={live} />
      ))}
    </View>
  );
}

function Today() {
  const panel = useModel((s) => s.panels[MAIN]);
  const { chips, cards } = useMemo(() => {
    const known = (panel?.blocks ?? []).filter((b) => b && registry[b.kind]);
    return {
      chips: known.filter((b) => b.kind === "actions").flatMap((b) => (Array.isArray(b.items) ? (b.items as Record<string, unknown>[]) : [])),
      cards: known.filter((b) => b.kind !== "actions"),
    };
  }, [panel]);
  if (!chips.length && !cards.length) return null;
  return (
    <View>
      <Heading title="Today" />
      <View style={{ gap: sp(3), paddingHorizontal: sp(4) }}>
        {chips.length ? <ActionChips items={chips as never[]} /> : null}
        {cards.map((b, i) => (
          <BlockView key={`${i}-${b.kind}`} block={b} />
        ))}
      </View>
    </View>
  );
}

function Places() {
  const { c } = useTheme();
  const sections = useModel((s) => s.sections);
  const unread = useModel((s) => s.unread);
  const turns = useModel((s) => s.turns);
  const shown = sections.filter((s) => !s.deleted && !s.hidden && s.id !== MAIN);
  if (!shown.length) return null;
  return (
    <View>
      <Heading title="Rooms" />
      {shown.map((s) => (
        <Pressable
          key={s.id}
          onPress={() => model.show("talk", s.id)}
          android_ripple={{ color: c.line }}
          accessibilityRole="button"
          accessibilityLabel={s.name}
          style={{ flexDirection: "row", alignItems: "center", gap: sp(3), paddingHorizontal: sp(4), paddingVertical: sp(2.5) }}
        >
          <Mark icon={s.icon} accent={s.accent} size={32} />
          <Title style={{ flex: 1 }} numberOfLines={1}>
            {s.name}
          </Title>
          {turns[s.id] ? <View style={{ width: 8, height: 8, backgroundColor: validAccent(s.accent, c.accent) }} /> : null}
          {(unread[s.id] ?? 0) > 0 ? (
            <View style={{ minWidth: 22, height: 22, paddingHorizontal: 6, backgroundColor: c.accent, alignItems: "center", justifyContent: "center" }}>
              <Label color={c.onAccent} style={{ fontWeight: "700" }}>
                {unread[s.id]}
              </Label>
            </View>
          ) : (
            <Icon name="forward" size={18} color={c.faint} />
          )}
        </Pressable>
      ))}
    </View>
  );
}

/** The bar at the foot of the den. It opens the conversation with the keyboard up. */
function Ask({ bottom }: { bottom: number }) {
  const { c } = useTheme();
  const unread = useModel((s) => s.unread[MAIN] ?? 0);
  const preview = useModel((s) => s.previews[MAIN] ?? "");
  return (
    <View style={{ borderTopWidth: 1, borderTopColor: c.line, backgroundColor: c.bg, paddingBottom: bottom }}>
      {preview ? (
        <Pressable onPress={() => model.show("talk", MAIN)} android_ripple={{ color: c.line }} style={{ flexDirection: "row", alignItems: "center", gap: sp(2), paddingHorizontal: sp(4), paddingTop: sp(3) }}>
          {unread > 0 ? <View style={{ width: 8, height: 8, backgroundColor: c.accent }} /> : null}
          <Label numberOfLines={1} style={{ flex: 1, fontFamily: fonts.reading, fontSize: 14 }} color={unread > 0 ? c.ink : c.muted}>
            {preview}
          </Label>
        </Pressable>
      ) : null}
      <Pressable
        onPress={() => {
          model.show("talk", MAIN);
          model.say("", false);
        }}
        accessibilityRole="button"
        accessibilityLabel="Tell Diablo"
        style={{ margin: sp(3), minHeight: 52, flexDirection: "row", alignItems: "center", paddingLeft: sp(4), paddingRight: sp(1.5), backgroundColor: c.raised, borderWidth: 1, borderColor: c.lineStrong }}
      >
        <Label style={{ flex: 1, fontFamily: fonts.reading, fontSize: 16 }} color={c.faint}>
          Tell Diablo
        </Label>
        <View style={{ width: 40, height: 40, backgroundColor: c.accent, alignItems: "center", justifyContent: "center" }}>
          <Icon name="send" size={22} color={c.onAccent} />
        </View>
      </Pressable>
    </View>
  );
}

/**
 * Home: Diablo himself, one line on where things stand, then what he needs from Marcus, the
 * work he runs, today, and the rooms. Color is attention: ember only on what is owed.
 */
export function Den({
  width,
  height,
  bottom,
  ask = true,
  atTop = true,
  live = true,
}: {
  width: number;
  height: number;
  bottom: number;
  ask?: boolean;
  atTop?: boolean;
  live?: boolean;
}) {
  const { c } = useTheme();
  const safe = useSafeAreaInsets();
  const top = atTop ? safe.top : 0;
  const link = useModel((s) => s.link.state);
  const turns = useModel((s) => s.turns);
  const tasks = useModel((s) => s.tasks);
  const owed = useModel((s) => s.owed);
  const moment = useModel((s) => s.moment);
  const d = { link, turns, tasks, owed, moment };
  const mood = moodOf(d);
  const line = headline(d);
  const faceSize = Math.round(Math.min(200, Math.max(132, width * 0.42), height * 0.26));
  const actions = useMemo<BlockActions>(
    () => ({
      say: (text, send) => {
        model.show("talk", MAIN);
        model.say(text, send);
      },
      act: (a) => void model.act(a, MAIN),
      answer: (q, a) => void model.answer(q, a, model.getState().owed.find((o) => o.id === q)?.section ?? MAIN),
      file: (id) => model.fileSource(id),
      open: (module) => model.openModule(module),
    }),
    [],
  );
  return (
    <BlockActionsContext.Provider value={actions}>
      <View style={{ width, height, backgroundColor: c.bg }}>
        <ScrollView contentContainerStyle={{ paddingTop: top, paddingBottom: sp(6) + (ask ? 0 : bottom) }}>
          <View style={{ flexDirection: "row", justifyContent: "flex-end", paddingHorizontal: sp(2), height: 48, alignItems: "center" }}>
            <IconButton icon="settings" label="Settings" onPress={() => router.push("/settings")} />
          </View>
          <View style={{ alignItems: "center", paddingHorizontal: sp(6), gap: sp(3), marginTop: -sp(4) }}>
            <Face mood={mood} size={faceSize} live={live} onPress={() => model.poke()} />
            <Title style={{ fontFamily: fonts.reading, fontWeight: "400", fontSize: 22, lineHeight: 30, textAlign: "center" }} accessibilityLiveRegion="polite">
              {line}
            </Title>
          </View>
          <Owed width={width - sp(8)} live={live} />
          <Work width={width} live={live} />
          <Today />
          <Places />
        </ScrollView>
        <View pointerEvents="none" style={{ position: "absolute", left: 0, right: 0, top: 0, height: top, backgroundColor: c.bg }} />
        {ask ? <Ask bottom={bottom} /> : null}
      </View>
    </BlockActionsContext.Provider>
  );
}
