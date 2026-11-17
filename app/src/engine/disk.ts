/**
 * Where the app keeps its copy of Diablo: named strings. SQLite on the phone, IndexedDB in the
 * browser, a map in the tests.
 */
export interface Disk {
  read(key: string): Promise<string | null>;
  write(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
}

/** The device token. The phone's keystore, the browser's storage, a map in the tests. */
export interface Secrets {
  get(): Promise<string | null>;
  set(token: string | null): Promise<void>;
}

export function memoryDisk(seed: Record<string, string> = {}): Disk & { data: Map<string, string> } {
  const data = new Map(Object.entries(seed));
  return {
    data,
    read: async (key) => data.get(key) ?? null,
    write: async (key, value) => void data.set(key, value),
    remove: async (key) => void data.delete(key),
  };
}

export function memorySecrets(token: string | null = null): Secrets {
  let held = token;
  return {
    get: async () => held,
    set: async (next) => void (held = next),
  };
}
