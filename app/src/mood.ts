import type { LinkState } from "./engine/engine";
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

/** What the headline says when the link, not the work, is the news. */
const LINK: Partial<Record<LinkState, string>> = {
  unpaired: "We haven't met on this phone, sir.",
  refused: "The hub stopped knowing this phone, sir.",
  offline: "I can't reach the hub, sir.",
};

/**
 * The block's colour means attention: calm while he works, orange only when something needs
 * you. What he is doing shows in his face and the thin line, never in the fill.
 *
 * Offline, what still needs you outranks the lost link: the asks were already there and stay
 * true. Unpaired or refused, nothing he holds can be trusted, so the link speaks first.
 */
export function mood(doing: State, needsYou: number, link: LinkState = "online"): Mood {
  const lost = link === "unpaired" || link === "refused";
  const asks = needsYou > 0 && !lost;
  const n = COUNT[needsYou] ?? `${needsYou} things`;
  const said = lost || (link === "offline" && !asks) ? LINK[link] : undefined;
  return {
    fill: asks ? color.attention : color.calm,
    ink: asks ? color.attentionInk : color.calmInk,
    face: faceFor[doing],
    // On the orange block every state colour drowns, so the line takes the block's ink there.
    line: doing === "resting" ? null : asks ? color.attentionInk : stateColor[doing],
    headline: said ?? (asks ? `${n} ${needsYou === 1 ? "needs" : "need"} you, sir.` : "All quiet, sir."),
  };
}
