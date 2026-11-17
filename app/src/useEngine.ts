import { useEffect, useRef, useSyncExternalStore } from "react";
import { AppState } from "react-native";

import { Engine, type EngineState } from "./engine/engine";
import { platform } from "./platform";

/** The one engine the whole app shares. */
export const engine = new Engine(platform());

/** Starts the engine and follows the app in and out of the foreground. Mount once, at the root. */
export function useEngineLifecycle() {
  useEffect(() => {
    void engine.start();
    engine.setForeground(AppState.currentState !== "background");
    const sub = AppState.addEventListener("change", (s) => engine.setForeground(s === "active"));
    return () => sub.remove();
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
