import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import { Pressable, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { mood } from "../src/mood";
import { MoodBlock } from "../src/MoodBlock";
import { color, font, radius, space, type, type State } from "../src/tokens";

const DOING: State[] = ["resting", "listening", "thinking", "speaking"];
const LABEL: Record<State, string> = { resting: "Resting", listening: "Listening", thinking: "Thinking", speaking: "Speaking", trouble: "Trouble" };

/**
 * The den, as a frame for now: the mood block and the input. Step 3 of #91 fills it with Needs
 * you, Work and the rooms. Until the hub is wired (step 2), a tap on the block steps through
 * what he can be doing and a long press raises or clears an ask, so the motion can be checked.
 */
export default function Den() {
  const insets = useSafeAreaInsets();
  const [doing, setDoing] = useState(0);
  const [asks, setAsks] = useState(0);
  const state = DOING[doing];

  return (
    <View style={{ flex: 1, backgroundColor: color.bg }}>
      <StatusBar style={asks ? "dark" : "light"} />
      <Pressable onPress={() => setDoing((d) => (d + 1) % DOING.length)} onLongPress={() => setAsks((a) => (a ? 0 : 2))}>
        <MoodBlock mood={mood(state, asks)} label={LABEL[state]} inset={insets.top} />
      </Pressable>
      <View style={{ flex: 1 }} />
      <View style={{ padding: space(3.5), paddingBottom: space(3.5) + insets.bottom }}>
        <TextInput
          placeholder="Talk to Diablo"
          placeholderTextColor={color.dim}
          style={{ backgroundColor: color.raised, borderRadius: radius.field, paddingHorizontal: space(3), paddingVertical: space(2.5), color: color.text, fontFamily: font.regular, ...type.body }}
        />
      </View>
    </View>
  );
}
