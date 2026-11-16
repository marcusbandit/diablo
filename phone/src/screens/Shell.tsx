import { useFocusEffect } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { BackHandler, Keyboard, LayoutAnimation, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { MAIN, model } from "../model";
import { useLayout, type Layout } from "../posture";
import { useTheme } from "../theme";
import { useModel } from "../use";
import { ChatPane } from "./Chat";
import { Den } from "./Den";

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
 * The main screen in every posture: the den and one room's conversation. A phone shows one at
 * a time and slides between them, a wide screen puts them side by side, a book fold one on
 * each half, a tabletop fold the conversation above the hinge and the den and composer below. Changes glide; the first
 * frame does not.
 */
export function Shell() {
  const { c } = useTheme();
  const view = useModel((s) => s.view);
  const layout = useLayout(view === "den");
  const current = useModel((s) => s.current);
  const keyboard = useKeyboard();
  const insets = useSafeAreaInsets();
  const last = useRef<Layout | null>(null);

  if (last.current && !sameRects(last.current, layout)) glide();
  last.current = layout;

  const denShown = !layout.drawer || view === "den";
  const talkShown = !layout.drawer || view === "talk";
  useEffect(() => model.setLooking(talkShown), [talkShown]);

  // Back steps back: on a phone the conversation returns to the den; a side room returns to
  // the main conversation first. Only the den, or the main room beside it, leaves the app.
  useFocusEffect(
    useCallback(() => {
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        if (current !== MAIN) {
          model.select(MAIN);
          if (layout.drawer) model.show("den");
          return true;
        }
        if (layout.drawer && view === "talk") {
          Keyboard.dismiss();
          model.show("den");
          return true;
        }
        return false;
      });
      return () => sub.remove();
    }, [current, view, layout.drawer]),
  );

  const { pane, chat, seam } = layout;
  return (
    <View style={{ flex: 1, backgroundColor: c.bg }}>
      <View style={{ position: "absolute", left: pane.x, top: pane.y, width: pane.width, height: pane.height }}>
        <Den width={pane.width} height={pane.height} bottom={keyboard > 0 ? 0 : insets.bottom} ask={layout.drawer} atTop={pane.y <= 0} live={denShown} />
      </View>
      <View style={{ position: "absolute", left: chat.x, top: chat.y, width: chat.width, height: chat.height, elevation: layout.drawer ? 8 : 0 }}>
        <ChatPane
          width={chat.width}
          height={chat.height}
          seam={seam}
          controlsLeft={layout.controlsLeft}
          onMenu={layout.drawer ? () => { Keyboard.dismiss(); model.show("den"); } : null}
          showSettings={false}
          live={talkShown}
          keyboard={keyboard}
        />
      </View>
    </View>
  );
}
