import { useCallback, useState } from "react";
import { bootstrapToken, saveToken } from "./lib/auth";
import { HubProvider } from "./lib/hub";
import { ToastProvider } from "./lib/toast";
import { Onboarding } from "./views/Onboarding";
import { Shell } from "./views/Shell";

export function App() {
  const [token, setToken] = useState<string | null>(() => bootstrapToken());
  const [revoked, setRevoked] = useState(false);

  const signOut = useCallback((reason?: string) => {
    saveToken(null);
    setRevoked(reason === "revoked");
    setToken(null);
  }, []);

  const paired = useCallback((t: string) => {
    saveToken(t);
    setRevoked(false);
    setToken(t);
  }, []);

  return (
    <ToastProvider>
      {token ? (
        <HubProvider token={token} onSignOut={signOut}>
          <Shell />
        </HubProvider>
      ) : (
        <Onboarding revoked={revoked} onPaired={paired} />
      )}
    </ToastProvider>
  );
}
