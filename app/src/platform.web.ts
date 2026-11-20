// The browser's side of the engine: IndexedDB for the store and the browser's own fetch. The
// hub serves this build at /app, so the hub is the page's own origin unless Metro was told
// another one for development.
import type { Disk, Secrets } from "./engine/disk";
import type { EngineOptions } from "./engine/engine";

const TOKEN = "diablo-token";

function indexedDisk(name: string): Disk {
  let opened: Promise<IDBDatabase> | null = null;
  const db = () =>
    (opened ??= new Promise((resolve, reject) => {
      const req = indexedDB.open(name, 1);
      req.onupgradeneeded = () => req.result.createObjectStore("kv");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }));
  const run = <T,>(mode: IDBTransactionMode, work: (store: IDBObjectStore) => IDBRequest<T>) =>
    db().then(
      (d) =>
        new Promise<T>((resolve, reject) => {
          const req = work(d.transaction("kv", mode).objectStore("kv"));
          req.onsuccess = () => resolve(req.result);
          req.onerror = () => reject(req.error);
        }),
    );
  return {
    read: async (key) => (await run<unknown>("readonly", (s) => s.get(key))) as string | null ?? null,
    write: async (key, value) => void (await run("readwrite", (s) => s.put(value, key))),
    remove: async (key) => void (await run("readwrite", (s) => s.delete(key))),
  };
}

const storage: Secrets = {
  get: async () => {
    try {
      return window.localStorage.getItem(TOKEN);
    } catch {
      return null;
    }
  },
  set: async (token) => {
    try {
      if (token) window.localStorage.setItem(TOKEN, token);
      else window.localStorage.removeItem(TOKEN);
    } catch {
      // a private window keeps nothing
    }
  },
};

function hubs(): string[] {
  const dev = process.env.EXPO_PUBLIC_DIABLO_HUBS;
  if (dev) return dev.split(",").map((h: string) => h.trim()).filter(Boolean);
  return [""];
}

export function platform(): EngineOptions {
  return {
    disk: indexedDisk("diablo-next"),
    secrets: storage,
    fetch: (input, init) => fetch(input, init),
    hubs: hubs(),
    deviceName: "Browser",
    source: "web",
  };
}

/** The browser has no assistant. */
export async function assistLink(_current: string | null): Promise<{ hubs: string[]; token: string | null }> {
  return { hubs: [], token: null };
}

export const assistIntro = {
  seen: async () => true,
  mark: async () => undefined,
};
