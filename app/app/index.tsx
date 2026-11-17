import { StatusBar } from "expo-status-bar";
import { useRef, useState, type ComponentRef } from "react";
import { Pressable, ScrollView, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { doing, needsYou } from "../src/engine/derive";
import { MAIN, type Shown } from "../src/engine/engine";
import { mood } from "../src/mood";
import { MoodBlock } from "../src/MoodBlock";
import { color, font, radius, space, type, type State } from "../src/tokens";
import { engine, useEngine } from "../src/useEngine";

const LABEL: Record<State, string> = { resting: "Resting", listening: "Listening", thinking: "Thinking", speaking: "Speaking", trouble: "Trouble" };
const EMPTY: Shown[] = [];

function Chip({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={{ backgroundColor: color.raised, borderRadius: radius.control, paddingHorizontal: space(3), paddingVertical: space(2) }}>
      <Text style={{ color: color.text, fontFamily: font.medium, ...type.body }}>{label}</Text>
    </Pressable>
  );
}

/**
 * The den, as a frame: the mood block follows the hub, and the main conversation runs under it
 * as plain lines. Step 3 of #91 turns this into the den and Talk.
 */
export default function Den() {
  const insets = useSafeAreaInsets();
  const [draft, setDraft] = useState("");
  const scroll = useRef<ComponentRef<typeof ScrollView>>(null);
  const s = useEngine((x) => x);
  const lines = s.conversations[MAIN] ?? EMPTY;
  const now = doing(s, draft.length > 0);
  const m = mood(now, needsYou(s), s.link);

  const send = () => {
    const text = draft;
    setDraft("");
    void engine.send(text);
  };

  return (
    <View style={{ flex: 1, backgroundColor: color.bg }}>
      <StatusBar style={m.fill === color.attention ? "dark" : "light"} />
      <MoodBlock mood={m} label={LABEL[now]} inset={insets.top} />
      <ScrollView
        ref={scroll}
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: space(3.5), gap: space(3) }}
        onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: false })}
      >
        {s.pairing.confirm ? (
          <View style={{ gap: space(2) }}>
            <Text style={{ color: color.text, fontFamily: font.medium, ...type.body }}>Pair with {s.pairing.confirm.host}?</Text>
            <View style={{ flexDirection: "row", gap: space(3) }}>
              <Chip label="Pair" onPress={() => void engine.confirmHost(true)} />
              <Chip label="Not now" onPress={() => void engine.confirmHost(false)} />
            </View>
          </View>
        ) : null}
        {s.pairing.working ? <Text style={{ color: color.sub, fontFamily: font.regular, ...type.body }}>Pairing</Text> : null}
        {s.pairing.error ? <Text style={{ color: color.attention, fontFamily: font.regular, ...type.body }}>{s.pairing.error}</Text> : null}
        {lines.slice(-40).map((l) => (
          // A line the hub refused shows in the attention colour; a tap sends it again.
          <Pressable key={l.key} disabled={l.state !== "failed"} onPress={() => l.clientId && void engine.retry(l.clientId)}>
            <Text
              style={{
                color: l.state === "failed" ? color.attention : l.role === "user" ? color.sub : color.text,
                opacity: l.state === "queued" || l.state === "sending" ? 0.55 : 1,
                fontFamily: l.role === "user" ? font.medium : font.regular,
                ...type.body,
              }}
            >
              {l.text}
            </Text>
          </Pressable>
        ))}
      </ScrollView>
      <View style={{ padding: space(3.5), paddingBottom: space(3.5) + insets.bottom }}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          onSubmitEditing={send}
          submitBehavior="submit"
          returnKeyType="send"
          placeholder="Talk to Diablo"
          placeholderTextColor={color.dim}
          style={{ backgroundColor: color.raised, borderRadius: radius.field, paddingHorizontal: space(3), paddingVertical: space(2.5), color: color.text, fontFamily: font.regular, ...type.body }}
        />
      </View>
    </View>
  );
}
