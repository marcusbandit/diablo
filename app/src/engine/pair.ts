/** A pairing code as the hub wants it: eight letters as ABCD-EFGH, or null. */
export function pairCode(input: string): string | null {
  const letters = input.toUpperCase().replace(/[^A-Z0-9]/g, "");
  return letters.length === 8 ? `${letters.slice(0, 4)}-${letters.slice(4)}` : null;
}

const SCHEMES = new Set(["diablo:", "diablo-next:"]);

/**
 * Reads what a pairing came from: a link the app opened (`diablo-next://pair?host=..&code=..`),
 * the hub's page (`https://<hub>/app/pair.html?code=..`), or typed letters. `host` is the hub
 * the link names, or null when it names none.
 */
export function readPairing(raw: string): { host: string | null; code: string } | null {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    const code = pairCode(raw);
    return code ? { host: null, code } : null;
  }
  const code = pairCode(url.searchParams.get("code") ?? "");
  if (!code) return null;
  if (SCHEMES.has(url.protocol)) {
    const host = url.searchParams.get("host");
    if (!host) return { host: null, code };
    return /^https?:\/\/[^/\s]+$/i.test(host.replace(/\/+$/, "")) ? { host: host.replace(/\/+$/, ""), code } : null;
  }
  if (url.protocol === "http:" || url.protocol === "https:") return { host: url.origin, code };
  return null;
}
