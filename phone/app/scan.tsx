import { CameraView, useCameraPermissions } from "expo-camera";
import { router } from "expo-router";
import { useRef } from "react";
import { View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { model } from "../src/model";
import { sp, useTheme } from "../src/theme";
import { Big, Button, IconButton, Reading } from "../src/ui/parts";

/** The in-app scanner, for phones without Google's code scanner. */
export default function Scan() {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const [permission, ask] = useCameraPermissions();
  const done = useRef(false);
  return (
    <View style={{ flex: 1, backgroundColor: c.bg, paddingTop: insets.top }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: sp(2), paddingHorizontal: sp(2), minHeight: 56 }}>
        <IconButton icon="back" label="Back" onPress={() => router.back()} />
        <Big style={{ fontSize: 22 }}>Scan the pairing code</Big>
      </View>
      {permission?.granted ? (
        <CameraView
          style={{ flex: 1 }}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          onBarcodeScanned={({ data }) => {
            if (done.current || !data) return;
            done.current = true;
            router.back();
            void model.pairFrom(data);
          }}
        />
      ) : (
        <View style={{ padding: sp(6), gap: sp(4) }}>
          <View style={{ flexDirection: "row" }}>
            <Button label="Allow the camera" kind="primary" onPress={() => void ask()} />
          </View>
        </View>
      )}
    </View>
  );
}
