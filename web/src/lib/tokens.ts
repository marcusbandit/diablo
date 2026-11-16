// Turns design/tokens.json into CSS custom properties: the dark scheme on :root, the light one
// under prefers-color-scheme and under data-theme="light".

type Group = Record<string, string>;
export type TokenFile = {
  sizes: Group;
  colors: Group;
  "type-colors": Group;
  "colors-light": Group;
  spacing: Group;
  motion: Group;
  schemes?: { dark?: Group; light?: Group };
};

/** Scheme colours the v1 palette lacks, borrowed from the v2 schemes. */
const BORROWED = ["fail", "overlay", "scrim", "on-accent"];

function vars(group: Group, prefix = ""): string[] {
  return Object.entries(group).map(([name, value]) => `  --${prefix}${name}: ${value};`);
}

function borrowed(own: Group, scheme: Group | undefined): Group {
  const out: Group = {};
  for (const name of BORROWED) {
    if (!(name in own) && scheme && name in scheme) out[name] = scheme[name];
  }
  return out;
}

export function tokensCss(t: TokenFile): string {
  const dark = { ...t.colors, ...t["type-colors"], ...borrowed(t.colors, t.schemes?.dark) };
  const light = { ...t["colors-light"], ...borrowed(t["colors-light"], t.schemes?.light) };
  const spacing = Object.fromEntries(
    Object.entries(t.spacing).map(([name, value]) => [name === "reading" ? "reading-width" : name, value]),
  );
  const root = [
    ":root {",
    ...vars(t.sizes, "fs-"),
    ...vars(spacing),
    ...vars(t.motion),
    ...vars(dark),
    "  color-scheme: dark;",
    "}",
  ];
  const lightBody = [...vars(light), "  color-scheme: light;"];
  return [
    ...root,
    '@media (prefers-color-scheme: light) {',
    '  :root:not([data-theme="dark"]) {',
    ...lightBody.map((line) => "  " + line),
    "  }",
    "}",
    ':root[data-theme="light"] {',
    ...lightBody,
    "}",
    "",
  ].join("\n");
}
