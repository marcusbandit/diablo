import { useEffect, useRef, useSyncExternalStore } from "react";
import { AppState } from "react-native";

import { Engine, type EngineState } from "./engine/engine";
import { assist } from "../modules/diablo-assist";
import { assistLink, platform } from "./platform";

/** The one engine the whole app shares. */
export const engine = new Engine(platform());

/** Starts the engine and follows the app in and out of the foreground. Mount once, at the root. */
export function useEngineLifecycle() {
  useEffect(() => {
    void engine.start();
    engine.setForeground(AppState.currentState !== "background");
    const sub = AppState.addEventListener("change", (s) => engine.setForeground(s === "active"));
    // The assistant talks to the hub on its own: hand it the hub and the token whenever they change.
    let handed = "";
    const hand = () => {
      const s = engine.getState();
      const key = `${s.hub}|${s.device?.id ?? ""}|${s.link}`;
      if (!assist.available || key === handed || s.link === "starting") return;
      handed = key;
      void assistLink(s.hub).then(({ hubs, token }) => assist.configure(hubs, s.link === "unpaired" || s.link === "refused" ? null : token));
    };
    hand();
    const unsubscribe = engine.subscribe(hand);
    return () => {
      sub.remove();
      unsubscribe();
    };
  }, []);
}

/**
 * A slice of the engine's state; the component draws again when that slice changes. The slice
 * is worked out once per state, so a pick may build a new array or object; such a pick draws
 * again on every change of state.
 */
export function useEngine<T>(pick: (s: EngineState) => T): T {
  const held = useRef<{ state: EngineState; value: T } | null>(null);
  const read = () => {
    const state = engine.getState();
    if (held.current?.state !== state) held.current = { state, value: pick(state) };
    return held.current.value;
  };
  return useSyncExternalStore(engine.subscribe, read, read);
}
