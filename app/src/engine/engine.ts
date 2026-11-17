import type { Disk, Secrets } from "./disk";
import { Hub, HubError, type Fetch } from "./hub";
import { flush } from "./outbox";
import { readPairing } from "./pair";
import { newClientId, Store, type Device } from "./store";
import type { Block, BusyTurn, Fact, HubEvent, Question, Section, Task } from "./wire";

/** The main conversation, the one the den talks in. */
export const MAIN = "diablo";

/**
 * Where the app stands with the hub. `starting` until the first round has run, `unpaired`
 * when a hub answers but this device has no token, `refused` when the hub stopped knowing it.
 */
export type LinkState = "starting" | "online" | "offline" | "unpaired" | "refused";

/** One line of a conversation: from the hub, or his own still in the outbox. */
export type Shown = {
  key: string;
  id: number | null;
  role: string;
  text: string;
  /** Unix seconds. */
  ts: number;
  meta: Record<string, unknown> | null;
  state: "sent" | "queued" | "sending" | "failed";
  clientId: string | null;
  error: string | null;
};

/** A turn running in one section, from the event stream. */
export type Turn = { state: "thinking" | "doing"; step: string; tool: string };

export type Pairing = {
  working: boolean;
  error: string | null;
  /** A link named a hub this app does not know; it pairs only once he says yes. */
  confirm: { host: string; code: string } | null;
};

export type EngineState = {
  /** The store has been read: screens can draw. */
  ready: boolean;
  link: LinkState;
  /** The hub answering right now, or null. */
  hub: string | null;
  device: Device | null;
  sections: Section[];
  /** Each section's lines, oldest first. */
  conversations: Record<string, Shown[]>;
  /** Questions that wait for him and can be answered here, newest first. */
  questions: Question[];
  tasks: Task[];
  /** Every turn the hub runs right now, whatever started it. */
  busy: BusyTurn[];
  turns: Record<string, Turn>;
  /** The client id of the line on its way to the hub. */
  sending: string | null;
  /** A reply just landed. */
  speaking: boolean;
  pairing: Pairing;
};

export type EngineOptions = {
  disk: Disk;
  secrets: Secrets;
  fetch: Fetch;
  /** The hubs the build knows, in order of preference: the Pi, then the standby. */
  hubs: string[];
  /** How this device is named on the hub. */
  deviceName: string;
  /** How the hub names where a line came from: `phone`, `web`. */
  source: string;
  /** Rounds while online; the event stream does the real work in between. */
  pollMs?: number;
  /** Rounds while no hub answers. */
  retryMs?: number;
  /** A dropped event stream comes back after this. */
  streamRetryMs?: number;
  /** Seconds between the hub's keepalives on the stream. */
  pingS?: number;
  /** How long his face shows he is talking after a reply lands. */
  speakMs?: number;
};

const unique = (list: string[]) => [...new Set(list.map((h) => h.replace(/\/+$/, "")))];

/** Questions he can answer here: still pending, and not one the old web chat has to answer. */
const answerable = (q: Question) => (q.state == null || q.state === "pending") && !String(q.conv ?? "").startsWith("web-");

const visible = (s: Section) => !s.deleted && (!s.hidden || s.id === MAIN);

/**
 * The app's link to Diablo. It keeps a store that screens draw from at once, offline too, and
 * brings it up to date from the first hub that answers: a round on start, on every return to
 * the foreground and after a dropped stream, and the event stream in between. Lines he writes
 * go into the outbox first and leave in order once a hub takes them.
 *
 * Platform free: the disk, the keystore and fetch are handed in, so the tests run it against a
 * fake hub.
 */
export class Engine {
  private store!: Store;
  private token: string | null = null;
  private listeners = new Set<() => void>();
  private hub: Hub | null = null;
  private foreground = true;
  private started = false;
  private stopped = false;
  private running = false;
  private again = false;
  private flushing = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private speakTimer: ReturnType<typeof setTimeout> | null = null;
  private stream: { controller: AbortController; base: string; watchdog: ReturnType<typeof setTimeout> | null } | null = null;
  private readonly o: Required<EngineOptions>;

  state: EngineState = {
    ready: false,
    link: "starting",
    hub: null,
    device: null,
    sections: [],
    conversations: {},
    questions: [],
    tasks: [],
    busy: [],
    turns: {},
    sending: null,
    speaking: false,
    pairing: { working: false, error: null, confirm: null },
  };

  constructor(options: EngineOptions) {
    this.o = { pollMs: 60_000, retryMs: 15_000, streamRetryMs: 3000, pingS: 30, speakMs: 2500, ...options };
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  getState = () => this.state;

  private set(patch: Partial<EngineState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    const [store, token] = await Promise.all([Store.open(this.o.disk), this.o.secrets.get().catch(() => null)]);
    this.store = store;
    this.token = token;
    this.set({
      ready: true,
      device: token ? store.device : null,
      sections: store.sections,
      questions: store.questions.filter(answerable),
      tasks: store.tasks,
    });
    this.showAll();
    this.kick();
  }

  /** Stops every timer and the stream, and writes what is left. For tests and teardown. */
  async stop(): Promise<void> {
    this.stopped = true;
    this.stopStream();
    for (const t of [this.timer, this.saveTimer, this.speakTimer]) if (t) clearTimeout(t);
    if (this.store) await this.store.save();
  }

  /** The app came to the front or went away. Away, it holds no stream and runs no rounds. */
  setForeground(on: boolean) {
    if (on === this.foreground) return;
    this.foreground = on;
    if (on) {
      this.kick();
      return;
    }
    this.stopStream();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    void this.store?.save();
  }

  /** Runs a round now, or right after the one running. */
  kick() {
    if (!this.started || this.stopped || !this.foreground || !this.store) return;
    if (this.running) {
      this.again = true;
      return;
    }
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    void this.round();
  }

  // ---- talking --------------------------------------------------------------------------

  /** Queues a line and sends it once a hub takes it. */
  async send(text: string, section = MAIN): Promise<void> {
    const line = text.trim();
    if (!line || !this.store) return;
    await this.store.enqueue(section, line, newClientId(this.o.source));
    this.show(section);
    void this.flushOutbox();
  }

  async retry(clientId: string): Promise<void> {
    const line = this.store.outbox.find((l) => l.clientId === clientId);
    if (!line) return;
    await this.store.retry(clientId);
    this.show(line.section);
    void this.flushOutbox();
  }

  async discard(clientId: string): Promise<void> {
    const line = this.store.outbox.find((l) => l.clientId === clientId);
    if (!line) return;
    await this.store.drop(clientId);
    this.show(line.section);
  }

  /** Ends the turn running in a section. */
  async stopTurn(section = MAIN): Promise<boolean> {
    return (await this.hub?.stopTurn(section).catch(() => false)) ?? false;
  }

  /**
   * Answers a question and waits for the turn it starts. Null when it went through, else why
   * not. The question leaves Needs you at once and comes back if the hub did not take it.
   */
  async answer(question: Question, answer: { choices?: number[]; text?: string }): Promise<string | null> {
    const hub = this.hub;
    if (!hub) return "The hub has to be there to take an answer.";
    const section = question.section ?? MAIN;
    this.set({ questions: this.state.questions.filter((q) => q.id !== question.id) });
    this.setTurn(section, { state: "thinking", step: "", tool: "" });
    try {
      await hub.answer(question.id, answer, newClientId(this.o.source), this.o.source);
      await this.pull(hub, section);
      return null;
    } catch (error) {
      void this.syncDen(hub).catch(() => undefined);
      return error instanceof HubError ? error.message : "The hub did not take the answer.";
    } finally {
      this.setTurn(section, null);
    }
  }

  /** A tap in a panel or a reply. */
  async act(act: Record<string, unknown>, section = MAIN): Promise<{ message: string; panel: Block[] | null } | { error: string }> {
    const hub = this.hub;
    if (!hub) return { error: "The hub has to be there for that." };
    try {
      return await hub.act(act, section);
    } catch (error) {
      return { error: error instanceof HubError ? error.message : "That did nothing." };
    }
  }

  async panel(section: string): Promise<Block[] | null> {
    return (await this.hub?.panel(section).catch(() => null)) ?? null;
  }

  /** What he remembers, newest first, or null while no hub answers. */
  async facts(q = ""): Promise<Fact[] | null> {
    return (await this.hub?.facts(q).catch(() => null)) ?? null;
  }

  /** Puts a finished card away. */
  seen(taskId: string) {
    const task = this.store.tasks.find((t) => t.id === taskId);
    if (!task) return;
    this.store.patchTask({ ...task, seen_at: Date.now() / 1000 });
    this.set({ tasks: this.store.tasks });
    void this.hub?.taskSeen(taskId).catch(() => undefined);
    this.saveSoon();
  }

  /** Fetches the page of messages before the oldest one held. False when there are none. */
  async older(section = MAIN): Promise<boolean> {
    const hub = this.hub;
    const oldest = this.store.oldest(section);
    if (!hub || oldest == null) return false;
    try {
      const page = await hub.messages(section, { before: oldest, limit: 100 });
      if (!this.store.addMessages(section, page)) return false;
      this.show(section);
      return true;
    } catch {
      return false;
    }
  }

  /** Where a stored file lives, with the token header, or null unpaired. */
  file(id: string): { uri: string; headers: Record<string, string> } | null {
    return this.hub?.file(id) ?? null;
  }

  // ---- pairing --------------------------------------------------------------------------

  /** A tapped pairing link, a scanned code or typed letters. */
  async pairFrom(raw: string): Promise<void> {
    const link = readPairing(raw);
    if (!link) {
      this.set({ pairing: { working: false, error: "That is not a pairing code. It looks like ABCD-EFGH.", confirm: null } });
      return;
    }
    if (link.host && !this.hubs().includes(link.host)) {
      this.set({ pairing: { working: false, error: null, confirm: link as { host: string; code: string } } });
      return;
    }
    await this.pair(link.code, link.host);
  }

  /** His answer to pairing with a hub the app did not know. */
  async confirmHost(yes: boolean): Promise<void> {
    const confirm = this.state.pairing.confirm;
    if (!confirm) return;
    if (!yes) {
      this.set({ pairing: { working: false, error: null, confirm: null } });
      return;
    }
    await this.pair(confirm.code, confirm.host);
  }

  private async pair(code: string, host: string | null): Promise<void> {
    if (this.state.pairing.working) return;
    this.set({ pairing: { working: true, error: null, confirm: null } });
    let why = "No hub answered. Is Tailscale on?";
    for (const base of host ? [host] : this.hubs()) {
      let paired;
      try {
        paired = await new Hub(base, null, this.o.fetch).pair(code, this.o.deviceName);
      } catch (error) {
        const status = error instanceof HubError ? error.status : 0;
        if (status === 403) {
          why = "The hub does not know that code. A code lasts ten minutes and works once.";
          break;
        }
        if (status === 429) {
          why = "Too many wrong codes. Wait ten minutes, then ask for a new one.";
          break;
        }
        if (status !== 0 && status !== 503) why = (error as Error).message;
        continue;
      }
      const before = this.store.device;
      const oldToken = this.token;
      if (before && before.hub !== base) this.store.forgetHub();
      if (!this.o.hubs.includes(base)) this.store.addHub(base);
      await this.o.secrets.set(paired.token);
      this.token = paired.token;
      const device: Device = { id: paired.device.id, name: paired.device.name, hub: base };
      this.store.setDevice(device);
      await this.store.save();
      // The old pairing goes, so the hub keeps one device per phone.
      if (before && oldToken && before.id !== device.id) void new Hub(before.hub, oldToken, this.o.fetch).revokeDevice(before.id).catch(() => undefined);
      this.stopStream();
      this.set({ device, link: "starting", sections: this.store.sections, pairing: { working: false, error: null, confirm: null } });
      this.showAll();
      this.kick();
      return;
    }
    this.set({ pairing: { working: false, error: why, confirm: null } });
  }

  /** Locks this device out on the hub and forgets its token. False when the hub did not hear it. */
  async unpair(): Promise<boolean> {
    const device = this.store.device;
    let heard = false;
    if (device && this.token) {
      heard = await new Hub(device.hub, this.token, this.o.fetch)
        .revokeDevice(device.id)
        .then(() => true)
        .catch(() => false);
    }
    await this.o.secrets.set(null);
    this.token = null;
    this.hub = null;
    this.store.setDevice(null);
    await this.store.save();
    this.stopStream();
    this.set({ device: null, link: "unpaired" });
    return heard;
  }

  // ---- rounds ---------------------------------------------------------------------------

  private hubs(): string[] {
    return unique([...(this.store?.extraHubs ?? []), ...this.o.hubs]);
  }

  private async round() {
    this.running = true;
    try {
      await this.sync();
    } catch {
      // the next round tries again
    } finally {
      this.running = false;
    }
    if (this.again) {
      this.again = false;
      this.kick();
      return;
    }
    if (this.stopped || !this.foreground) return;
    this.timer = setTimeout(() => this.kick(), this.state.link === "online" ? this.o.pollMs : this.o.retryMs);
  }

  /** The first hub that answers, in order. */
  private async pick(): Promise<Hub | null> {
    for (const base of this.hubs()) {
      const hub = new Hub(base, this.token, this.o.fetch);
      try {
        await hub.ping();
        return hub;
      } catch {
        // the next one
      }
    }
    return null;
  }

  private async sync() {
    const hub = await this.pick();
    if (!hub) {
      this.hub = null;
      this.stopStream();
      this.set({ link: "offline", hub: null });
      return;
    }
    if (!this.token) {
      this.hub = null;
      this.set({ link: "unpaired", hub: hub.base });
      return;
    }
    if (this.hub?.base !== hub.base) this.stopStream();
    this.hub = hub;
    try {
      await this.syncSections(hub);
      void this.flushOutbox();
      for (const s of this.state.sections.filter(visible)) await this.pull(hub, s.id);
      await this.syncDen(hub);
      this.set({ link: "online", hub: hub.base });
      this.listen(hub);
    } catch (error) {
      const status = error instanceof HubError ? error.status : 0;
      this.stopStream();
      this.set({ link: status === 401 || status === 403 ? "refused" : "offline", hub: status === 401 || status === 403 ? hub.base : null });
      if (status !== 401 && status !== 403) this.hub = null;
    } finally {
      this.saveSoon();
    }
  }

  private async syncSections(hub: Hub) {
    const list = await hub.sections();
    if (!list.length) return;
    this.store.setSections(list);
    this.set({ sections: this.store.sections });
  }

  private async syncDen(hub: Hub) {
    const [questions, tasks, busy] = await Promise.all([hub.questions(), hub.tasks(), hub.busy()]);
    this.store.setQuestions(questions);
    this.store.setTasks(tasks);
    this.set({ questions: questions.filter(answerable), tasks, busy });
  }

  /** Fetches a section past its cursor; the first pull takes the newest page. */
  private async pull(hub: Hub, section: string) {
    let cursor = this.store.cursor(section);
    const first = cursor == null;
    let fresh = false;
    for (let page = 0; page < 10; page++) {
      const batch = await hub.messages(section, cursor == null ? { limit: 200 } : { after: cursor, limit: 200 });
      if (!batch.length) break;
      const before = new Set(this.store.messages(section).map((m) => m.id));
      this.store.addMessages(section, batch);
      if (batch.some((m) => m.role === "assistant" && !before.has(m.id))) fresh = true;
      cursor = Math.max(...batch.map((m) => m.id));
      this.store.setCursor(section, cursor);
      if (batch.length < 200) break;
    }
    this.show(section);
    if (fresh && !first) this.speak();
    this.saveSoon();
  }

  private async flushOutbox() {
    const hub = this.hub;
    if (this.flushing || !hub || !this.token) return;
    this.flushing = true;
    try {
      const out = await flush(this.store, (line) => hub.chat(line.section, line.text, line.clientId, this.o.source), {
        started: (line) => {
          this.set({ sending: line.clientId });
          if (!this.state.turns[line.section]) this.setTurn(line.section, { state: "thinking", step: "", tool: "" });
          this.show(line.section);
        },
        finished: (line) => {
          this.set({ sending: null });
          this.setTurn(line.section, null);
          this.show(line.section);
        },
      });
      if (out.sent) this.speak();
      if (out.stopped === "unpaired") {
        this.stopStream();
        this.set({ link: "refused" });
      }
    } finally {
      this.flushing = false;
      this.saveSoon();
    }
  }

  // ---- the event stream -----------------------------------------------------------------

  private listen(hub: Hub) {
    if (!this.foreground || this.stopped) return;
    if (this.stream && this.stream.base === hub.base) return;
    this.stopStream();
    const controller = new AbortController();
    const stream = { controller, base: hub.base, watchdog: null as ReturnType<typeof setTimeout> | null };
    this.stream = stream;
    // A connection that went quiet without closing, like a phone that changed networks, is
    // only noticed by the keepalives stopping.
    const alive = () => {
      if (stream.watchdog) clearTimeout(stream.watchdog);
      stream.watchdog = setTimeout(() => controller.abort(), this.o.pingS * 2500);
    };
    hub
      .events((event) => this.onEvent(hub, event), { ping: this.o.pingS, signal: controller.signal, alive })
      .catch(() => undefined)
      .finally(() => {
        if (stream.watchdog) clearTimeout(stream.watchdog);
        if (this.stream !== stream) return;
        this.stream = null;
        if (!this.stopped && this.foreground) setTimeout(() => this.kick(), this.o.streamRetryMs);
      });
  }

  private stopStream() {
    const stream = this.stream;
    this.stream = null;
    if (!stream) return;
    if (stream.watchdog) clearTimeout(stream.watchdog);
    stream.controller.abort();
  }

  private onEvent(hub: Hub, event: HubEvent) {
    const section = typeof (event as { section?: unknown }).section === "string" ? ((event as { section: string }).section) : null;
    switch (event.kind) {
      case "message":
        if (section) void this.pull(hub, section).catch(() => undefined);
        break;
      case "sections":
        void this.syncSections(hub)
          .then(() => this.showAll())
          .catch(() => undefined);
        break;
      case "turn": {
        if (!section) break;
        const e = event as { state?: string; step?: unknown; tool?: unknown };
        if (e.state === "thinking" || e.state === "doing") {
          const was = this.state.turns[section];
          this.setTurn(section, {
            state: e.state,
            step: typeof e.step === "string" ? e.step : (was?.step ?? ""),
            tool: typeof e.tool === "string" ? e.tool : (was?.tool ?? ""),
          });
        } else {
          this.setTurn(section, null);
          void this.pull(hub, section).catch(() => undefined);
        }
        break;
      }
      case "question": {
        const q = (event as { question?: Question }).question;
        if (!q || typeof q.id !== "string") break;
        const asked = { ...q, section: q.section ?? section, message: q.message ?? ((event as { message?: number }).message ?? null) };
        this.store.patchQuestion(asked);
        const rest = this.store.questions.filter((x) => x.id !== q.id);
        this.store.setQuestions(answerable(asked) ? [asked, ...rest] : rest);
        this.set({ questions: this.store.questions.filter(answerable) });
        if (asked.section) this.show(asked.section);
        this.saveSoon();
        break;
      }
      case "task": {
        const task = (event as { task?: Task }).task;
        if (!task || typeof task.id !== "string") break;
        this.store.patchTask(task);
        this.set({ tasks: this.store.tasks });
        this.saveSoon();
        break;
      }
      case "busy": {
        const turns = (event as { turns?: unknown }).turns;
        if (Array.isArray(turns)) this.set({ busy: turns as BusyTurn[] });
        break;
      }
    }
  }

  // ---- state ----------------------------------------------------------------------------

  private setTurn(section: string, turn: Turn | null) {
    const turns = { ...this.state.turns };
    if (turn) turns[section] = turn;
    else delete turns[section];
    this.set({ turns });
  }

  private speak() {
    if (!this.foreground) return;
    if (this.speakTimer) clearTimeout(this.speakTimer);
    this.set({ speaking: true });
    this.speakTimer = setTimeout(() => this.set({ speaking: false }), this.o.speakMs);
  }

  private showAll() {
    const ids = new Set([...this.store.sections.map((s) => s.id), ...this.store.outbox.map((l) => l.section), MAIN]);
    const conversations: Record<string, Shown[]> = {};
    for (const id of ids) conversations[id] = this.lines(id);
    this.set({ conversations });
  }

  private show(section: string) {
    this.set({ conversations: { ...this.state.conversations, [section]: this.lines(section) } });
  }

  /** A section's messages, minus the ones a later edit replaced, then his lines still waiting. */
  private lines(section: string): Shown[] {
    const out: Shown[] = [];
    for (const m of this.store.messages(section)) {
      const meta = m.meta ?? null;
      if (meta?.superseded_by != null) continue;
      const clientId = meta?.client_id != null ? String(meta.client_id) : null;
      out.push({ key: `m${m.id}`, id: m.id, role: m.role, text: m.text, ts: m.ts, meta, state: "sent", clientId, error: null });
    }
    for (const l of this.store.outbox) {
      if (l.section !== section) continue;
      const state = l.state === "failed" ? "failed" : this.state.sending === l.clientId ? "sending" : "queued";
      out.push({ key: `o${l.clientId}`, id: null, role: "user", text: l.text, ts: l.created, meta: null, state, clientId: l.clientId, error: l.error });
    }
    return out;
  }

  private saveSoon() {
    if (this.saveTimer || this.stopped) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      void this.store.save().catch(() => undefined);
    }, 400);
  }
}
