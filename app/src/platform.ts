// The phone's side of the engine: SQLite for the store, the keystore for the token, expo/fetch
// so the event stream arrives as it is sent. The browser's side is platform.web.ts.
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as SecureStore from "expo-secure-store";
import { openDatabaseAsync, type SQLiteDatabase } from "expo-sqlite";
import { fetch as expoFetch } from "expo/fetch";

import type { Disk, Secrets } from "./engine/disk";
import type { EngineOptions } from "./engine/engine";
import type { Fetch } from "./engine/hub";

const TOKEN = "hub-token";

function sqliteDisk(name: string): Disk {
  let opened: Promise<SQLiteDatabase> | null = null;
  const db = () =>
    (opened ??= openDatabaseAsync(name).then(async (d) => {
      await d.execAsync("PRAGMA journal_mode = WAL; CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);");
      return d;
    }));
  return {
    read: async (key) => (await (await db()).getFirstAsync<{ value: string }>("SELECT value FROM kv WHERE key = ?", [key]))?.value ?? null,
    write: async (key, value) => {
      await (await db()).runAsync("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", [key, value]);
    },
    remove: async (key) => {
      await (await db()).runAsync("DELETE FROM kv WHERE key = ?", [key]);
    },
  };
}

const keystore: Secrets = {
  get: () => SecureStore.getItemAsync(TOKEN),
  set: (token) => (token ? SecureStore.setItemAsync(TOKEN, token) : SecureStore.deleteItemAsync(TOKEN)),
};

/** The hubs this build knows: Metro's EXPO_PUBLIC_DIABLO_HUBS for a dev hub, else app.config's. */
function hubs(): string[] {
  const dev = process.env.EXPO_PUBLIC_DIABLO_HUBS;
  if (dev) return dev.split(",").map((h: string) => h.trim()).filter(Boolean);
  const listed = (Constants.expoConfig?.extra as { hubs?: unknown } | undefined)?.hubs;
  return Array.isArray(listed) ? listed.filter((h): h is string => typeof h === "string" && h.length > 0) : [];
}

function deviceName(): string {
  const maker = Device.manufacturer ?? "";
  const model = Device.modelName ?? "Android phone";
  return maker && !model.toLowerCase().startsWith(maker.toLowerCase()) ? `${maker} ${model}` : model;
}

export function platform(): EngineOptions {
  return {
    disk: sqliteDisk("diablo-next.db"),
    secrets: keystore,
    fetch: expoFetch as unknown as Fetch,
    hubs: hubs(),
    deviceName: deviceName(),
    source: "phone",
  };
}

/** The hubs and this device's token, for the assistant, which talks to the hub on its own. */
export async function assistLink(current: string | null): Promise<{ hubs: string[]; token: string | null }> {
  const all = [current, ...hubs()].filter((h): h is string => !!h);
  return { hubs: [...new Set(all)], token: await SecureStore.getItemAsync(TOKEN) };
}

/** Whether the assistant introduction has been through once. */
export const assistIntro = {
  seen: async () => (await SecureStore.getItemAsync("assist-intro-seen")) === "1",
  mark: () => SecureStore.setItemAsync("assist-intro-seen", "1"),
};
