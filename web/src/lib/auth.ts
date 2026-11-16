export const TOKEN_KEY = "diablo-token";
/** Set for the tab once the tailnet door said no, so a reload does not ask again. */
export const DOOR_KEY = "diablo-door";

/**
 * Reads `?token=` from a query string. A token already stored wins, the way the old page did,
 * so a shared link cannot swap this browser's key. The query comes back without the token.
 */
export function takeTokenFromQuery(search: string, stored: string | null): { token: string | null; search: string } {
  const q = new URLSearchParams(search);
  const given = q.get("token");
  q.delete("token");
  const rest = q.toString();
  return { token: stored || given || null, search: rest ? `?${rest}` : "" };
}

function storage(kind: "local" | "session"): Storage | null {
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

export function loadToken(): string | null {
  try {
    return storage("local")?.getItem(TOKEN_KEY) || null;
  } catch {
    return null;
  }
}

export function saveToken(token: string | null): void {
  try {
    const s = storage("local");
    if (!s) return;
    if (token) s.setItem(TOKEN_KEY, token);
    else s.removeItem(TOKEN_KEY);
  } catch {
    // storage refused: the token lives for this page only
  }
}

export function doorShut(): boolean {
  try {
    return storage("session")?.getItem(DOOR_KEY) === "shut";
  } catch {
    return false;
  }
}

export function shutDoor(): void {
  try {
    storage("session")?.setItem(DOOR_KEY, "shut");
  } catch {
    // nothing to remember it in
  }
}

/** Takes `?token=` off the address bar and returns the token to use. */
export function bootstrapToken(): string | null {
  const { token, search } = takeTokenFromQuery(window.location.search, loadToken());
  if (search !== window.location.search) {
    window.history.replaceState(null, "", window.location.pathname + search + window.location.hash);
  }
  if (token) saveToken(token);
  return token;
}

/** A name for this browser, like "Firefox on Linux", as pair.html's devicename.js makes it. */
export function deviceLabel(ua: string): string {
  const browser = /Firefox\//.test(ua)
    ? "Firefox"
    : /Edg\//.test(ua)
      ? "Edge"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : "browser";
  const os = /Android/.test(ua)
    ? "Android"
    : /iPhone|iPad/.test(ua)
      ? "iOS"
      : /Mac/.test(ua)
        ? "macOS"
        : /Linux/.test(ua)
          ? "Linux"
          : /Windows/.test(ua)
            ? "Windows"
            : "";
  return browser + (os ? ` on ${os}` : "");
}
