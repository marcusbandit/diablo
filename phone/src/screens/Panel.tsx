import type { Block } from "@diablo/hub";
import { useMemo } from "react";
import { Pressable, ScrollView, View } from "react-native";

import { ActionChips, BlockView, registry } from "../blocks/Blocks";
import type { PanelState } from "../model";
import { sp, useTheme } from "../theme";
import { Icon } from "../ui/Icon";
import { Label, Title } from "../ui/parts";

const ago = (ts: number | null) => {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};

/** One line that stands for a closed panel: its main number, or its first title. */
function summary(blocks: Block[]): string {
  const goal = blocks.find((b) => b.kind === "goal");
  if (goal) return `${goal.title ?? ""} ${goal.value ?? ""}/${goal.max ?? ""}`.trim();
  const stats = blocks.find((b) => b.kind === "stats");
  const first = Array.isArray(stats?.items) ? (stats!.items as Record<string, unknown>[])[0] : null;
  if (first) return `${first.label ?? ""} ${first.value ?? ""}${first.unit ? ` ${first.unit}` : ""}`.trim();
  const titled = blocks.find((b) => typeof b.title === "string" && b.title);
  return typeof titled?.title === "string" ? titled.title : "";
}

/**
 * What Diablo knows for this section, above its chat: the main number first, the section's
 * buttons as one row of chips, then the cards. It folds to one line and scrolls inside itself.
 */
export function Panel({ panel, open, onToggle, maxHeight }: { panel: PanelState; open: boolean; onToggle: () => void; maxHeight: number }) {
  const { c } = useTheme();
  const { heroes, chips, cards } = useMemo(() => {
    const known = panel.blocks.filter((b) => b && registry[b.kind]);
    return {
      heroes: known.filter((b) => b.kind === "goal"),
      chips: known.filter((b) => b.kind === "actions").flatMap((b) => (Array.isArray(b.items) ? (b.items as Record<string, unknown>[]) : [])),
      cards: known.filter((b) => b.kind !== "goal" && b.kind !== "actions"),
    };
  }, [panel.blocks]);
  if (!heroes.length && !chips.length && !cards.length) return null;
  return (
    <View style={{ borderBottomWidth: 1, borderBottomColor: c.line, backgroundColor: c.bg }}>
      <Pressable
        onPress={onToggle}
        accessibilityRole="button"
        accessibilityLabel={open ? "Fold the panel" : "Open the panel"}
        android_ripple={{ color: c.line }}
        style={{ flexDirection: "row", alignItems: "center", gap: sp(2), paddingHorizontal: sp(4), minHeight: 40 }}
      >
        <Icon name="panel" size={16} color={c.muted} />
        {open ? <Label style={{ flex: 1 }}>{ago(panel.generated)}</Label> : <Title style={{ flex: 1, fontSize: 15 }} numberOfLines={1}>{summary(panel.blocks)}</Title>}
        <Icon name={open ? "less" : "more"} size={20} color={c.muted} />
      </Pressable>
      {open ? (
        <ScrollView style={{ maxHeight }} nestedScrollEnabled contentContainerStyle={{ paddingHorizontal: sp(4), paddingBottom: sp(4), gap: sp(3) }}>
          {heroes.map((b, i) => (
            <BlockView key={`g${i}`} block={b} />
          ))}
          {chips.length ? <ActionChips items={chips as never[]} /> : null}
          {cards.map((b, i) => (
            <BlockView key={`c${i}-${b.kind}`} block={b} />
          ))}
        </ScrollView>
      ) : null}
    </View>
  );
}
