import { createContext, useContext } from "react";

// Design language v2 from design/tokens.json ("schemes"), the same palette as the desktop app.
// Corners stay square everywhere.

export type Colors = {
  bg: string;
  raised: string;
  sunken: string;
  overlay: string;
  ink: string;
  muted: string;
  faint: string;
  line: string;
  lineStrong: string;
  accent: string;
  accentInk: string;
  onAccent: string;
  ok: string;
  warn: string;
  fail: string;
  invertBg: string;
  invertFg: string;
  scrim: string;
  tCode: string;
  tTable: string;
  tLights: string;
  tGit: string;
};

export const dark: Colors = {
  bg: "#131112",
  raised: "#1b1819",
  sunken: "#0c0a0b",
  overlay: "#262122",
  ink: "#f1ebe4",
  muted: "rgba(241, 235, 228, 0.68)",
  faint: "rgba(241, 235, 228, 0.57)",
  line: "rgba(241, 235, 228, 0.12)",
  lineStrong: "rgba(241, 235, 228, 0.38)",
  accent: "#ff4d2e",
  accentInk: "#ff8a70",
  onAccent: "#131112",
  ok: "#63c7a4",
  warn: "#e9b44c",
  fail: "#f4708f",
  invertBg: "#f1ebe4",
  invertFg: "#131112",
  scrim: "rgba(6, 4, 5, 0.6)",
  tCode: "#8b5cf6",
  tTable: "#12a58a",
  tLights: "#ffd166",
  tGit: "#3fb96f",
};

export const light: Colors = {
  bg: "#f5f0e8",
  raised: "#fcf9f4",
  sunken: "#ebe4d9",
  overlay: "#ffffff",
  ink: "#1a1516",
  muted: "rgba(26, 21, 22, 0.72)",
  faint: "rgba(26, 21, 22, 0.64)",
  line: "rgba(26, 21, 22, 0.14)",
  lineStrong: "rgba(26, 21, 22, 0.49)",
  accent: "#d6331a",
  accentInk: "#ae250e",
  onAccent: "#ffffff",
  ok: "#17664d",
  warn: "#7a5200",
  fail: "#af1c43",
  invertBg: "#1a1516",
  invertFg: "#f5f0e8",
  scrim: "rgba(26, 21, 22, 0.35)",
  tCode: "#7c4ddb",
  tTable: "#0e7d68",
  tLights: "#a37200",
  tGit: "#23824c",
};

/** Libron for reading, Source Sans 3 for titles and controls, JetBrains Mono for code. */
export const fonts = {
  reading: "Libron",
  titles: "Source Sans 3",
  brand: "Alegreya Sans",
  mono: "JetBrains Mono",
};

export const size = { small: 13, normal: 16, large: 26 };

export const sp = (n: number) => n * 4;

export type Theme = { c: Colors; dark: boolean };

export const ThemeContext = createContext<Theme>({ c: dark, dark: true });

export const useTheme = () => useContext(ThemeContext);

/** #rrggbb with an alpha, for tints of a section's accent. */
export function tint(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return `rgba(255, 77, 46, ${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export function validAccent(hex: string | undefined, fallback: string): string {
  return hex && /^#[0-9a-f]{6}$/i.test(hex) ? hex : fallback;
}
