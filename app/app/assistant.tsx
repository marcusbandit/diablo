import { router } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppState, BackHandler, Image, PermissionsAndroid, Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { assist, type AssistStatus } from "../modules/diablo-assist";
import { face } from "../src/faces";
import { MoodBlock } from "../src/MoodBlock";
import { assistIntro } from "../src/platform";
import { color, font, radius, space, state, type } from "../src/tokens";

type Step = "mic" | "assistant" | "overlay";
const ORDER: Step[] = ["mic", "assistant", "overlay"];

type Copy = { ask: string; done: string; act: string; faceAsk: string; fill: string; ink: string };

const COPY: Record<Step, Copy> = {
  mic: { ask: "Just talk. I listen first.", done: "I can hear you now, sir.", act: "Let me hear you", faceAsk: "listening", fill: state.listening, ink: "#0d1a22" },
  assistant: { ask: "Hold power, and I'm on any screen.", done: "Hold power and I'm there, sir.", act: "Open the assistant page", faceAsk: "expectant", fill: state.listening, ink: "#0d1a22" },
  overlay: { ask: "Let me stay on screen while I finish.", done: "I can float over your apps now.", act: "Allow drawing over apps", faceAsk: "delighted", fill: state.speaking, ink: "#0c1f15" },
};

/** The exact clicks on Android's side, with its own labels. */
const CLICKS: Partial<Record<Step, string[]>> = {
  assistant: ["Default digital assistant app", "Diablo next", "OK"],
  overlay: ["Allow display over other apps"],
};

function useStatus(): [AssistStatus, () => void] {
  const [now, setNow] = useState(assist.status());
  const read = useCallback(() => setNow(assist.status()), []);
  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => s === "active" && read());
    return () => sub.remove();
  }, [read]);
  return [now, read];
}

/** A small phone with an app on it and him floating at the bottom, the way it will look. */
function Preview({ step }: { step: Step }) {
  const glass = { backgroundColor: "rgba(18,20,24,0.66)", borderColor: "rgba(255,255,255,0.18)", borderWidth: 1 } as const;
  return (
    <View style={{ width: 150, height: 236, borderRadius: 20, backgroundColor: "#f6f4ef", alignSelf: "center", overflow: "hidden", padding: 12 }}>
      <View style={{ height: 10, borderRadius: 5, backgroundColor: "#e4e1da", marginBottom: 10 }} />
      <View style={{ height: 9, width: "80%", borderRadius: 3, backgroundColor: "#3a3a3a", marginBottom: 6 }} />
      <View style={{ height: 9, width: "55%", borderRadius: 3, backgroundColor: "#3a3a3a", marginBottom: 10 }} />
      <View style={{ height: 46, borderRadius: 6, backgroundColor: "#7cc0b0", marginBottom: 8 }} />
      {[92, 86, 90, 70].map((w, i) => (
        <View key={i} style={{ height: 5, width: `${w}%`, borderRadius: 2, backgroundColor: "#c9c5bd", marginBottom: 5 }} />
      ))}
      {step === "overlay" ? (
        <View style={{ position: "absolute", left: 12, right: 12, top: 96, gap: 16 }}>
          <Text style={[glass, { alignSelf: "flex-start", color: color.text, fontFamily: font.medium, fontSize: 10, paddingHorizontal: 7, paddingVertical: 3, borderRadius: 6 }]}>Client</Text>
          <Text style={[glass, { alignSelf: "flex-end", color: color.text, fontFamily: font.medium, fontSize: 10, paddingHorizontal: 7, paddingVertical: 3, borderRadius: 6 }]}>Server</Text>
        </View>
      ) : (
        <View style={[glass, { position: "absolute", left: 10, right: 10, bottom: 58, borderRadius: 10, paddingHorizontal: 8, paddingVertical: 6 }]}>
          <Text style={{ color: color.text, fontFamily: font.regular, fontSize: 10, lineHeight: 13 }}>{step === "mic" ? "Turn the lamp blue" : "Explain this to me"}</Text>
        </View>
      )}
      <View style={[glass, { position: "absolute", bottom: 12, alignSelf: "center", left: 54, width: 40, height: 40, borderRadius: 20, borderColor: COPY[step].fill, borderWidth: 2, alignItems: "center", justifyContent: "center" }]}>
        <Image source={face(step === "overlay" ? "talking" : "listening")} style={{ width: 28, height: 28, tintColor: color.text }} />
      </View>
    </View>
  );
}

/**
 * The assistant's introduction: his ears, the assistant role, drawing over apps. Each shows what
 * it gives first, opens Android's own page for it, and checks the result on the way back. Every
 * step can wait; the den keeps a quiet way back here.
 */
export default function Assistant() {
  const insets = useSafeAreaInsets();
  const [status, read] = useStatus();
  const steps = useMemo(() => ORDER.filter((s) => !status[s]), []); // the steps that were open when it started
  const list = steps.length ? steps : ORDER;
  const [at, setAt] = useState(0);
  const step = list[Math.min(at, list.length - 1)];
  const done = status[step];
  const copy = COPY[step];

  const leave = useCallback(() => {
    void assistIntro.mark();
    if (router.canGoBack()) router.back();
    else router.replace("/");
  }, []);
  const next = useCallback(() => (at + 1 < list.length ? setAt(at + 1) : leave()), [at, list.length, leave]);

  // Back goes back a step, and from the first step leaves the introduction.
  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (at > 0) setAt(at - 1);
      else leave();
      return true;
    });
    return () => sub.remove();
  }, [at, leave]);

  const act = async () => {
    if (done) return next();
    if (step === "mic") {
      await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO);
      read();
    } else if (step === "assistant") {
      assist.openAssistantSettings();
    } else {
      assist.openOverlaySettings();
    }
  };

  const mood = { fill: copy.fill, ink: copy.ink, face: done ? "delighted" : copy.faceAsk, line: null, headline: done ? copy.done : copy.ask };
  const clicks = done ? null : CLICKS[step];

  return (
    <View style={{ flex: 1, backgroundColor: color.bg }}>
      <StatusBar style="dark" />
      <MoodBlock mood={mood} label={`${at + 1} of ${list.length}`} height={190} inset={insets.top} />
      <View style={{ flex: 1, padding: space(3.5), paddingBottom: space(3.5) + insets.bottom, gap: space(4) }}>
        <View style={{ flex: 1, justifyContent: "center" }}>
          <Preview step={step} />
        </View>
        {clicks ? (
          <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "center", gap: space(1.5) }}>
            {clicks.map((c, i) => (
              <View key={c} style={{ flexDirection: "row", alignItems: "center", gap: space(1.5) }}>
                {i > 0 ? <Text style={{ color: color.dim, fontFamily: font.medium, ...type.sub }}>then</Text> : null}
                <Text style={{ color: color.text, fontFamily: font.medium, ...type.sub, backgroundColor: color.raised, borderRadius: radius.control, paddingHorizontal: space(2), paddingVertical: space(1) }}>{c}</Text>
              </View>
            ))}
          </View>
        ) : null}
        <Pressable onPress={() => void act()} style={{ backgroundColor: copy.fill, borderRadius: radius.field, paddingVertical: space(3), alignItems: "center" }}>
          <Text style={{ color: copy.ink, fontFamily: font.heavy, ...type.body }}>{done ? (at + 1 < list.length ? "Next" : "Done") : copy.act}</Text>
        </Pressable>
        {done ? null : (
          <Pressable onPress={next} style={{ alignItems: "center", paddingVertical: space(1.5) }}>
            <Text style={{ color: color.sub, fontFamily: font.medium, ...type.body }}>Later</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}
