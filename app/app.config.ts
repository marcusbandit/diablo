import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ConfigContext, ExpoConfig } from "expo/config";

// Built side by side with the old app (phone/) under its own id until the cutover in #91,
// which moves this app onto com.marcusrosado.diablo and deletes phone/ and web/.
const BG = "#202326";

/**
 * This box's own values from ~/.config/diablo/local.env (deploy/local.env.example lists them).
 * The hub addresses are baked into the build from there, so no address lives in the repo.
 */
function localEnv(): Record<string, string> {
  const file = join(process.env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "diablo", "local.env");
  if (!existsSync(file)) return {};
  const out: Record<string, string> = {};
  for (const raw of readFileSync(file, "utf8").split("\n")) {
    const m = raw.trim().match(/^(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$/);
    if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
  return out;
}

const env = localEnv();
const pick = (key: string, fallback: string) => (process.env[key] || env[key] || fallback).replace(/\/+$/, "");

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: "Diablo next",
  slug: "diablo-next",
  version: "0.8.1",
  scheme: "diablo-next",
  orientation: "default",
  userInterfaceStyle: "dark",
  backgroundColor: BG,
  icon: "./assets/images/icon.png",
  android: {
    package: "com.marcusrosado.diablo.next",
    versionCode: 13,
    allowBackup: false,
    predictiveBackGestureEnabled: true,
    softwareKeyboardLayoutMode: "resize",
    backgroundColor: BG,
    adaptiveIcon: {
      foregroundImage: "./assets/images/adaptive-foreground.png",
      monochromeImage: "./assets/images/adaptive-monochrome.png",
      backgroundColor: "#F2F1EE",
    },
  },
  web: { bundler: "metro", output: "single" },
  plugins: [
    "expo-router",
    "expo-secure-store",
    "expo-sqlite",
    ["expo-splash-screen", { image: "./assets/images/splash.png", imageWidth: 160, backgroundColor: BG }],
    ["expo-build-properties", { android: { usesCleartextTraffic: true } }],
    "./plugins/with-activity",
  ],
  experiments: { typedRoutes: false },
  extra: {
    // The Pi first, then the standby, which answers only while the Pi is down. The standby has
    // no default: its address only ever comes from this box's own config.
    hubs: [pick("DIABLO_HUB_URL", "http://diablo-pi:8755"), pick("DIABLO_STANDBY_URL", "")].filter(Boolean),
  },
});
