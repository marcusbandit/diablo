// Pairing and section links are handled by the root layout; the router only goes home for them.
export function redirectSystemPath({ path }: { path: string; initial: boolean }): string {
  const p = path.replace(/^diablo:\/\//, "/");
  if (/^\/*(pair|section)(\/|\?|$)/.test(p)) return "/";
  return path;
}
