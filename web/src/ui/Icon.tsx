// Icon names as the hub sends them (docs/api.md, Block kinds). An unknown name draws a neutral
// square, as the docs ask.

const PATHS: Record<string, string> = {
  flame: "M12 3c1 3 5 5 5 10a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5 0 2 1 3 2 3-1-3 0-6 1-8.5z",
  dumbbell: "M3 9v6M6 6v12M18 6v12M21 9v6M6 12h12",
  run: "M13 4.5a1.5 1.5 0 1 0 0 .01M9 20l3-6 3 3v4M7 11l3-3h4l2 3 3 1M12 14l-1-5",
  lightbulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z",
  lamp: "M8 3h8l3 8H5l3-8zM12 11v8M8 21h8",
  home: "M3 11l9-7 9 7M5 9.5V20h14V9.5M10 20v-6h4v6",
  alarm: "M12 21a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM12 9v4l2.5 2.5M3 5l3-2M21 5l-3-2",
  calendar: "M4 6h16v15H4zM4 10h16M8 3v5M16 3v5",
  done: "M4 12.5l5 5L20 6.5",
  add: "M12 5v14M5 12h14",
  insights: "M4 20V10M10 20V4M16 20v-8M22 20H2",
  trend: "M3 17l6-6 4 4 8-8M15 7h6v6",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 3",
  off: "M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0",
  dim: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 3v2M12 19v2M3 12h2M19 12h2",
  bright: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
  cosy: "M4 14c0-4 3-8 8-8s8 4 8 8M2 18h20M7 14h10",
  mail: "M3 5h18v14H3zM3 6l9 7 9-7",
  inbox: "M3 13l3-8h12l3 8v6H3zM3 13h5l1 2h6l1-2h5",
  folder: "M3 6h6l2 2h10v11H3z",
  handshake: "M2 12l4-4 4 2 4-3 4 3 4 2M6 12l4 4 2-1 2 2 3-3",
  devices: "M3 5h13v10H3zM1 18h17M18 9h5v11h-5z",
  phone: "M7 2h10v20H7zM11 18h2",
  desktop: "M3 4h18v12H3zM8 20h8M12 16v4",
  laptop: "M5 5h14v10H5zM2 19h20",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  menu: "M3 6h18M3 12h18M3 18h18",
  close: "M6 6l12 12M18 6L6 18",
  copy: "M8 8h12v12H8zM4 16V4h12",
  stop: "M7 7h10v10H7z",
  send: "M4 12h15M13 6l6 6-6 6",
  open: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  retry: "M4 12a8 8 0 1 0 2.5-5.8M4 4v4h4",
  speaking: "M4 9v6h4l5 4V5L8 9zM16 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12",
  muted: "M4 9v6h4l5 4V5L8 9zM17 9l5 5M22 9l-5 5",
  chevron: "M9 6l6 6-6 6",
};

export function Icon({ name, size = 16, title }: { name?: string; size?: number; title?: string }) {
  const d = name ? PATHS[name] : undefined;
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title ? <title>{title}</title> : null}
      {d ? (
        <path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" strokeLinejoin="miter" />
      ) : (
        <rect x="7" y="7" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.8" />
      )}
    </svg>
  );
}
