import { useEffect, useRef, useState } from "react";
import { Animated, Easing, Image, Pressable, View } from "react-native";

import { useTheme } from "../theme";
import { FACES, type Mood } from "./faces";

/** One hue per thing he does, the same as the orb on the desktop (presence/Style.qml). */
export const HUES: Record<Mood, string> = {
  asleep: "#78838f",
  resting: "#78838f",
  listening: "#7fb3d4",
  expectant: "#ff4d2e",
  thinking: "#e0915a",
  working: "#e0915a",
  talking: "#6fc49a",
  proud: "#6fc49a",
  delighted: "#6fc49a",
  relieved: "#6fc49a",
  wink: "#a98fe0",
  surprised: "#a98fe0",
  smug: "#a98fe0",
  sorry: "#f29ba8",
  worried: "#f29ba8",
};

const BUSY: Mood[] = ["thinking", "working", "talking", "listening"];

/**
 * Diablo himself: his hand-drawn face, four frames a mood shown in shuffled order so the ink
 * boils like a cartoon, on a dark disc ringed in the hue of what he is doing. A new mood lands
 * with a small squash and settle; busy moods breathe.
 */
export function Face({ mood, size = 160, onPress }: { mood: Mood; size?: number; onPress?: () => void }) {
  const { c, dark } = useTheme();
  const frames = FACES[mood] ?? FACES.resting;
  const [frame, setFrame] = useState(0);
  const pop = useRef(new Animated.Value(1)).current;
  const breath = useRef(new Animated.Value(0)).current;
  const hue = HUES[mood] ?? HUES.resting;
  const busy = BUSY.includes(mood);
  const asleep = mood === "asleep";

  useEffect(() => {
    const id = setInterval(() => setFrame((f) => (f + 1 + Math.floor(Math.random() * (frames.length - 1))) % frames.length), asleep ? 600 : 250);
    return () => clearInterval(id);
  }, [frames.length, asleep]);

  useEffect(() => {
    pop.setValue(0.9);
    Animated.spring(pop, { toValue: 1, friction: 4, tension: 160, useNativeDriver: true }).start();
  }, [mood, pop]);

  useEffect(() => {
    if (!busy) {
      breath.stopAnimation();
      Animated.timing(breath, { toValue: 0, duration: 300, useNativeDriver: true }).start();
      return;
    }
    const half = mood === "talking" ? 260 : 900;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(breath, { toValue: 1, duration: half, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(breath, { toValue: 0, duration: half, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [busy, mood, breath]);

  const disc = Math.round(size * 0.82);
  const glowScale = breath.interpolate({ inputRange: [0, 1], outputRange: [1, 1.12] });
  return (
    <Pressable onPress={onPress} accessibilityRole="imagebutton" accessibilityLabel={`Diablo, ${mood}`} style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Animated.View
        style={{
          position: "absolute",
          width: disc,
          height: disc,
          borderRadius: disc / 2,
          backgroundColor: hue,
          opacity: asleep ? 0.06 : 0.16,
          transform: [{ scale: glowScale }],
        }}
      />
      <Animated.View style={{ transform: [{ scale: pop }], alignItems: "center", justifyContent: "center" }}>
        <View
          style={{
            width: disc * 0.88,
            height: disc * 0.88,
            borderRadius: disc,
            backgroundColor: dark ? "#0c0a0b" : c.invertBg,
            borderWidth: 1.5,
            borderColor: hue,
            opacity: asleep ? 0.85 : 1,
            alignItems: "center",
            justifyContent: "center",
            overflow: "hidden",
          }}
        >
          <Image source={frames[frame % frames.length]} style={{ width: disc * 1.04, height: disc * 1.04, tintColor: dark ? "#f1ebe4" : c.invertFg }} fadeDuration={0} />
        </View>
      </Animated.View>
    </Pressable>
  );
}

export type { Mood };
