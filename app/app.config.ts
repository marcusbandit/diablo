import type { ConfigContext, ExpoConfig } from "expo/config";

// Built side by side with the old app (phone/) under its own id until the cutover in #91,
// which moves this app onto com.marcusrosado.diablo and deletes phone/ and web/.
const BG = "#202326";

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: "Diablo next",
  slug: "diablo-next",
  version: "0.8.0",
  scheme: "diablo-next",
  orientation: "default",
  userInterfaceStyle: "dark",
  backgroundColor: BG,
  icon: "./assets/images/icon.png",
  android: {
    package: "com.marcusrosado.diablo.next",
    versionCode: 12,
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
    ["expo-splash-screen", { image: "./assets/images/splash.png", imageWidth: 160, backgroundColor: BG }],
    ["expo-build-properties", { android: { usesCleartextTraffic: true } }],
    "./plugins/with-activity",
  ],
  experiments: { typedRoutes: false },
});
