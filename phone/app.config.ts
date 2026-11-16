import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ConfigContext, ExpoConfig } from "expo/config";

// Hub addresses are baked in at build time: the environment first, then local.env on this box,
// then the tailnet names. Real values never live in the repo.
function localEnv(): Record<string, string> {
  const file = join(process.env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "diablo", "local.env");
  if (!existsSync(file)) return {};
  const out: Record<string, string> = {};
  for (const raw of readFileSync(file, "utf8").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).replace(/^export\s+/, "").trim();
    out[key] = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
  }
  return out;
}

const env = localEnv();
const pick = (key: string, fallback: string) => (process.env[key] || env[key] || fallback).replace(/\/+$/, "");

const fonts = (dir: string) => [
  {
    fontFamily: "Libron",
    fontDefinitions: [
      { path: `${dir}/Libron-Regular.ttf`, weight: 400 },
      { path: `${dir}/Libron-Italic.ttf`, weight: 400, style: "italic" },
      { path: `${dir}/Libron-Bold.ttf`, weight: 700 },
      { path: `${dir}/Libron-BoldItalic.ttf`, weight: 700, style: "italic" },
    ],
  },
  {
    fontFamily: "Source Sans 3",
    path: `${dir}/SourceSans3.ttf`,
    fontDefinitions: [
      { weight: 400, axes: { wght: 400 } },
      { weight: 500, axes: { wght: 500 } },
      { weight: 600, axes: { wght: 600 } },
      { weight: 700, axes: { wght: 700 } },
    ],
  },
  {
    fontFamily: "Alegreya Sans",
    fontDefinitions: [
      { path: `${dir}/AlegreyaSans-Medium.ttf`, weight: 500 },
      { path: `${dir}/AlegreyaSans-Bold.ttf`, weight: 700 },
    ],
  },
  {
    fontFamily: "JetBrains Mono",
    fontDefinitions: [
      { path: `${dir}/JetBrainsMono-Regular.ttf`, weight: 400 },
      { path: `${dir}/JetBrainsMono-Bold.ttf`, weight: 700 },
    ],
  },
];

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: "Diablo",
  slug: "diablo",
  version: "0.7.0",
  scheme: "diablo",
  orientation: "default",
  userInterfaceStyle: "automatic",
  backgroundColor: "#131112",
  icon: "./assets/images/icon.png",
  android: {
    package: "com.marcusrosado.diablo",
    versionCode: 10,
    allowBackup: false,
    predictiveBackGestureEnabled: true,
    softwareKeyboardLayoutMode: "resize",
    backgroundColor: "#131112",
    adaptiveIcon: {
      foregroundImage: "./assets/images/adaptive-foreground.png",
      monochromeImage: "./assets/images/adaptive-monochrome.png",
      backgroundColor: "#F2F1EE",
    },
    permissions: ["android.permission.POST_NOTIFICATIONS"],
    blockedPermissions: ["android.permission.RECORD_AUDIO", "android.permission.SYSTEM_ALERT_WINDOW"],
  },
  plugins: [
    "expo-router",
    "expo-secure-store",
    "expo-sqlite",
    ["expo-camera", { recordAudioAndroid: false, cameraPermission: "Diablo scans pairing codes with the camera." }],
    ["expo-font", { android: { fonts: fonts("./assets/fonts") } }],
    [
      "expo-splash-screen",
      { image: "./assets/images/splash.png", imageWidth: 160, backgroundColor: "#f5f0e8", dark: { backgroundColor: "#131112" } },
    ],
    ["expo-build-properties", { android: { usesCleartextTraffic: true } }],
    "./plugins/with-activity",
  ],
  experiments: { typedRoutes: false },
  extra: {
    // The standby has no default: its address only ever comes from this box's own config.
    hubs: [pick("DIABLO_HUB_URL", "http://diablo-pi:8755"), pick("DIABLO_STANDBY_URL", "")].filter(Boolean),
  },
});
