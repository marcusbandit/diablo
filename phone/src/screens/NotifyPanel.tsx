import type { Section } from "@diablo/hub";
import { useState } from "react";
import { Linking, Text, View } from "react-native";

import { MAIN, model } from "../model";
import { fonts, size, sp, useTheme, validAccent } from "../theme";
import { useModel } from "../use";
import { Button, Label, Reading, Status, Title } from "../ui/parts";
import { Step } from "./PairPanel";

const SAMPLES: Record<string, string> = {
  diablo: "The handoff finished: the pairing fix is merged and the build is green.",
  workout: "Logged 5 sets of squats at 100 kg. Thursday: deadlifts, add 2.5 kg.",
  home: "The hallway lamp has been on since 08:10. Want it off?",
  projects: "Nightly report: every service came back after the reboot.",
};

const sample = (s: Section) => SAMPLES[s.id] ?? `Done with what you asked in ${s.name}. The details are in the thread.`;

export function openNotificationSettings() {
  Linking.sendIntent("android.settings.APP_NOTIFICATION_SETTINGS", [{ key: "android.provider.extra.APP_PACKAGE", value: "com.marcusrosado.diablo" }]).catch(() =>
    Linking.openSettings(),
  );
}

/** What his notifications look like, from his real sections and last replies where there are some. */
export function NotifyPreview() {
  const { c } = useTheme();
  const sections = useModel((s) => s.sections);
  const previews = useModel((s) => s.previews);
  const others = sections.filter((s) => s.id !== MAIN && !s.deleted && !s.hidden);
  const picks = [others.find((s) => previews[s.id]) ?? others[0], sections.find((s) => s.id === MAIN)].filter((s): s is Section => !!s).slice(0, 2);
  const shown = picks.length ? picks : [{ id: MAIN, name: "Diablo", accent: "#ff4d2e" } as Section];
  return (
    <View style={{ backgroundColor: c.sunken, padding: sp(2.5), gap: sp(2) }}>
      {shown.map((s, i) => {
        const accent = validAccent(s.accent, c.accent);
        return (
          <View key={s.id} style={{ backgroundColor: c.raised, padding: sp(3), flexDirection: "row", gap: sp(3) }}>
            <View style={{ flex: 1, gap: 4 }}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: sp(2) }}>
                <View style={{ width: 8, height: 8, backgroundColor: accent }} />
                <Label color={accent}>{`Diablo · ${s.name} · ${i === 0 ? "now" : "12 min"}`}</Label>
              </View>
              <Title>{s.name}</Title>
              <Reading numberOfLines={2} color={c.muted} style={{ fontSize: size.small + 1, lineHeight: 20 }}>
                {previews[s.id] || sample(s)}
              </Reading>
            </View>
            <View style={{ width: 36, height: 36, backgroundColor: accent, alignItems: "center", justifyContent: "center" }}>
              <Text style={{ fontFamily: fonts.titles, fontWeight: "700", color: "#131112", fontSize: 18 }}>{s.name.slice(0, 1).toUpperCase()}</Text>
            </View>
          </View>
        );
      })}
    </View>
  );
}

/** The switch, with the way back when Android blocks it. */
export function NotifyControls({ settings }: { settings?: boolean }) {
  const notify = useModel((s) => s.notify);
  const paired = useModel((s) => s.device != null);
  const [tested, setTested] = useState(false);
  const on = notify.enabled && notify.allowed;
  if (on) {
    return (
      <View style={{ gap: sp(3) }}>
        <Status ok text={paired ? "Notifications are on." : "Notifications are on. They start once this phone is paired."} />
        {tested ? <Reading>Sent. Pull down the shade: that is how his replies arrive.</Reading> : null}
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(2) }}>
          <Button
            label="Send a test"
            icon="bell"
            onPress={() => {
              model.testNotification();
              setTested(true);
            }}
          />
          {settings ? <Button label="Channels" onPress={openNotificationSettings} /> : null}
          {settings ? <Button label="Turn off" kind="quiet" onPress={() => void model.setNotifications(false)} /> : null}
        </View>
      </View>
    );
  }
  if (notify.blocked) {
    return (
      <View style={{ gap: sp(3) }}>
        <Status ok={false} text="Android is blocking notifications for Diablo." />
        <Step n={1}>Tap Open settings.</Step>
        <Step n={2}>Switch on All Diablo notifications.</Step>
        <Step n={3}>Come back here; it turns on by itself.</Step>
        <View style={{ flexDirection: "row" }}>
          <Button label="Open settings" onPress={openNotificationSettings} />
        </View>
      </View>
    );
  }
  return (
    <View style={{ flexDirection: "row" }}>
      <Button label="Turn on notifications" icon="bell" kind="primary" onPress={() => void model.setNotifications(true)} />
    </View>
  );
}
