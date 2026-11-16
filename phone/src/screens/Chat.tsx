import * as Haptics from "expo-haptics";
import { router } from "expo-router";
import { useEffect, useMemo, useRef, useState, type ComponentRef } from "react";
import { Pressable, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { BlockActionsContext, type BlockActions } from "../blocks/context";
import { moodOf } from "../den";
import { Face } from "../ui/Face";
import { MAIN, model } from "../model";
import { fonts, size, sp, useTheme } from "../theme";
import { useModel } from "../use";
import { Icon } from "../ui/Icon";
import { IconButton, Label, Mark, Title } from "../ui/parts";
import { Messages } from "./Messages";
import { Panel } from "./Panel";

function Composer({ section, bottom }: { section: string; bottom: number }) {
  const { c } = useTheme();
  const [text, setText] = useState("");
  const input = useRef<ComponentRef<typeof TextInput>>(null);
  const draft = useModel((s) => s.draft);
  const name = useModel((s) => s.sections.find((x) => x.id === section)?.name ?? "Diablo");
  useEffect(() => {
    if (!draft || draft.section !== section) return;
    setText(draft.text);
    model.draftTaken();
    setTimeout(() => input.current?.focus(), 50);
  }, [draft, section]);
  const send = () => {
    if (!text.trim()) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
    model.send(text, section);
    setText("");
  };
  return (
    <View style={{ flexDirection: "row", alignItems: "flex-end", gap: sp(2), paddingHorizontal: sp(3), paddingTop: sp(2), paddingBottom: sp(2) + bottom, backgroundColor: c.bg, borderTopWidth: 1, borderTopColor: c.line }}>
      <TextInput
        ref={input}
        value={text}
        onChangeText={setText}
        placeholder={section === MAIN ? "Message Diablo" : `Message Diablo about ${name.toLowerCase()}`}
        placeholderTextColor={c.faint}
        selectionColor={c.accent}
        cursorColor={c.accent}
        multiline
        style={{
          flex: 1,
          minHeight: 48,
          maxHeight: 160,
          paddingHorizontal: sp(4),
          paddingTop: 12,
          paddingBottom: 12,
          backgroundColor: c.sunken,
          borderWidth: 1,
          borderColor: c.line,
          color: c.ink,
          fontFamily: fonts.reading,
          fontSize: size.normal,
          lineHeight: 22,
        }}
      />
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Send"
        onPress={send}
        disabled={!text.trim()}
        android_ripple={{ color: c.onAccent }}
        style={{ width: 48, height: 48, alignItems: "center", justifyContent: "center", backgroundColor: text.trim() ? c.accent : c.raised, borderWidth: 1, borderColor: text.trim() ? c.accent : c.line }}
      >
        <Icon name="send" size={22} color={text.trim() ? c.onAccent : c.faint} />
      </Pressable>
    </View>
  );
}

/** His face in the conversation's header, small, wearing the same mood as on the den. */
function HeadFace() {
  const link = useModel((s) => s.link.state);
  const turns = useModel((s) => s.turns);
  const tasks = useModel((s) => s.tasks);
  const owed = useModel((s) => s.owed);
  const moment = useModel((s) => s.moment);
  return <Face mood={moodOf({ link, turns, tasks, owed, moment })} size={44} onPress={() => model.poke()} />;
}

function LinkLine() {
  const { c } = useTheme();
  const link = useModel((s) => s.link);
  if (link.state === "online" || link.state === "checking") return null;
  const text =
    link.state === "offline"
      ? `Offline${link.queued ? `, ${link.queued} queued` : ""}`
      : link.state === "unpaired"
        ? "Not paired"
        : "The hub no longer knows this phone";
  return (
    <Pressable onPress={() => (link.state === "offline" ? model.kick() : router.push("/settings"))} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
      <Icon name="offline" size={14} color={c.warn} />
      <Label color={c.warn}>{text}</Label>
    </Pressable>
  );
}

/** One section's conversation: its header, its panel, the messages and the composer. */
export function ChatPane({
  width,
  height,
  seam,
  controlsLeft,
  onMenu,
  showSettings,
  keyboard,
}: {
  width: number;
  height: number;
  seam: { top: number; bottom: number } | null;
  controlsLeft: number;
  onMenu: (() => void) | null;
  showSettings: boolean;
  keyboard: number;
}) {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const current = useModel((s) => s.current);
  const sections = useModel((s) => s.sections);
  const messages = useModel((s) => s.messages);
  const turn = useModel((s) => s.turns[current]);
  const panel = useModel((s) => s.panels[current]);
  const open = useModel((s) => s.panelOpen[current] ?? true);
  const notice = useModel((s) => s.notice);
  const section = sections.find((s) => s.id === current) ?? sections[0];

  const actions = useMemo<BlockActions>(
    () => ({
      say: (text, send) => model.say(text, send),
      act: (a) => void model.act(a),
      answer: (q, a) => void model.answer(q, a),
      file: (id) => model.fileSource(id),
      open: (module) => model.openModule(module),
    }),
    [],
  );

  // Tabletop: everything above the hinge, the composer below it. The same three pieces in every
  // posture; only their heights change.
  const bottomInset = keyboard > 0 ? keyboard : insets.bottom;
  return (
    <BlockActionsContext.Provider value={actions}>
      <View style={{ width, height, backgroundColor: c.bg }}>
        <View style={seam ? { height: seam.top } : { flex: 1 }}>
          <View style={{ paddingTop: insets.top, borderBottomWidth: 1, borderBottomColor: c.line, backgroundColor: c.bg }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: sp(2), paddingHorizontal: sp(2), minHeight: 56 }}>
              {onMenu ? <IconButton icon="back" label="Back to the den" onPress={onMenu} /> : <View style={{ width: sp(2) }} />}
              {current === MAIN ? <HeadFace /> : <Mark icon={section.icon} accent={section.accent} size={32} />}
              <View style={{ flex: 1, paddingLeft: sp(1) }}>
                <Title style={{ fontSize: 18 }} numberOfLines={1}>
                  {section.name}
                </Title>
                {turn ? (
                  <Label color={c.accentInk} numberOfLines={1}>
                    {turn.state === "doing" ? turn.tool || turn.step || "Working" : "Thinking"}
                  </Label>
                ) : (
                  <LinkLine />
                )}
              </View>
              {showSettings ? <IconButton icon="settings" label="Settings" onPress={() => router.push("/settings")} /> : null}
            </View>
          </View>
          {panel ? <Panel panel={panel} open={open} onToggle={() => model.togglePanel(current)} maxHeight={Math.max(160, (seam ? seam.top : height) * 0.45)} /> : null}
          <View style={{ flex: 1 }}>
            <Messages messages={messages} turn={turn} section={current} />
          </View>
          {notice && notice.section === current ? (
            <View style={{ marginHorizontal: sp(4), marginBottom: sp(2), padding: sp(3), backgroundColor: c.overlay, borderLeftWidth: 3, borderLeftColor: notice.failed ? c.fail : c.ok }}>
              <Label color={c.ink}>{notice.text}</Label>
            </View>
          ) : null}
        </View>
        <View style={{ height: seam ? seam.bottom - seam.top : 0 }} />
        <View style={[seam ? { flex: 1 } : null, { paddingLeft: controlsLeft }]}>
          <Composer section={current} bottom={bottomInset} />
        </View>
      </View>
    </BlockActionsContext.Provider>
  );
}
