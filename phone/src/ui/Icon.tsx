import MaterialIcons from "@expo/vector-icons/MaterialIcons";
import type { ComponentProps } from "react";

type Glyph = ComponentProps<typeof MaterialIcons>["name"];

/**
 * Icons by name, the names the hub and the section list use. An unknown name gets a neutral
 * mark, so a hub that knows a newer name never breaks an older app.
 */
const byName: Record<string, Glyph> = {
  flame: "local-fire-department",
  fire: "local-fire-department",
  dumbbell: "fitness-center",
  fitness: "fitness-center",
  run: "directions-run",
  folder: "folder",
  code: "code",
  lightbulb: "lightbulb",
  lamp: "wb-incandescent",
  home: "home",
  alarm: "alarm",
  reminder: "notifications-active",
  calendar: "calendar-month",
  chat: "chat",
  ask: "question-answer",
  insights: "insights",
  trend: "trending-up",
  trophy: "emoji-events",
  done: "check-circle",
  stretch: "self-improvement",
  timer: "timer",
  clock: "schedule",
  off: "power-settings-new",
  dim: "bedtime",
  bright: "brightness-high",
  day: "light-mode",
  cosy: "weekend",
  sun: "brightness-5",
  add: "add",
  music: "music-note",
  food: "restaurant",
  school: "school",
  shopping: "shopping-cart",
  work: "work",
  star: "star",
  idea: "tips-and-updates",
  mail: "mail",
  usage: "data-usage",
  devices: "devices",
  machines: "dns",
  projects: "folder",
};

/** App chrome: names only this app uses. */
const chrome: Record<string, Glyph> = {
  settings: "settings",
  send: "arrow-upward",
  back: "arrow-back",
  menu: "menu",
  more: "expand-more",
  less: "expand-less",
  copy: "content-copy",
  scan: "qr-code-scanner",
  offline: "cloud-off",
  retry: "refresh",
  close: "close",
  check: "check",
  error: "error-outline",
  queued: "schedule",
  open: "open-in-new",
  panel: "dashboard",
  unchecked: "check-box-outline-blank",
  checked: "check-box",
  forward: "arrow-forward",
  bell: "notifications",
  link: "link",
};

export function glyphFor(name: string | undefined | null): Glyph {
  const key = (name ?? "").toLowerCase();
  return chrome[key] ?? byName[key] ?? "auto-awesome";
}

export const SECTION_ICONS = ["flame", "dumbbell", "home", "lightbulb", "folder", "code", "calendar", "music", "food", "school", "work", "star"];

export function Icon({ name, size = 20, color }: { name: string | undefined | null; size?: number; color: string }) {
  return <MaterialIcons name={glyphFor(name)} size={size} color={color} />;
}

export const iconFont = MaterialIcons.font;
