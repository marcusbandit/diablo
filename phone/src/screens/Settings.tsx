import Constants from "expo-constants";
import { router } from "expo-router";
import { useState, type ReactNode } from "react";
import { ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { model } from "../model";
import { useLayout } from "../posture";
import { sp, useTheme } from "../theme";
import { useModel } from "../use";
import { Big, Button, IconButton, Label, Status, Title } from "../ui/parts";
import { NotifyControls } from "./NotifyPanel";
import { PairPanel, PairResult } from "./PairPanel";
import { hostOf } from "../host";

function Group({ title, children }: { title: string; children: ReactNode }) {
  const { c } = useTheme();
  return (
    <View style={{ gap: sp(3), paddingTop: sp(4), borderTopWidth: 1, borderTopColor: c.line }}>
      <Title color={c.accentInk}>{title}</Title>
      {children}
    </View>
  );
}

function ThisPhone() {
  const device = useModel((s) => s.device);
  const link = useModel((s) => s.link);
  const pairing = useModel((s) => s.pairing);
  const [again, setAgain] = useState(false);
  const refused = link.state === "refused";
  if (device && !refused && !again) {
    return (
      <View style={{ gap: sp(3) }}>
        <Status ok text={`Paired as ${device.name} with ${hostOf(device.hub)}.`} />
        {pairing.text && pairing.ok ? null : <PairResult />}
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(2) }}>
          <Button label="Pair again" onPress={() => setAgain(true)} />
          <Button label="Unpair this phone" kind="danger" onPress={() => void model.unpair()} />
        </View>
      </View>
    );
  }
  return (
    <View style={{ gap: sp(4) }}>
      <Status ok={false} text={refused ? "The hub no longer knows this phone. Pair it again." : device ? "Pairing again replaces this phone's key." : "This phone is not paired yet."} />
      <PairPanel />
      {again ? (
        <View style={{ flexDirection: "row" }}>
          <Button label="Keep the current pairing" kind="quiet" onPress={() => setAgain(false)} />
        </View>
      ) : null}
    </View>
  );
}

function Hubs() {
  const hubs = useModel((s) => s.hubs);
  const times = useModel((s) => s.hubTimes);
  const link = useModel((s) => s.link);
  return (
    <View style={{ gap: sp(3) }}>
      {hubs.map((h) => {
        const ms = times[h];
        const using = link.hub === h && link.state === "online";
        return (
          <View key={h} style={{ flexDirection: "row", alignItems: "center", gap: sp(3) }}>
            <View style={{ flex: 1 }}>
              <Title>{hostOf(h)}</Title>
              <Label>{ms == null ? (ms === null ? "not answering" : "not tried yet") : `${using ? "in use, " : ""}${ms} ms`}</Label>
            </View>
          </View>
        );
      })}
      <View style={{ flexDirection: "row" }}>
        <Button label="Check again" icon="retry" onPress={() => model.kick()} />
      </View>
    </View>
  );
}

/** Pairing, notifications and hubs: everything that changes without a new build. */
export function Settings() {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const layout = useLayout(false);
  const fold = layout.posture.fold;
  // Book fold: settings stay on the right half, clear of the seam.
  const left = layout.mode === "book" && fold ? fold.right : 0;
  return (
    <View style={{ flex: 1, backgroundColor: c.bg, paddingLeft: left }}>
      <View style={{ paddingTop: insets.top, flexDirection: "row", alignItems: "center", gap: sp(2), paddingHorizontal: sp(2), minHeight: 56 + insets.top }}>
        <IconButton icon="back" label="Back" onPress={() => router.back()} />
        <Big style={{ fontSize: 22 }}>Settings</Big>
      </View>
      <ScrollView contentContainerStyle={{ alignSelf: "center", width: "100%", maxWidth: 640, paddingHorizontal: sp(4), paddingBottom: insets.bottom + sp(8), gap: sp(6) }}>
        <Group title="This phone">
          <ThisPhone />
        </Group>
        <Group title="Notifications">
          <NotifyControls settings />
        </Group>
        <Group title="Hubs">
          <Hubs />
        </Group>
        <Group title="Introduction">
          <View style={{ flexDirection: "row" }}>
            <Button label="Show it again" onPress={() => router.push({ pathname: "/onboarding", params: { again: "1" } })} />
          </View>
        </Group>
        <Label>Diablo {Constants.expoConfig?.version ?? ""}</Label>
      </ScrollView>
    </View>
  );
}
