import { useEffect, useRef, useState } from "react";
import { type LayoutChangeEvent, View } from "react-native";
import Animated, {
  Easing,
  cancelAnimation,
  runOnJS,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";

import { face } from "./faces";
import type { Mood } from "./mood";
import { font, motion, space, type } from "./tokens";

const FACE = 84;
const INK_EASE = Easing.bezier(0.3, 0.6, 0.2, 1);

/**
 * His mood on top of every screen: his face and one big line on a block that is calm while he
 * works and turns orange when something needs you. A new colour spreads out from his face like
 * ink; what he is doing runs as a thin line along the bottom edge.
 */
export function MoodBlock({ mood, label, height = 150, inset = 0 }: { mood: Mood; label: string; height?: number; inset?: number }) {
  const reduce = useReducedMotion();
  const [size, setSize] = useState({ w: 0, h: height + inset });
  const [base, setBase] = useState(mood.fill);
  const [inkColor, setInkColor] = useState(mood.fill);
  const spread = useSharedValue(0);
  const shown = useRef(mood.fill);
  // The colour a running spread is carrying, so a change mid-spread can settle it first.
  const spreading = useRef<string | null>(null);

  // Face centre, where the ink starts, and the radius that reaches the far corner from there.
  const cx = size.w - space(2) - FACE / 2;
  const cy = inset + space(2) + FACE / 2;
  const r = Math.hypot(Math.max(cx, size.w - cx), Math.max(cy, size.h - cy));

  useEffect(() => {
    if (mood.fill === shown.current) return;
    shown.current = mood.fill;
    if (reduce || size.w === 0) {
      setBase(mood.fill);
      return;
    }
    const target = mood.fill;
    cancelAnimation(spread);
    if (spreading.current) setBase(spreading.current);
    spreading.current = target;
    setInkColor(target);
    spread.value = 0;
    spread.value = withTiming(1, { duration: motion.ink, easing: INK_EASE }, (done) => {
      if (done) runOnJS(settle)(target);
    });
  }, [mood.fill, reduce, size.w, spread]);

  function settle(target: string) {
    setBase(target);
    if (spreading.current === target) spreading.current = null;
  }

  const ink = useAnimatedStyle(() => ({ transform: [{ scale: spread.value }], opacity: spread.value === 0 ? 0 : 1 }));
  const inkText = useAnimatedStyle(() => ({ color: withTiming(mood.ink, { duration: motion.fade }) }), [mood.ink]);
  const inkFace = useAnimatedStyle(() => ({ tintColor: withTiming(mood.ink, { duration: motion.fade }) }), [mood.ink]);

  const onLayout = (e: LayoutChangeEvent) => setSize({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height });

  return (
    <View onLayout={onLayout} style={{ height: height + inset, overflow: "hidden", backgroundColor: base, paddingHorizontal: space(3.5), paddingTop: inset + space(3), paddingBottom: space(3) }}>
      <Animated.View
        pointerEvents="none"
        style={[{ position: "absolute", left: cx - r, top: cy - r, width: r * 2, height: r * 2, borderRadius: r, backgroundColor: inkColor }, ink]}
      />
      <Animated.Image
        source={face(mood.face)}
        style={[{ position: "absolute", right: space(2), top: inset + space(2), width: FACE, height: FACE }, inkFace]}
        accessibilityIgnoresInvertColors
      />
      <Animated.Text style={[{ fontFamily: font.medium, ...type.small }, inkText]}>{label}</Animated.Text>
      <Animated.Text style={[{ fontFamily: font.heavy, ...type.head, letterSpacing: -0.6, marginTop: "auto", maxWidth: "80%" }, inkText]}>
        {mood.headline}
      </Animated.Text>
      <WorkLine color={mood.line} width={size.w} reduce={reduce} />
    </View>
  );
}

/** A short bar sweeping along the block's bottom edge while he works. */
function WorkLine({ color, width, reduce }: { color: string | null; width: number; reduce: boolean }) {
  const x = useSharedValue(0);
  const seg = width * 0.4;

  useEffect(() => {
    cancelAnimation(x);
    if (!color || reduce || width === 0) return;
    x.value = -seg;
    x.value = withRepeat(withTiming(width, { duration: motion.sweep, easing: Easing.linear }), -1, false);
  }, [color, reduce, width, seg, x]);

  const style = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));
  if (!color) return null;
  return (
    <View pointerEvents="none" style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 3, overflow: "hidden" }}>
      {reduce ? (
        <View style={{ flex: 1, backgroundColor: color }} />
      ) : (
        <Animated.View style={[{ width: seg, height: 3, borderRadius: 2, backgroundColor: color }, style]} />
      )}
    </View>
  );
}

