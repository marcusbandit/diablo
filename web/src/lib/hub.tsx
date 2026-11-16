import { Hub, HubError, type HubEvent } from "@diablo/hub";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type Link = "connecting" | "live" | "down";

type Listener = (event: HubEvent) => void;

type HubState = {
  hub: Hub;
  link: Link;
  /** Called with the reason when the hub no longer knows this browser. */
  signOut: (reason?: string) => void;
  subscribe: (listener: Listener) => () => void;
};

const HubContext = createContext<HubState | null>(null);

export function useHub(): HubState {
  const value = useContext(HubContext);
  if (!value) throw new Error("useHub outside HubProvider");
  return value;
}

/** Calls handler for every hub event while the component is mounted. */
export function useHubEvents(handler: Listener): void {
  const { subscribe } = useHub();
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => subscribe((e) => ref.current(e)), [subscribe]);
}

export function HubProvider({ token, onSignOut, children }: { token: string; onSignOut: (reason?: string) => void; children: ReactNode }) {
  const hub = useMemo(() => new Hub("", token), [token]);
  const [link, setLink] = useState<Link>("connecting");
  const listeners = useRef(new Set<Listener>());
  const signOut = useRef(onSignOut);
  signOut.current = onSignOut;

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const open = () => {
      if (stopped) return;
      source = new EventSource(hub.eventsUrl(30));
      source.onopen = () => setLink("live");
      source.onmessage = (e) => {
        let event: HubEvent;
        try {
          event = JSON.parse(e.data);
        } catch {
          return;
        }
        if (!event || typeof event.kind !== "string") return;
        for (const l of listeners.current) l(event);
      };
      source.onerror = () => {
        if (!source || source.readyState !== EventSource.CLOSED) {
          setLink("connecting");
          return;
        }
        // The browser gave up: a refusal or a hub that is down. Ask which.
        source = null;
        setLink("down");
        hub.whoami().then(
          () => {
            retry = setTimeout(open, 3000);
          },
          (err: unknown) => {
            if (err instanceof HubError && err.status === 403) signOut.current("revoked");
            else retry = setTimeout(open, 5000);
          },
        );
      };
    };
    open();
    return () => {
      stopped = true;
      clearTimeout(retry);
      source?.close();
    };
  }, [hub]);

  const value = useMemo<HubState>(
    () => ({
      hub,
      link,
      signOut: (reason) => signOut.current(reason),
      subscribe: (listener) => {
        listeners.current.add(listener);
        return () => listeners.current.delete(listener);
      },
    }),
    [hub, link],
  );
  return <HubContext.Provider value={value}>{children}</HubContext.Provider>;
}

export function errorText(err: unknown): string {
  if (err instanceof HubError) return err.message;
  return err instanceof Error ? err.message : String(err);
}
