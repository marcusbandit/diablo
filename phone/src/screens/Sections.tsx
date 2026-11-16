import type { Section } from "@diablo/hub";
import { router } from "expo-router";
import { FlatList, Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { MAIN, model } from "../model";
import { fonts, sp, useTheme, validAccent } from "../theme";
import { useModel } from "../use";
import { Icon } from "../ui/Icon";
import { Label, Mark, Title } from "../ui/parts";

function Row({ s, selected, onPick }: { s: Section; selected: boolean; onPick: (id: string) => void }) {
  const { c } = useTheme();
  const unread = useModel((st) => st.unread[s.id] ?? 0);
  const preview = useModel((st) => st.previews[s.id] ?? "");
  const turn = useModel((st) => st.turns[s.id]);
  return (
    <Pressable
      onPress={() => onPick(s.id)}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      accessibilityLabel={s.name}
      android_ripple={{ color: c.line }}
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: sp(3),
        paddingVertical: sp(3),
        paddingHorizontal: sp(4),
        backgroundColor: selected ? c.raised : "transparent",
        borderLeftWidth: 3,
        borderLeftColor: selected ? validAccent(s.accent, c.accent) : "transparent",
      }}
    >
      <Mark icon={s.icon} accent={s.accent} size={40} />
      <View style={{ flex: 1, gap: 2 }}>
        <Title numberOfLines={1}>{s.name}</Title>
        {turn || preview ? (
          <Label numberOfLines={1} color={turn ? c.accentInk : c.muted}>
            {turn ? (turn.state === "doing" ? turn.tool || turn.step || "Working" : "Thinking") : preview}
          </Label>
        ) : null}
      </View>
      {unread > 0 ? (
        <View style={{ minWidth: 22, height: 22, paddingHorizontal: 6, backgroundColor: c.accent, alignItems: "center", justifyContent: "center" }}>
          <Text style={{ fontFamily: fonts.titles, fontSize: 12, fontWeight: "700", color: c.onAccent }}>{unread > 99 ? "99+" : unread}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

/** Every section, the main one first. A drawer on a phone, a column beside the chat when there is room. */
export function SectionsPane({ width, height, onPicked, atTop = true }: { width: number; height: number; onPicked: () => void; atTop?: boolean }) {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const sections = useModel((s) => s.sections);
  const current = useModel((s) => s.current);
  const link = useModel((s) => s.link);
  const shown = sections.filter((s) => !s.deleted && (!s.hidden || s.id === MAIN));
  const pick = (id: string) => {
    model.select(id);
    onPicked();
  };
  return (
    <View style={{ width, height, backgroundColor: c.bg, borderRightWidth: 1, borderRightColor: c.line }}>
      <View style={{ paddingTop: (atTop ? insets.top : 0) + sp(3), paddingHorizontal: sp(4), paddingBottom: sp(3), flexDirection: "row", alignItems: "center" }}>
        <Text style={{ fontFamily: fonts.brand, fontWeight: "700", fontSize: 26, color: c.ink, flex: 1 }}>Diablo</Text>
        <Pressable
          onPress={() => router.push("/settings")}
          accessibilityRole="button"
          accessibilityLabel="Settings"
          android_ripple={{ color: c.line, borderless: true, radius: 22 }}
          style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center" }}
        >
          <Icon name="settings" size={22} color={c.ink} />
        </Pressable>
      </View>
      <FlatList data={shown} keyExtractor={(s) => s.id} renderItem={({ item }) => <Row s={item} selected={item.id === current} onPick={pick} />} />
      <View style={{ paddingHorizontal: sp(4), paddingTop: sp(2), paddingBottom: insets.bottom + sp(3), flexDirection: "row", alignItems: "center", gap: sp(2) }}>
        <View style={{ width: 8, height: 8, backgroundColor: link.state === "online" ? c.ok : link.state === "checking" ? c.faint : c.warn }} />
        <Label numberOfLines={1} style={{ flex: 1 }}>
          {link.state === "online"
            ? `${hostOf(link.hub)}${link.ms != null ? `, ${link.ms} ms` : ""}`
            : link.state === "checking"
              ? "Looking for the hub"
              : link.state === "offline"
                ? `Offline${link.queued ? `, ${link.queued} queued` : ""}`
                : link.state === "unpaired"
                  ? "Not paired"
                  : "Pair this phone again"}
        </Label>
      </View>
    </View>
  );
}

export const hostOf = (url: string | null) => {
  if (!url) return "";
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
};
