import { color, faceFor, state as stateColor, type State } from "./tokens";

export type Mood = {
  /** The block's fill. */
  fill: string;
  /** Text and face colour on the block. */
  ink: string;
  /** The face folder to show. */
  face: string;
  /** The working line under the block, or null when he is resting. */
  line: string | null;
  /** The one big line on the den. */
  headline: string;
};

const COUNT = ["Nothing", "One thing", "Two things", "Three things", "Four things", "Five things"];

/**
 * The block's colour means attention: calm while he works, orange only when something needs
 * you. What he is doing shows in his face and the thin line, never in the fill.
 */
export function mood(doing: State, needsYou: number): Mood {
  const asks = needsYou > 0;
  const n = COUNT[needsYou] ?? `${needsYou} things`;
  return {
    fill: asks ? color.attention : color.calm,
    ink: asks ? color.attentionInk : color.calmInk,
    face: faceFor[doing],
    // On the orange block every state colour drowns, so the line takes the block's ink there.
    line: doing === "resting" ? null : asks ? color.attentionInk : stateColor[doing],
    headline: asks ? `${n} ${needsYou === 1 ? "needs" : "need"} you, sir.` : "All quiet, sir.",
  };
}
