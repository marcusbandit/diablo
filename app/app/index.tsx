import { StatusBar } from "expo-status-bar";
import { useRef, useState, type ComponentRef } from "react";
import { ScrollView, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { doing, needsYou } from "../src/engine/derive";
import { MAIN, type Shown } from "../src/engine/engine";
import { mood } from "../src/mood";
import { MoodBlock } from "../src/MoodBlock";
import { color, font, radius, space, type, type State } from "../src/tokens";
import { engine, useEngine } from "../src/useEngine";

const LABEL: Record<State, string> = { resting: "Resting", listening: "Listening", thinking: "Thinking", speaking: "Speaking", trouble: "Trouble" };
const EMPTY: Shown[] = [];

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
        {lines.slice(-40).map((l) => (
          <Text
            key={l.key}
            style={{
              color: l.role === "user" ? color.sub : color.text,
              opacity: l.state === "sent" ? 1 : 0.55,
              fontFamily: l.role === "user" ? font.medium : font.regular,
              ...type.body,
            }}
          >
            {l.text}
          </Text>
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
