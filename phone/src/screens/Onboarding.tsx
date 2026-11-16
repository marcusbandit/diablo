import type { Block, Section } from "@diablo/hub";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useRef, useState, type ComponentRef } from "react";
import { BackHandler, LayoutAnimation, ScrollView, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { BlockView } from "../blocks/Blocks";
import { MAIN, model } from "../model";
import { useLayout } from "../posture";
import { fonts, sp, useTheme } from "../theme";
import { useModel } from "../use";
import { Big, Button, Card, IconButton, Label, Mark, Reading, Status, Title } from "../ui/parts";
import { NotifyControls, NotifyPreview } from "./NotifyPanel";
import { PairPanel, PairResult } from "./PairPanel";

const PAGES = 4;

const SAMPLE_SECTIONS: Section[] = [
  { id: MAIN, name: "Diablo", icon: "flame", accent: "#ff4d2e", brief: "", position: 0, hidden: false, deleted: false, updated: 0 },
  { id: "workout", name: "Workout", icon: "dumbbell", accent: "#19b39a", brief: "", position: 1, hidden: false, deleted: false, updated: 0 },
  { id: "home", name: "Home", icon: "home", accent: "#2d81ff", brief: "", position: 2, hidden: false, deleted: false, updated: 0 },
  { id: "projects", name: "Projects", icon: "folder", accent: "#8b67f2", brief: "", position: 3, hidden: false, deleted: false, updated: 0 },
];

const SAMPLE_GOAL: Block = {
  kind: "goal",
  title: "Push-ups today",
  icon: "dumbbell",
  value: 40,
  max: 75,
  caption: "On pace, 35 to go",
  badges: [
    { icon: "flame", label: "5 day streak" },
    { icon: "calendar", label: "Day 8 of 30" },
  ],
};

function StepBars({ page }: { page: number }) {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
      {Array.from({ length: PAGES }, (_, i) => (
        <View key={i} style={{ width: i === page ? 28 : 12, height: 4, backgroundColor: i <= page ? c.accent : c.line }} />
      ))}
      <Label style={{ marginLeft: sp(2) }}>
        Step {page + 1} of {PAGES}
      </Label>
    </View>
  );
}

function SectionsPage() {
  const { c } = useTheme();
  const real = useModel((s) => s.sections);
  const sections = real.length > 1 ? real.filter((s) => !s.deleted && !s.hidden) : SAMPLE_SECTIONS;
  return (
    <>
      <Big>One Diablo, many sections</Big>
      <Reading color={c.muted}>
        Diablo is one brain with one memory. Telegram, the voice in the room and every section here feed it. A section only gives him a focus, so "log 20 push-ups" in
        Workout is a workout, and he still knows everything else.
      </Reading>
      <View style={{ backgroundColor: c.raised }}>
        {sections.slice(0, 5).map((s) => (
          <View key={s.id} style={{ flexDirection: "row", alignItems: "center", gap: sp(3), paddingHorizontal: sp(4), paddingVertical: sp(2.5) }}>
            <Mark icon={s.icon} accent={s.accent} size={36} />
            <Title>{s.name}</Title>
          </View>
        ))}
      </View>
      <Title style={{ marginTop: sp(2) }}>You talk, he shows</Title>
      <Reading color={c.muted}>Above each chat sits what he knows right now, like the push-up ring in Workout or the lamps in Home. Tap a card to log a set or switch a lamp.</Reading>
      <BlockView block={SAMPLE_GOAL} />
    </>
  );
}

function PairPage() {
  const { c } = useTheme();
  const device = useModel((s) => s.device);
  const pairing = useModel((s) => s.pairing);
  const rows: [string, string][] = [
    ["Diablo", "the same conversation as Telegram"],
    ["Sections", "each with its live panel"],
    ["Offline", "every conversation stays readable"],
  ];
  return (
    <>
      <Big>Let him know it's you</Big>
      <Reading color={c.muted}>Pair this phone once and it reads every section, talks to Diablo and keeps a copy of each conversation for when no hub is up.</Reading>
      <Card>
        <Label>ONCE PAIRED</Label>
        {rows.map(([name, line]) => (
          <View key={name} style={{ flexDirection: "row", alignItems: "center", gap: sp(3) }}>
            <Title style={{ width: 84 }}>{name}</Title>
            <Label style={{ flex: 1 }}>{line}</Label>
          </View>
        ))}
      </Card>
      <Label>
        It costs one code from Diablo. A code lasts ten minutes and works once. The phone gets its own key, sealed by Android's keystore, and /devices on Telegram locks it out any time.
      </Label>
      {device && pairing.ok !== false ? (pairing.text ? <PairResult /> : <Status ok text={`Paired as ${device.name}.`} />) : <PairPanel />}
    </>
  );
}

function NotifyPage() {
  const { c } = useTheme();
  return (
    <>
      <Big>Hear back while you're away</Big>
      <Reading color={c.muted}>Long work finishes later. With notifications on, his answer reaches you even when the app is closed.</Reading>
      <NotifyPreview />
      <Reading color={c.muted}>
        Each section is its own channel in its own colour, so you can mute one and keep another loud. Your own messages and the section you are reading never ring.
      </Reading>
      <Label>
        The phone keeps one quiet link to the hub open over Tailscale, and Android shows a small Listening notice while it does. No Google push: nothing leaves your tailnet.
      </Label>
      <NotifyControls />
    </>
  );
}

function ReadyPage() {
  const { c } = useTheme();
  return (
    <>
      <Big>Ready</Big>
      <Reading color={c.muted}>
        Everything you read here is kept on the phone. With no hub up, every section still opens with its history, and what you write waits until a hub answers.
      </Reading>
      <Reading color={c.muted}>Replies come as text, tables, code, pictures and live cards, the way Diablo sends them.</Reading>
    </>
  );
}

/**
 * First launch: what the app is, then pairing, then notifications, each showing what it gives
 * before it asks. Every step can be skipped; settings holds the same controls and opens this again.
 */
export function Onboarding({ again }: { again: boolean }) {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const [page, setPage] = useState(0);
  const scroll = useRef<ComponentRef<typeof ScrollView>>(null);
  const layout = useLayout(false);
  const fold = layout.posture.fold;

  const go = (next: number) => {
    setPage(next);
    scroll.current?.scrollTo({ y: 0, animated: false });
  };
  const leave = () => {
    if (again) router.back();
    else {
      model.finishOnboarding();
      router.replace("/");
    }
  };

  useFocusEffect(
    useCallback(() => {
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        if (page > 0) {
          go(page - 1);
          return true;
        }
        if (again) {
          router.back();
          return true;
        }
        return false;
      });
      return () => sub.remove();
    }, [page, again]),
  );

  // Book fold: the mark on the left half, the steps on the right. Tabletop: the steps above the
  // hinge, the buttons below it. Nothing sits on the seam.
  const book = layout.mode === "book" && fold;
  const table = layout.mode === "tabletop" && fold;
  const last = useRef(layout.mode);
  if (last.current !== layout.mode) {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    last.current = layout.mode;
  }

  return (
    <View style={{ flex: 1, backgroundColor: c.bg, flexDirection: "row" }}>
      <View style={{ width: book ? fold.left : 0, overflow: "hidden", alignItems: "center", justifyContent: "center", backgroundColor: c.raised }}>
        <Text style={{ fontFamily: fonts.brand, fontWeight: "700", fontSize: 44, color: c.ink }}>Diablo</Text>
      </View>
      <View style={{ width: book ? fold.right - fold.left : 0 }} />
      <View style={{ flex: 1, alignItems: "center" }}>
        <View style={{ flex: 1, width: "100%", maxWidth: 560, paddingTop: insets.top + sp(4), paddingBottom: insets.bottom + sp(4), paddingHorizontal: sp(6) }}>
          <View style={{ flexDirection: "row", alignItems: "center", minHeight: 44 }}>
            <View style={{ flex: 1 }}>
              <StepBars page={page} />
            </View>
            {again ? <IconButton icon="close" label="Close" onPress={() => router.back()} /> : null}
          </View>
          <ScrollView
            ref={scroll}
            style={table ? { maxHeight: fold.top - insets.top - sp(16) } : { flex: 1 }}
            contentContainerStyle={{ gap: sp(4), paddingTop: sp(6), paddingBottom: sp(6) }}
            keyboardShouldPersistTaps="handled"
          >
            {page === 0 ? <SectionsPage /> : page === 1 ? <PairPage /> : page === 2 ? <NotifyPage /> : <ReadyPage />}
          </ScrollView>
          <View style={{ height: table ? Math.max(0, fold.bottom - fold.top) : 0 }} />
          <View style={[{ flexDirection: "row", gap: sp(2), paddingTop: sp(3) }, table ? { flex: 1, alignItems: "flex-start" } : null]}>
            {page < PAGES - 1 ? (
              <>
                <Button label="Continue" kind="primary" onPress={() => go(page + 1)} />
                {page > 0 ? <Button label="Skip for now" kind="quiet" onPress={() => go(page + 1)} /> : null}
              </>
            ) : (
              <Button label={again ? "Done" : "Open Diablo"} kind="primary" onPress={leave} />
            )}
          </View>
        </View>
      </View>
    </View>
  );
}
