import { useEffect, useState } from "react";
import { useWindowDimensions } from "react-native";

import Posture, { type PostureSnapshot } from "../modules/diablo-posture";

export type Rect = { x: number; y: number; width: number; height: number };

export type Mode = "phone" | "wide" | "book" | "tabletop";

export type Layout = {
  mode: Mode;
  /** The sections list. On a phone it is a drawer: off screen until opened. */
  pane: Rect;
  drawer: boolean;
  chat: Rect;
  /** Tabletop: where the hinge runs across the chat, so the composer sits below it. */
  seam: { top: number; bottom: number } | null;
  /** Tabletop with room: the sections sit below the hinge on the left, the composer beside them. */
  controlsLeft: number;
};

/** Below this width the window is a phone screen; 600 dp is Material's compact limit. */
export const WIDE_DP = 600;

/**
 * Bent counts from about ten degrees off flat: the hinge angle sensor says so long before the
 * system's own half-opened state does. Nearly shut is the outer screen, not a bend.
 */
export function bent(p: PostureSnapshot): boolean {
  if (!p.fold) return false;
  if (p.angle != null) return p.angle < 170 && p.angle > 25;
  return p.fold.state === "half";
}

const paneWidth = (w: number) => Math.round(Math.min(Math.max(w * 0.32, 260), 360));

/**
 * Where the sections list and the chat go for a window and a hinge. One tree for every
 * posture: only these rectangles change, so a posture change moves lines and never swaps screens.
 */
export function layoutFor(width: number, height: number, posture: PostureSnapshot, drawerOpen: boolean): Layout {
  const f = posture.fold;
  if (f && bent(posture) && f.orientation === "vertical" && f.left > 160 && width - f.right > 160) {
    return {
      mode: "book",
      pane: { x: 0, y: 0, width: f.left, height },
      drawer: false,
      chat: { x: f.right, y: 0, width: width - f.right, height },
      seam: null,
      controlsLeft: 0,
    };
  }
  const wide = width >= WIDE_DP;
  const paneW = wide ? paneWidth(width) : Math.round(Math.min(320, width * 0.86));
  const tabletop = !!f && bent(posture) && f.orientation === "horizontal" && f.top > 160 && height - f.bottom > 120;
  const seam = tabletop && f ? { top: f.top, bottom: f.bottom } : null;
  if (wide && seam) {
    // Like a camera on a table: the conversation fills the top half; the sections and the
    // composer, the controls, share the bottom half.
    return {
      mode: "tabletop",
      pane: { x: 0, y: seam.bottom, width: paneW, height: height - seam.bottom },
      drawer: false,
      chat: { x: 0, y: 0, width, height },
      seam,
      controlsLeft: paneW,
    };
  }
  if (wide) {
    return {
      mode: "wide",
      pane: { x: 0, y: 0, width: paneW, height },
      drawer: false,
      chat: { x: paneW, y: 0, width: width - paneW, height },
      seam: null,
      controlsLeft: 0,
    };
  }
  return {
    mode: tabletop ? "tabletop" : "phone",
    pane: { x: drawerOpen ? 0 : -paneW - 8, y: 0, width: paneW, height: seam ? seam.top : height },
    drawer: true,
    chat: { x: 0, y: 0, width, height },
    seam,
    controlsLeft: 0,
  };
}

const flat: PostureSnapshot = { fold: null, angle: null, hasHinge: false };

/** The hinge as it is now, following every change. */
export function usePosture(): PostureSnapshot {
  const [snap, setSnap] = useState<PostureSnapshot>(() => Posture?.get() ?? flat);
  useEffect(() => {
    if (!Posture) return;
    const sub = Posture.addListener("onChange", setSnap);
    setSnap(Posture.get());
    return () => sub.remove();
  }, []);
  return snap;
}

export function useLayout(drawerOpen: boolean): Layout & { posture: PostureSnapshot } {
  const { width, height } = useWindowDimensions();
  const posture = usePosture();
  return { ...layoutFor(width, height, posture, drawerOpen), posture };
}
