import { useFocusEffect } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { BackHandler, Keyboard, LayoutAnimation, Pressable, View } from "react-native";

import { MAIN, model } from "../model";
import { useLayout, type Layout } from "../posture";
import { useTheme } from "../theme";
import { useModel } from "../use";
import { ChatPane } from "./Chat";
import { SectionsPane } from "./Sections";

const glide = () => LayoutAnimation.configureNext(LayoutAnimation.create(240, LayoutAnimation.Types.easeInEaseOut, LayoutAnimation.Properties.scaleXY));

function sameRects(a: Layout, b: Layout) {
  const r = (x: Layout["pane"]) => `${x.x},${x.y},${x.width},${x.height}`;
  return a.mode === b.mode && r(a.pane) === r(b.pane) && r(a.chat) === r(b.chat) && a.seam?.top === b.seam?.top && a.controlsLeft === b.controlsLeft;
}

function useKeyboard(): number {
  const [h, setH] = useState(0);
  useEffect(() => {
    const show = Keyboard.addListener("keyboardDidShow", (e) => setH(e.endCoordinates.height));
    const hide = Keyboard.addListener("keyboardDidHide", () => setH(0));
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);
  return h;
}

/**
 * The main screen in every posture: the sections list and one section's chat. A phone keeps
 * the list in a drawer, a wide screen beside the chat, a book fold on the left half and the
 * chat on the right, a tabletop fold the composer below the hinge. Changes glide; the first
 * frame does not.
 */
export function Shell() {
  const { c } = useTheme();
  const [drawer, setDrawer] = useState(false);
  const layout = useLayout(drawer);
  const current = useModel((s) => s.current);
  const keyboard = useKeyboard();
  const last = useRef<Layout | null>(null);

  if (last.current && !sameRects(last.current, layout)) glide();
  last.current = layout;

  useEffect(() => {
    if (!layout.drawer && drawer) setDrawer(false);
  }, [layout.drawer, drawer]);

  // Back steps back: the drawer closes first, a section returns to the main one, and only the
  // main section with nothing open leaves the app.
  useFocusEffect(
    useCallback(() => {
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        if (drawer) {
          setDrawer(false);
          return true;
        }
        if (current !== MAIN) {
          model.select(MAIN);
          return true;
        }
        return false;
      });
      return () => sub.remove();
    }, [drawer, current]),
  );

  const { pane, chat, seam } = layout;
  const drawerShown = layout.drawer && drawer;
  return (
    <View style={{ flex: 1, backgroundColor: c.bg }}>
      <View style={{ position: "absolute", left: chat.x, top: chat.y, width: chat.width, height: chat.height }}>
        <ChatPane
          width={chat.width}
          height={chat.height}
          seam={seam}
          controlsLeft={layout.controlsLeft}
          onMenu={layout.drawer ? () => setDrawer(true) : null}
          showSettings={layout.drawer}
          keyboard={keyboard}
        />
      </View>
      <Pressable
        accessibilityLabel="Close the sections"
        onPress={() => setDrawer(false)}
        pointerEvents={drawerShown ? "auto" : "none"}
        style={{ position: "absolute", left: 0, top: 0, right: 0, bottom: 0, backgroundColor: c.scrim, opacity: drawerShown ? 1 : 0 }}
      />
      <View style={{ position: "absolute", left: pane.x, top: pane.y, width: pane.width, height: pane.height, elevation: layout.drawer ? 8 : 0 }}>
        <SectionsPane width={pane.width} height={pane.height} atTop={pane.y <= 0} onPicked={() => setDrawer(false)} />
      </View>
    </View>
  );
}
