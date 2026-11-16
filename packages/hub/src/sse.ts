import type { HubEvent } from "./types";

/**
 * Turns chunks of a server-sent event stream into events. Feed it text as it arrives, in any
 * chunk sizes; it calls onEvent once per complete `data:` line that holds a JSON object.
 * Comments (keepalives) and malformed lines are dropped.
 */
export function sseParser(onEvent: (event: HubEvent) => void): (chunk: string) => void {
  let pending = "";
  return (chunk: string) => {
    pending += chunk;
    let newline = pending.indexOf("\n");
    while (newline >= 0) {
      const line = pending.slice(0, newline).replace(/\r$/, "");
      pending = pending.slice(newline + 1);
      if (line.startsWith("data:")) {
        try {
          const value = JSON.parse(line.slice(5).trim());
          if (value && typeof value === "object" && typeof value.kind === "string") onEvent(value);
        } catch {
          // A line that is not JSON is not an event.
        }
      }
      newline = pending.indexOf("\n");
    }
  };
}
