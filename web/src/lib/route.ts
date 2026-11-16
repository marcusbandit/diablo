export type Route = { view: "section"; id: string } | { view: "devices" } | { view: "character" } | { view: "intro" };

/** `#/s/<id>`, `#/devices`, `#/character`, `#/intro`. Anything else is the main section. */
export function parseRoute(hash: string, main = "diablo"): Route {
  const path = hash.replace(/^#\/?/, "");
  const [head, ...rest] = path.split("/");
  if (head === "devices") return { view: "devices" };
  if (head === "intro") return { view: "intro" };
  if (head === "character") return { view: "character" };
  if (head === "s" && rest[0]) return { view: "section", id: decodeURIComponent(rest[0]) };
  return { view: "section", id: main };
}

export function routeHash(route: Route): string {
  if (route.view === "devices") return "#/devices";
  if (route.view === "intro") return "#/intro";
  if (route.view === "character") return "#/character";
  return `#/s/${encodeURIComponent(route.id)}`;
}
