import { NativeModule, requireOptionalNativeModule } from "expo";

export type ListenerSection = { id: string; name: string; accent: string; brief: string; deleted?: boolean };

export type ListenerConfig = {
  enabled?: boolean;
  hubs?: string[];
  sections?: ListenerSection[];
  /** The device token. The listener keeps its own copy, sealed by a keystore key. */
  token?: string | null;
};

export type ListenerState = { enabled: boolean; allowed: boolean; paired: boolean; running: boolean };

declare class DiabloListenerModule extends NativeModule<Record<string, never>> {
  configure(config: ListenerConfig): ListenerState;
  state(): ListenerState;
  setPresence(foreground: boolean, section: string | null): void;
  cancel(section: string): void;
  test(section: string, text: string): void;
}

/** Null where the native side is missing (web, tests). */
export default requireOptionalNativeModule<DiabloListenerModule>("DiabloListener");
