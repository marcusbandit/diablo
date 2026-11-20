import { Platform } from "react-native";
import { requireOptionalNativeModule } from "expo";

/** Where the phone stands with what the assistant needs. */
export type AssistStatus = {
  /** Diablo holds Android's assistant role: holding power opens him. */
  assistant: boolean;
  /** He may draw over other apps. */
  overlay: boolean;
  /** He may use the microphone. */
  mic: boolean;
};

type Native = {
  configure(hubs: string[], token: string | null): void;
  status(): AssistStatus;
  openAssistantSettings(): void;
  openOverlaySettings(): void;
};

const native = Platform.OS === "android" ? requireOptionalNativeModule<Native>("DiabloAssist") : null;

const NONE: AssistStatus = { assistant: false, overlay: false, mic: false };

/** The phone's assistant side (modules/diablo-assist). Everything is a no-op off Android. */
export const assist = {
  available: native != null,
  /** Hands the hubs and this device's token to the assistant, which talks to the hub on its own. */
  configure: (hubs: string[], token: string | null) => native?.configure(hubs, token),
  status: (): AssistStatus => native?.status() ?? NONE,
  openAssistantSettings: () => native?.openAssistantSettings(),
  openOverlaySettings: () => native?.openOverlaySettings(),
};
