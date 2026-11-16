import { useSyncExternalStore } from "react";

import { model, type State } from "./model";

/** A slice of the model's state; the component draws again only when that slice changes. */
export function useModel<T>(pick: (s: State) => T): T {
  return useSyncExternalStore(model.subscribe, () => pick(model.getState()));
}
