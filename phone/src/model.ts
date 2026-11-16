import { Hub, HubError, parsePairLink, type Block, type Fetch, type HubEvent, type Section } from "@diablo/hub";
import Constants from "expo-constants";
import * as Device from "expo-device";
import { fetch as expoFetch } from "expo/fetch";
import * as SecureStore from "expo-secure-store";
import { AppState, PermissionsAndroid, Platform } from "react-native";

import Listener from "../modules/diablo-listener";
import { plain } from "./md/plain";
import { openExpoSql } from "./store/expo-sql";
import { now, Store, type Shown } from "./store/store";
import { flushOutbox } from "./sync/outbox";

export const MAIN = "diablo";
const TOKEN = "hub-token";
const fetchFn = expoFetch as unknown as Fetch;

export type LinkState = "checking" | "online" | "offline" | "unpaired" | "refused";
export type Link = { state: LinkState; hub: string | null; ms: number | null; queued: number };
export type Turn = { state: "thinking" | "doing"; step?: string; tool?: string; since: number };
export type Pairing = {
  working: boolean;
  ok: boolean | null;
  text: string | null;
  unknownHost: { host: string; code: string } | null;
  fromLink: boolean;
};
export type Notice = { section: string; text: string; failed: boolean; at: number };
export type Draft = { section: string; text: string; nonce: number };
export type DeviceInfo = { name: string; id: number; hub: string };
export type PanelState = { blocks: Block[]; generated: number | null };

export type State = {
  ready: boolean;
  onboarded: boolean;
  sections: Section[];
  current: string;
  messages: Shown[];
  panels: Record<string, PanelState>;
  turns: Record<string, Turn>;
  unread: Record<string, number>;
  previews: Record<string, string>;
  link: Link;
  device: DeviceInfo | null;
  pairing: Pairing;
  notify: { enabled: boolean; allowed: boolean; blocked: boolean };
  notice: Notice | null;
  draft: Draft | null;
  hubs: string[];
  hubTimes: Record<string, number | null>;
  panelOpen: Record<string, boolean>;
  sending: string | null;
};

/** The main section before the hub has sent its list, so the app opens to something. */
const PLACEHOLDER: Section = {
  id: MAIN,
  name: "Diablo",
  icon: "flame",
  accent: "#ff4d2e",
  brief: "",
  position: 0,
  hidden: false,
  deleted: false,
  updated: 0,
};

const idle: Pairing = { working: false, ok: null, text: null, unknownHost: null, fromLink: false };

function defaultHubs(): string[] {
  // Development against a fake hub: Metro started with EXPO_PUBLIC_DIABLO_HUBS=url,url inlines it.
  const dev = process.env.EXPO_PUBLIC_DIABLO_HUBS;
  if (dev) return dev.split(",").map((h: string) => h.trim()).filter(Boolean);
  const extra = (Constants.expoConfig?.extra ?? {}) as { hubs?: unknown };
  return Array.isArray(extra.hubs) ? extra.hubs.filter((h): h is string => typeof h === "string" && h.length > 0) : [];
}

const unique = (list: string[]) => [...new Set(list.map((h) => h.replace(/\/+$/, "")))];

function deviceName(): string {
  const maker = Device.manufacturer ?? "";
  const model = Device.modelName ?? "Android phone";
  return maker && !model.toLowerCase().startsWith(maker.toLowerCase()) ? `${maker} ${model}` : model;
}

const visible = (s: Section) => !s.deleted && (!s.hidden || s.id === MAIN);

/**
 * The phone's view of Diablo. The store is the source of what is shown; the network only adds
 * to it, so every section opens at once and offline. One instance for the whole app.
 */
class Model {
  private store!: Store;
  private token: string | null = null;
  private listeners = new Set<() => void>();
  private wake: (() => void) | null = null;
  private stream: AbortController | null = null;
  private streamHub: string | null = null;
  private hub: Hub | null = null;
  private foreground = true;
  private flushing = false;
  private actedAt: Record<string, number> = {};
  private started = false;
  private noticeTimer: ReturnType<typeof setTimeout> | null = null;

  state: State = {
    ready: false,
    onboarded: false,
    sections: [PLACEHOLDER],
    current: MAIN,
    messages: [],
    panels: {},
    turns: {},
    unread: {},
    previews: {},
    link: { state: "checking", hub: null, ms: null, queued: 0 },
    device: null,
    pairing: idle,
    notify: { enabled: false, allowed: false, blocked: false },
    notice: null,
    draft: null,
    hubs: [],
    hubTimes: {},
    panelOpen: {},
    sending: null,
  };

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getState = () => this.state;

  private set(patch: Partial<State>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  async start() {
    if (this.started) return;
    this.started = true;
    this.store = new Store(openExpoSql());
    this.token = await SecureStore.getItemAsync(TOKEN).catch(() => null);
    const device = this.readDevice();
    const sections = this.store.sections();
    const current = this.store.get("current") ?? MAIN;
    const listener = Listener?.state();
    this.set({
      ready: true,
      onboarded: this.store.get("onboarded") === "1",
      sections: sections.length ? sections : [PLACEHOLDER],
      current: sections.some((s) => s.id === current) ? current : MAIN,
      device: this.token ? device : null,
      hubs: this.hubs(),
      panelOpen: JSON.parse(this.store.get("panel-open") ?? "{}"),
      notify: { enabled: listener?.enabled ?? false, allowed: listener?.allowed ?? false, blocked: false },
    });
    this.reload();
    this.loadPanels();
    AppState.addEventListener("change", (s) => this.appState(s === "active"));
    this.appState(AppState.currentState === "active");
    void this.loop();
  }

  // ---- reading ---------------------------------------------------------------------------

  /** The sections the navigation shows, in order. */
  shownSections(): Section[] {
    return this.state.sections.filter(visible);
  }

  section(id = this.state.current): Section {
    return this.state.sections.find((s) => s.id === id) ?? this.state.sections.find((s) => s.id === MAIN) ?? PLACEHOLDER;
  }

  private hubs(): string[] {
    const extra: string[] = JSON.parse(this.store.get("hubs:extra") ?? "[]");
    return unique([...extra, ...defaultHubs()]);
  }

  private readDevice(): DeviceInfo | null {
    const raw = this.store.get("device");
    if (!raw) return null;
    try {
      return JSON.parse(raw) as DeviceInfo;
    } catch {
      return null;
    }
  }

  private reload() {
    const s = this.store;
    const unread: Record<string, number> = {};
    const previews: Record<string, string> = {};
    for (const sec of this.state.sections) {
      unread[sec.id] = sec.id === this.state.current ? 0 : s.unread(sec.id);
      const last = s.lastAssistant(sec.id);
      if (last) previews[sec.id] = plain(last.text).split("\n")[0] ?? "";
    }
    this.set({
      messages: s.messages(this.state.current),
      unread,
      previews,
      link: { ...this.state.link, queued: s.queuedCount() },
    });
  }

  private loadPanels() {
    const panels: Record<string, PanelState> = {};
    for (const sec of this.state.sections) {
      const p = this.store.panel(sec.id);
      if (p) panels[sec.id] = p;
    }
    this.set({ panels });
  }

  /** His latest reply in a section as one plain line, for previews. */
  lastReply(section: string): string | null {
    return this.state.previews[section] ?? null;
  }

  // ---- navigation ------------------------------------------------------------------------

  select(id: string) {
    if (!this.state.sections.some((s) => s.id === id)) return;
    this.store.set("current", id);
    this.store.markRead(id);
    this.set({ current: id });
    this.reload();
    Listener?.setPresence(this.foreground, id);
    void this.refreshSection(id);
  }

  /** Opens the section that shows a module's panel, like Workout for a tap on the push-up tile. */
  openModule(module: string) {
    const shows = this.state.sections.filter((s) => !s.deleted && s.panels?.includes(module));
    const target = shows.find((s) => s.id !== MAIN) ?? shows[0];
    if (target) this.select(target.id);
  }

  /** Where a stored file lives, with the token header the hub wants, or null unpaired. */
  fileSource(id: string): { uri: string; headers: Record<string, string> } | null {
    const base = this.hub?.base ?? this.state.device?.hub;
    if (!base || !this.token) return null;
    return new Hub(base, this.token).file(id);
  }

  togglePanel(section: string) {
    const open = { ...this.state.panelOpen, [section]: !(this.state.panelOpen[section] ?? true) };
    this.store.set("panel-open", JSON.stringify(open));
    this.set({ panelOpen: open });
  }

  // ---- talking ---------------------------------------------------------------------------

  send(text: string, section = this.state.current) {
    const line = text.trim();
    if (!line) return;
    this.store.enqueue(section, line);
    this.reload();
    void this.flush();
  }

  retry(clientId: string) {
    this.store.retry(clientId);
    this.reload();
    void this.flush();
  }

  discard(clientId: string) {
    this.store.dropOutgoing(clientId);
    this.reload();
  }

  /** A button in a panel or reply: send the line, or only put it in the composer. */
  say(text: string, send: boolean) {
    if (send) this.send(text);
    else this.set({ draft: { section: this.state.current, text, nonce: Date.now() } });
  }

  draftTaken() {
    this.set({ draft: null });
  }

  async act(act: Record<string, unknown>) {
    const section = this.state.current;
    const hub = this.hub;
    if (!hub || !this.token) {
      this.notice(section, "Offline: the hub has to answer for that.", true);
      return;
    }
    try {
      const out = await hub.act(act, section);
      if (out.panel) {
        this.actedAt[section] = Date.now();
        this.store.savePanel(section, out.panel);
        this.set({ panels: { ...this.state.panels, [section]: { blocks: out.panel, generated: now() } } });
      }
      this.notice(section, out.message, false);
    } catch (error) {
      this.notice(section, error instanceof HubError ? error.message : "That did nothing.", true);
      void this.refreshPanel(hub, section);
    }
  }

  async answer(questionId: string, answer: { choices?: number[]; text?: string }) {
    const hub = this.hub;
    const section = this.state.current;
    if (!hub || !this.token) {
      this.notice(section, "Offline: answer once the hub is back.", true);
      return;
    }
    this.setTurn(section, { state: "thinking", since: Date.now() });
    try {
      await hub.answerQuestion(questionId, { ...answer, ...({ source: "phone" } as object) });
      await this.pull(hub, section);
    } catch (error) {
      this.notice(section, error instanceof HubError ? error.message : "The hub did not take the answer.", true);
    } finally {
      this.setTurn(section, null);
    }
  }

  private notice(section: string, text: string, failed: boolean) {
    this.set({ notice: { section, text, failed, at: Date.now() } });
    if (this.noticeTimer) clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => this.set({ notice: null }), 4000);
  }

  private setTurn(section: string, turn: Turn | null) {
    const turns = { ...this.state.turns };
    if (turn) turns[section] = turn;
    else delete turns[section];
    this.set({ turns });
  }

  // ---- pairing ---------------------------------------------------------------------------

  /** A scanned QR code, a tapped pairing link, or typed letters. */
  async pairFrom(raw: string, fromLink = false) {
    const link = parsePairLink(raw);
    if (!link) {
      this.set({ pairing: { ...idle, ok: false, text: "That is not a pairing code. It looks like ABCD-EFGH.", fromLink } });
      return;
    }
    if (link.host && !this.state.hubs.includes(link.host)) {
      this.set({ pairing: { ...idle, unknownHost: { host: link.host, code: link.code }, fromLink } });
      return;
    }
    await this.pair(link.code, link.host, fromLink);
  }

  async confirmHost(yes: boolean) {
    const unknown = this.state.pairing.unknownHost;
    const fromLink = this.state.pairing.fromLink;
    if (!unknown) return;
    if (!yes) {
      this.set({ pairing: { ...idle, ok: false, text: "Not paired.", fromLink } });
      return;
    }
    await this.pair(unknown.code, unknown.host, fromLink);
  }

  dismissPairing() {
    this.set({ pairing: idle });
  }

  private async pair(code: string, host: string | null, fromLink: boolean) {
    if (this.state.pairing.working) return;
    this.set({ pairing: { ...idle, working: true, text: "Pairing", fromLink } });
    const candidates = host ? [host] : this.state.hubs;
    let last = "No hub answered. Is Tailscale on?";
    for (const base of candidates) {
      let paired;
      try {
        paired = await new Hub(base, null, { fetch: fetchFn }).pair(code, deviceName());
      } catch (error) {
        const status = error instanceof HubError ? error.status : 0;
        if (status === 403) {
          last = "The hub does not know that code. Codes last ten minutes and work once; ask for a new one.";
          break;
        }
        if (status === 429) {
          last = "Too many wrong codes. Wait ten minutes, then ask for a new one.";
          break;
        }
        if (status !== 0 && status !== 503) last = `The hub answered ${status}: ${(error as Error).message}`;
        continue;
      }
      const previous = this.readDevice();
      const previousToken = this.token;
      if (!this.state.hubs.includes(base)) {
        const extra: string[] = JSON.parse(this.store.get("hubs:extra") ?? "[]");
        this.store.set("hubs:extra", JSON.stringify(unique([base, ...extra])));
      }
      if (previous && previous.hub !== base) this.store.clearHubData();
      await SecureStore.setItemAsync(TOKEN, paired.token);
      this.token = paired.token;
      const device: DeviceInfo = { name: paired.device, id: paired.deviceId, hub: base };
      this.store.set("device", JSON.stringify(device));
      this.set({ device, hubs: this.hubs(), link: { ...this.state.link, state: "checking" } });
      if (previous && previousToken && previous.id !== paired.deviceId) {
        new Hub(previous.hub, paired.token, { fetch: fetchFn }).revokeDevice(previous.id).catch(() => undefined);
      }
      this.configureListener({ token: paired.token });
      this.set({ pairing: { ...idle, ok: true, text: `Paired as ${paired.device}.`, fromLink } });
      this.kick();
      return;
    }
    this.set({ pairing: { ...idle, ok: false, text: last, fromLink } });
  }

  /** Locks this phone out on the hub and forgets its token. */
  async unpair() {
    const device = this.readDevice();
    const token = this.token;
    let heard = false;
    if (device && token) {
      try {
        await new Hub(device.hub, token, { fetch: fetchFn }).revokeDevice(device.id);
        heard = true;
      } catch {
        heard = false;
      }
    }
    await SecureStore.deleteItemAsync(TOKEN).catch(() => undefined);
    this.token = null;
    this.store.set("device", null);
    this.stopStream();
    this.configureListener({ token: null });
    this.set({
      device: null,
      link: { ...this.state.link, state: "unpaired" },
      pairing: heard
        ? idle
        : { ...idle, ok: false, text: `This phone forgot its key, but the hub did not hear it. Send Diablo /devices and revoke ${device?.name ?? "this phone"} there.` },
    });
  }

  // ---- notifications ---------------------------------------------------------------------

  private configureListener(extra: Record<string, unknown> = {}) {
    if (!Listener) return;
    const out = Listener.configure({
      hubs: this.state.hubs,
      sections: this.state.sections.map((s) => ({ id: s.id, name: s.name, accent: s.accent, brief: s.brief, deleted: s.deleted })),
      ...extra,
    });
    this.set({ notify: { ...this.state.notify, enabled: out.enabled, allowed: out.allowed } });
  }

  /** Asks Android once, then starts the listener. False when Android said no. */
  async setNotifications(on: boolean): Promise<boolean> {
    if (on && Platform.OS === "android" && Number(Platform.Version) >= 33) {
      const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
      if (result !== PermissionsAndroid.RESULTS.GRANTED) {
        this.set({ notify: { ...this.state.notify, allowed: false, blocked: true } });
        return false;
      }
    }
    this.configureListener({ enabled: on, token: this.token });
    this.set({ notify: { ...this.state.notify, blocked: false } });
    return true;
  }

  /** Reads the switch and Android's permission again, after a trip to the system settings. */
  refreshNotify() {
    const s = Listener?.state();
    if (!s) return;
    if (this.state.notify.blocked && s.allowed) {
      // He switched it on in Android's settings after we asked: finish turning it on.
      this.set({ notify: { ...this.state.notify, blocked: false } });
      this.configureListener({ enabled: true, token: this.token });
      return;
    }
    this.set({ notify: { enabled: s.enabled, allowed: s.allowed, blocked: this.state.notify.blocked && !s.allowed } });
  }

  testNotification() {
    Listener?.test(MAIN, "Notifications reach this phone. When a reply lands while the app is closed, it shows up like this.");
  }

  // ---- onboarding ------------------------------------------------------------------------

  finishOnboarding() {
    this.store.set("onboarded", "1");
    this.set({ onboarded: true });
    this.kick();
  }

  replayOnboarding() {
    this.store.set("onboarded", "0");
    this.set({ onboarded: false });
  }

  // ---- sync ------------------------------------------------------------------------------

  kick() {
    this.wake?.();
  }

  private appState(active: boolean) {
    this.foreground = active;
    Listener?.setPresence(active, active ? this.state.current : null);
    if (active) {
      this.refreshNotify();
      this.kick();
    } else {
      this.stopStream();
    }
  }

  private async loop() {
    for (;;) {
      if (this.foreground) {
        try {
          await this.syncOnce();
        } catch {
          // the next round tries again
        }
      }
      const wait = this.state.link.state === "online" ? 60_000 : 15_000;
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, wait);
        this.wake = () => {
          clearTimeout(timer);
          resolve();
        };
      });
      this.wake = null;
    }
  }

  /** The first hub that answers, Pi first, and how fast each was. */
  private async pickHub(): Promise<Hub | null> {
    const times: Record<string, number | null> = {};
    let found: Hub | null = null;
    for (const base of this.state.hubs) {
      const hub = new Hub(base, this.token, { fetch: fetchFn });
      try {
        times[base] = await hub.ping();
        found = hub;
        break;
      } catch {
        times[base] = null;
      }
    }
    this.set({ hubTimes: { ...this.state.hubTimes, ...times } });
    return found;
  }

  private async syncOnce() {
    const hub = await this.pickHub();
    const queued = this.store.queuedCount();
    if (!hub) {
      this.hub = null;
      this.stopStream();
      this.set({ link: { state: "offline", hub: null, ms: null, queued } });
      return;
    }
    const ms = this.state.hubTimes[hub.base] ?? null;
    if (!this.token) {
      this.hub = null;
      this.set({ link: { state: "unpaired", hub: hub.base, ms, queued } });
      return;
    }
    this.hub = hub;
    try {
      await this.syncSections(hub);
      void this.flush();
      for (const s of this.shownSections()) await this.pull(hub, s.id);
      await this.refreshPanel(hub, this.state.current);
      this.set({ link: { state: "online", hub: hub.base, ms, queued: this.store.queuedCount() } });
      this.listen(hub);
    } catch (error) {
      const status = error instanceof HubError ? error.status : 0;
      if (status === 401 || status === 403) {
        this.stopStream();
        this.set({ link: { state: "refused", hub: hub.base, ms, queued } });
      } else {
        this.set({ link: { state: "offline", hub: null, ms: null, queued } });
      }
    }
  }

  private async syncSections(hub: Hub) {
    const list = await hub.sections();
    if (!list.length) return;
    this.store.saveSections(list);
    const current = list.some((s) => s.id === this.state.current && !s.deleted) ? this.state.current : MAIN;
    this.set({ sections: list, current });
    if (this.state.notify.enabled) this.configureListener();
  }

  /** Fetches a section's messages past its cursor; the first pull takes the newest page. */
  private async pull(hub: Hub, section: string) {
    let cursor = this.store.cursor(section);
    let added = 0;
    for (let page = 0; page < 10; page++) {
      const batch = await hub.messages(section, cursor == null ? { limit: 200 } : { after: cursor, limit: 200 });
      if (!batch.length) break;
      added += this.store.addMessages(section, batch);
      const top = Math.max(...batch.map((m) => m.id));
      this.store.setCursor(section, top);
      cursor = top;
      if (batch.length < 200) break;
    }
    if (section === this.state.current && this.foreground) this.store.markRead(section);
    if (added || section === this.state.current) this.reload();
  }

  /** Older messages for the top of a conversation. False when there are none. */
  async older(section = this.state.current): Promise<boolean> {
    const hub = this.hub;
    const oldest = this.store.oldest(section);
    if (!hub || oldest == null) return false;
    try {
      const batch = await hub.messages(section, { before: oldest, limit: 100 });
      if (!batch.length) return false;
      this.store.addMessages(section, batch);
      this.reload();
      return true;
    } catch {
      return false;
    }
  }

  private async refreshPanel(hub: Hub, section: string) {
    const info = this.section(section);
    if (!info.panels?.length) return;
    try {
      const blocks = await hub.panel(section);
      this.store.savePanel(section, blocks);
      this.set({ panels: { ...this.state.panels, [section]: { blocks, generated: now() } } });
    } catch {
      // keep the stored panel
    }
  }

  private async refreshSection(section: string) {
    const hub = this.hub;
    if (!hub) return;
    try {
      await this.pull(hub, section);
      await this.refreshPanel(hub, section);
    } catch {
      // the loop tries again
    }
  }

  private async flush() {
    if (this.flushing || !this.token) return;
    const hub = this.hub;
    if (!hub) return;
    this.flushing = true;
    try {
      const out = await flushOutbox(this.store, (line) => hub.chat(line.section, line.text, line.clientId, "phone"), {
        started: (line) => {
          this.set({ sending: line.clientId });
          if (!this.state.turns[line.section]) this.setTurn(line.section, { state: "thinking", since: Date.now() });
        },
        finished: (line) => {
          this.set({ sending: null });
          this.setTurn(line.section, null);
          this.reload();
        },
      });
      if (out.stopped === "unpaired") this.set({ link: { ...this.state.link, state: "refused" } });
    } finally {
      this.flushing = false;
      this.reload();
    }
  }

  private stopStream() {
    this.stream?.abort();
    this.stream = null;
    this.streamHub = null;
  }

  private listen(hub: Hub) {
    if (!this.foreground) return;
    if (this.stream && this.streamHub === hub.base) return;
    this.stopStream();
    const controller = new AbortController();
    this.stream = controller;
    this.streamHub = hub.base;
    hub
      .events((e) => this.onEvent(hub, e), { ping: 30, signal: controller.signal })
      .catch(() => undefined)
      .finally(() => {
        if (this.stream !== controller) return;
        this.stream = null;
        this.streamHub = null;
        setTimeout(() => this.kick(), 3000);
      });
  }

  private onEvent(hub: Hub, event: HubEvent) {
    const raw = (event as { section?: unknown }).section;
    const section = typeof raw === "string" ? raw : null;
    switch (event.kind) {
      case "message":
        if (section) void this.pull(hub, section).catch(() => undefined);
        break;
      case "sections":
        void this.syncSections(hub).then(() => this.reload()).catch(() => undefined);
        break;
      case "panel": {
        const modules = Array.isArray(event.modules) ? (event.modules as string[]) : [];
        const current = this.state.current;
        const shown = this.section(current).panels ?? [];
        const fresh = Date.now() - (this.actedAt[current] ?? 0) < 2000;
        if (!fresh && shown.length && (!modules.length || modules.some((m) => shown.includes(m)))) void this.refreshPanel(hub, current);
        break;
      }
      case "turn": {
        if (!section) break;
        const state = String(event.state);
        if (state === "thinking" || state === "doing") {
          const was = this.state.turns[section];
          this.setTurn(section, {
            state,
            step: typeof event.step === "string" ? event.step : was?.step,
            tool: typeof event.tool === "string" ? event.tool : was?.tool,
            since: was?.since ?? Date.now(),
          });
        } else {
          this.setTurn(section, null);
          void this.pull(hub, section).catch(() => undefined);
        }
        break;
      }
      case "question": {
        const block = event.question as Block | undefined;
        if (block && this.store.patchQuestion(block)) this.reload();
        break;
      }
    }
  }
}

export const model = new Model();
