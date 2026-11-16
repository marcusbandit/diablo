import { useFonts } from "expo-font";
import * as Linking from "expo-linking";
import { router, Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import * as SystemUI from "expo-system-ui";
import { useEffect, useMemo } from "react";
import { Modal, View, useColorScheme } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { model } from "../src/model";
import { PairResult } from "../src/screens/PairPanel";
import { dark, light, sp, ThemeContext, useTheme } from "../src/theme";
import { useModel } from "../src/use";
import { iconFont } from "../src/ui/Icon";
import { Big, Button } from "../src/ui/parts";

SplashScreen.preventAutoHideAsync().catch(() => undefined);

/** diablo://pair?host=..&code=.. pairs; diablo://section/<id> opens a section (from a notification). */
const handled = new Set<string>();

function handleLink(url: string | null) {
  if (!url || handled.has(url)) return;
  handled.add(url);
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return;
  }
  if (parsed.protocol !== "diablo:") return;
  const where = `${parsed.hostname}${parsed.pathname}`.replace(/^\/+/, "");
  if (where.startsWith("pair")) void model.pairFrom(url, true);
  else if (where.startsWith("section/")) {
    const id = decodeURIComponent(where.slice("section/".length));
    model.select(id);
    if (router.canDismiss()) router.dismissAll();
  }
}

/** A pairing that came in through a tapped link, over whatever is on screen until dismissed. */
function LinkDialog() {
  const { c } = useTheme();
  const pairing = useModel((s) => s.pairing);
  const shown = pairing.fromLink && (pairing.working || !!pairing.text || !!pairing.unknownHost);
  return (
    <Modal visible={shown} transparent animationType="fade" onRequestClose={() => model.dismissPairing()}>
      <View style={{ flex: 1, backgroundColor: c.scrim, alignItems: "center", justifyContent: "center", padding: sp(6) }}>
        <View style={{ width: "100%", maxWidth: 440, backgroundColor: c.bg, borderWidth: 1, borderColor: c.lineStrong, padding: sp(5), gap: sp(4) }}>
          <Big style={{ fontSize: 22 }}>Pairing this phone</Big>
          <PairResult />
          {!pairing.unknownHost ? (
            <View style={{ flexDirection: "row" }}>
              <Button label={pairing.working ? "Hide" : "Done"} kind="primary" onPress={() => model.dismissPairing()} />
            </View>
          ) : null}
        </View>
      </View>
    </Modal>
  );
}

export default function Root() {
  const scheme = useColorScheme();
  const theme = useMemo(() => (scheme === "light" ? { c: light, dark: false } : { c: dark, dark: true }), [scheme]);
  const ready = useModel((s) => s.ready);
  const [fontsLoaded] = useFonts(iconFont);
  const url = Linking.useLinkingURL();

  useEffect(() => {
    void model.start();
  }, []);
  useEffect(() => {
    SystemUI.setBackgroundColorAsync(theme.c.bg).catch(() => undefined);
  }, [theme]);
  useEffect(() => {
    if (ready && fontsLoaded) SplashScreen.hideAsync().catch(() => undefined);
  }, [ready, fontsLoaded]);
  useEffect(() => {
    if (ready) handleLink(url);
  }, [ready, url]);

  if (!ready || !fontsLoaded) return null;
  return (
    <ThemeContext.Provider value={theme}>
      <SafeAreaProvider>
        <StatusBar style={theme.dark ? "light" : "dark"} />
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: theme.c.bg }, animation: "fade_from_bottom" }} />
        <LinkDialog />
      </SafeAreaProvider>
    </ThemeContext.Provider>
  );
}
