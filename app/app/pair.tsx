import { Redirect, useLocalSearchParams } from "expo-router";
import { useEffect } from "react";

import { engine } from "../src/useEngine";

/**
 * A pairing link (`diablo-next://pair?host=..&code=..`) lands here: the engine pairs, and the
 * den shows how it went. The pairing screens of onboarding come in step 5 of #91.
 */
export default function Pair() {
  const { code, host } = useLocalSearchParams<{ code?: string; host?: string }>();

  useEffect(() => {
    if (!code) return;
    const q = new URLSearchParams({ code });
    if (host) q.set("host", host);
    void engine.pairFrom(`diablo-next://pair?${q}`);
  }, [code, host]);

  return <Redirect href="/" />;
}
