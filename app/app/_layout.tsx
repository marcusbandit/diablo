import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import * as SystemUI from "expo-system-ui";
import { useEffect } from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { color, font } from "../src/tokens";

SplashScreen.preventAutoHideAsync().catch(() => undefined);

export default function Root() {
  const [loaded, failed] = useFonts({
    [font.regular]: require("../assets/fonts/BricolageGrotesque-Regular.ttf"),
    [font.medium]: require("../assets/fonts/BricolageGrotesque-Medium.ttf"),
    [font.heavy]: require("../assets/fonts/BricolageGrotesque-ExtraBold.ttf"),
  });

  useEffect(() => {
    SystemUI.setBackgroundColorAsync(color.bg).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (loaded || failed) SplashScreen.hideAsync().catch(() => undefined);
  }, [loaded, failed]);

  if (!loaded && !failed) return null;
  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: color.bg }}>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: color.bg }, animation: "fade" }} />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
