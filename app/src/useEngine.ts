import { useEffect, useSyncExternalStore } from "react";
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

/** A slice of the engine's state; the component draws again only when that slice changes. */
export function useEngine<T>(pick: (s: EngineState) => T): T {
  return useSyncExternalStore(engine.subscribe, () => pick(engine.getState()));
}
