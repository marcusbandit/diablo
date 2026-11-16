/** The host part of a hub address, for showing which hub a phone talks to. */
export const hostOf = (url: string | null) => {
  if (!url) return "";
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
};
