import { normalizePairCode } from "@diablo/hub";
import { CameraView } from "expo-camera";
import { router } from "expo-router";
import { useEffect, useState } from "react";
import { Linking, Text, View } from "react-native";

import { model } from "../model";
import { fonts, sp, useTheme } from "../theme";
import { useModel } from "../use";
import { Button, Field, Reading, Status } from "../ui/parts";
import { hostOf } from "./Sections";

export function Step({ n, children }: { n: number; children: React.ReactNode }) {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: "row", gap: sp(3) }}>
      <Text style={{ fontFamily: fonts.titles, fontWeight: "700", fontSize: 16, lineHeight: 24, color: c.accentInk, width: 16 }}>{n}</Text>
      <Reading style={{ flex: 1 }}>{children}</Reading>
    </View>
  );
}

/** Whether a hub answers this phone at all, and what to do when none does. */
export function Reach() {
  const link = useModel((s) => s.link);
  const times = useModel((s) => s.hubTimes);
  const reached = Object.entries(times).find(([, ms]) => ms != null);
  if (link.state === "checking" && !reached) return <Status ok text="Looking for the hub" waiting />;
  if (reached) return <Status ok text={`${hostOf(reached[0])} answers in ${reached[1]} ms`} />;
  return (
    <View style={{ gap: sp(3) }}>
      <Status ok={false} text="No hub answers. Is Tailscale on?" />
      <Step n={1}>Open the Tailscale app and tap Connect.</Step>
      <Step n={2}>Come back here and tap Check again.</Step>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: sp(2) }}>
        <Button label="Open Tailscale" onPress={() => Linking.openURL("https://play.google.com/store/apps/details?id=com.tailscale.ipn").catch(() => undefined)} />
        <Button label="Check again" icon="retry" onPress={() => model.kick()} />
      </View>
    </View>
  );
}

/** The outcome of a pairing, or the question when a code names a hub this phone does not know. */
export function PairResult() {
  const pairing = useModel((s) => s.pairing);
  if (pairing.unknownHost) {
    return (
      <View style={{ gap: sp(3) }}>
        <Status ok={false} text={`This code points at ${hostOf(pairing.unknownHost.host)}, a hub this phone has not used. Only pair with a hub you run yourself.`} />
        <View style={{ flexDirection: "row", gap: sp(2) }}>
          <Button label="Pair with it" kind="primary" onPress={() => void model.confirmHost(true)} />
          <Button label="Cancel" onPress={() => void model.confirmHost(false)} />
        </View>
      </View>
    );
  }
  if (!pairing.text) return null;
  return <Status ok={pairing.ok ?? true} text={pairing.text} waiting={pairing.working} />;
}

/** Scans with Google's code scanner, no camera permission; the in-app camera when that is missing. */
export async function scanCode() {
  let sub: { remove: () => void } | null = null;
  try {
    sub = CameraView.onModernBarcodeScanned((event) => {
      sub?.remove();
      if (event.data) void model.pairFrom(event.data);
    });
    await CameraView.launchScanner({ barcodeTypes: ["qr"] });
  } catch {
    sub?.remove();
    router.push("/scan");
  }
}

/** Getting a code and handing it to the hub, shared by onboarding and settings. */
export function PairPanel() {
  const pairing = useModel((s) => s.pairing);
  const [code, setCode] = useState("");
  const valid = normalizePairCode(code) != null;
  useEffect(() => {
    if (pairing.ok) setCode("");
  }, [pairing.ok]);
  return (
    <View style={{ gap: sp(4) }}>
      <Reach />
      <Step n={1}>Ask Diablo for a code: send him /pair on Telegram, or choose Pair a device on the hub's web page.</Step>
      <View style={{ flexDirection: "row" }}>
        <Button label="Open Telegram" onPress={() => Linking.openURL("https://t.me/").catch(() => undefined)} />
      </View>
      <Step n={2}>Scan its QR code, or type the eight letters.</Step>
      <View style={{ flexDirection: "row" }}>
        <Button label="Scan QR code" icon="scan" kind="primary" disabled={pairing.working} onPress={() => void scanCode()} />
      </View>
      <View style={{ flexDirection: "row", gap: sp(2), alignItems: "center" }}>
        <Field
          value={code}
          onChangeText={(t) => setCode(t.toUpperCase().slice(0, 12))}
          placeholder="ABCD-EFGH"
          mono
          autoCapitalize="characters"
          autoCorrect={false}
          returnKeyType="go"
          onSubmitEditing={() => valid && void model.pairFrom(code)}
          style={{ flex: 1 }}
        />
        <Button label="Pair" kind="primary" disabled={!valid || pairing.working} busy={pairing.working} onPress={() => void model.pairFrom(code)} />
      </View>
      <PairResult />
    </View>
  );
}
