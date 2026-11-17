/**
 * The one source for how Diablo's app looks: colours, type, spacing, corners and motion.
 * Phone and browser read it from here; the desktop overlay is meant to follow the same values.
 * Pictures of every screen: docs/app/design/. What each screen is for: docs/app-map.md.
 */

/** Dark only until someone besides Marcus uses the app. */
export const color = {
  bg: "#202326",
  raised: "#2c3035",
  line: "#33373c",
  text: "#eef0f2",
  sub: "#b9bec4",
  dim: "#8d939a",

  /** The mood block while nothing needs you. */
  calm: "#2e3338",
  calmInk: "#eef0f2",
  /** The mood block while something needs you. The only loud colour in the app. */
  attention: "#e0915a",
  attentionInk: "#22140a",
} as const;

/** What he is doing, in the orb's colours (presence/Style.qml). Shown as a thin line, never a fill. */
export const state = {
  resting: "#545d67",
  listening: "#7fb3d4",
  thinking: "#e0915a",
  speaking: "#6fc49a",
  trouble: "#f29ba8",
} as const;

export type State = keyof typeof state;

export const font = {
  regular: "BricolageGrotesque-Regular",
  medium: "BricolageGrotesque-Medium",
  heavy: "BricolageGrotesque-ExtraBold",
} as const;

/** Sizes in dp, each with its line height. */
export const type = {
  small: { fontSize: 12, lineHeight: 16 },
  sub: { fontSize: 13, lineHeight: 17 },
  body: { fontSize: 15, lineHeight: 19 },
  title: { fontSize: 22, lineHeight: 23 },
  head: { fontSize: 30, lineHeight: 30 },
  big: { fontSize: 56, lineHeight: 52 },
} as const;

/** Spacing steps of 4 dp: space(3) is 12. */
export const space = (n: number) => n * 4;

export const radius = {
  control: 6,
  field: 10,
} as const;

/** Durations in ms. Every animation also honours the system's reduce-motion setting. */
export const motion = {
  /** The mood colour spreading out from his face. */
  ink: 1100,
  /** Text and face colour following the block. */
  fade: 1000,
  /** One pass of the working line under the block. */
  sweep: 1400,
  /** Panes moving when the posture or the layout changes. */
  glide: 240,
} as const;

/** The faces he has, by state; each is a folder under assets/faces. */
export const faceFor: Record<State, string> = {
  resting: "resting",
  listening: "listening",
  thinking: "thinking",
  speaking: "talking",
  trouble: "sorry",
};
