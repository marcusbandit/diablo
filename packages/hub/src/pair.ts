/** A pairing code as the hub wants it: eight letters, upper case, as ABCD-EFGH. Null if it is not one. */
export function normalizePairCode(input: string): string | null {
  const letters = input.toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (letters.length !== 8) return null;
  return `${letters.slice(0, 4)}-${letters.slice(4)}`;
}

/**
 * Reads a pairing link: `diablo://pair?host=..&code=..` or the hub's
 * `http(s)://<hub>/app/pair.html?code=..`. Returns the hub base URL (when the link names one)
 * and the code.
 */
export function parsePairLink(link: string): { host: string | null; code: string } | null {
  let url: URL;
  try {
    url = new URL(link.trim());
  } catch {
    const code = normalizePairCode(link);
    return code ? { host: null, code } : null;
  }
  const code = normalizePairCode(url.searchParams.get("code") ?? "");
  if (!code) return null;
  if (url.protocol === "diablo:") {
    const host = url.searchParams.get("host");
    return { host: host ? host.replace(/\/+$/, "") : null, code };
  }
  if (url.protocol === "http:" || url.protocol === "https:") {
    return { host: url.origin, code };
  }
  return null;
}
