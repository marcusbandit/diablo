import { sseParser } from "./sse";
import type {
  Block,
  ChatReply,
  Device,
  Health,
  HubEvent,
  Message,
  PairCode,
  Character,
  PairStatus,
  Paired,
  Pending,
  Section,
  Task,
} from "./types";

export const TOKEN_HEADER = "X-Diablo-Token";

export class HubError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "HubError";
  }
}

/** The platform's fetch. React Native passes expo/fetch so the event stream can be read as it arrives. */
export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

type Options = { fetch?: Fetch; timeoutMs?: number };

/**
 * The hub's HTTP API for one hub base URL (`http://diablo-pi:8755`, or `""` for same origin on
 * the web). Every call except health and pairing needs a token.
 */
export class Hub {
  private readonly fetchFn: Fetch;
  private readonly timeoutMs: number;

  constructor(public readonly base: string, public token: string | null = null, options: Options = {}) {
    this.fetchFn = options.fetch ?? ((input, init) => fetch(input, init));
    this.timeoutMs = options.timeoutMs ?? 10_000;
  }

  withToken(token: string | null): Hub {
    return new Hub(this.base, token, { fetch: this.fetchFn, timeoutMs: this.timeoutMs });
  }

  /** Milliseconds the hub took to answer, or throws. */
  async ping(timeoutMs = 3000): Promise<number> {
    const started = Date.now();
    await this.request<Health>("GET", "/v1/health", undefined, { auth: false, timeoutMs });
    return Date.now() - started;
  }

  health(): Promise<Health> {
    return this.request("GET", "/v1/health", undefined, { auth: false });
  }

  async pair(code: string, name: string): Promise<Paired> {
    const out = await this.request<{ token: string; device?: { id: number; name: string }; hub?: string }>(
      "POST",
      "/v1/pair",
      { code, name },
      { auth: false },
    );
    if (!out.token) throw new HubError(500, "the hub sent no token");
    return { token: out.token, deviceId: out.device?.id ?? 0, device: out.device?.name ?? name, hub: out.hub ?? "" };
  }

  /** Pairs a browser that reached the hub through the tailnet door, with no code. */
  async pairTailnet(name: string): Promise<Paired> {
    const out = await this.request<{ token: string; device?: { id: number; name: string }; hub?: string }>(
      "POST",
      "/v1/pair/tailnet",
      { name },
      { auth: false },
    );
    return { token: out.token, deviceId: out.device?.id ?? 0, device: out.device?.name ?? name, hub: out.hub ?? "" };
  }

  mintPairCode(): Promise<PairCode> {
    return this.request("POST", "/v1/pair/code");
  }

  pairStatus(code: string): Promise<PairStatus> {
    return this.request("GET", `/v1/pair/status?code=${encodeURIComponent(code)}`);
  }

  whoami(): Promise<{ master: boolean; device: { id: number; name: string } | null; hub: string }> {
    return this.request("GET", "/v1/whoami");
  }

  async devices(): Promise<Device[]> {
    const out = await this.request<{ devices: Device[] }>("GET", "/v1/devices");
    return out.devices ?? [];
  }

  /** Task cards, newest activity first. */
  async tasks(): Promise<Task[]> {
    const out = await this.request<{ tasks: Task[] }>("GET", "/v1/tasks");
    return out.tasks ?? [];
  }

  async taskSeen(id: string): Promise<void> {
    await this.request("POST", `/v1/tasks/${encodeURIComponent(id)}/seen`);
  }

  /** Questions that still wait for an answer, on any channel. */
  async questions(): Promise<Pending[]> {
    const out = await this.request<{ questions: Pending[] }>("GET", "/v1/questions");
    return out.questions ?? [];
  }

  async revokeDevice(id: number): Promise<void> {
    await this.request("DELETE", `/v1/devices/${id}`);
  }

  character(): Promise<Character> {
    return this.request("GET", "/v1/character");
  }

  /** Only a paired device may save it; the machine token gets 403. */
  saveCharacter(text: string): Promise<Character> {
    return this.request("PUT", "/v1/character", { text });
  }

  async sections(): Promise<Section[]> {
    const out = await this.request<{ sections: Section[] }>("GET", "/v1/sections");
    return out.sections ?? [];
  }

  async putSections(sections: Section[]): Promise<Section[]> {
    const out = await this.request<{ sections: Section[] }>("PUT", "/v1/sections", { sections });
    return out.sections ?? [];
  }

  /** Oldest first. `after` asks for newer than that id, `before` pages backwards. */
  async messages(section: string, page: { after?: number; before?: number; limit?: number } = {}): Promise<Message[]> {
    const q = new URLSearchParams();
    if (page.after != null) q.set("after", String(page.after));
    if (page.before != null) q.set("before", String(page.before));
    q.set("limit", String(page.limit ?? 200));
    const out = await this.request<{ messages: Message[] }>("GET", `/v1/sections/${encodeURIComponent(section)}/messages?${q}`);
    return out.messages ?? [];
  }

  async panel(section: string): Promise<Block[]> {
    const out = await this.request<{ objects?: Block[] }>("GET", `/v1/sections/${encodeURIComponent(section)}/panel`);
    return out.objects ?? [];
  }

  /** Runs a tap from a panel or a reply. */
  async act(act: Record<string, unknown>, section: string): Promise<{ message: string; panel: Block[] | null }> {
    const out = await this.request<{ message?: string; panel?: { objects?: Block[] } }>("POST", "/v1/act", { ...act, section });
    return { message: out.message ?? "done", panel: out.panel?.objects ?? null };
  }

  /** One turn. clientId makes a retry safe: the hub never runs the same id twice. */
  async chat(
    section: string,
    text: string,
    clientId: string,
    source: string,
    extra: { edits?: number; regenerates?: number } = {},
  ): Promise<ChatReply> {
    const out = await this.request<{
      reply?: string;
      id?: number;
      user_id?: number;
      section?: string;
      objects?: Block[];
      threads?: { id: string; title: string }[];
    }>("POST", "/v1/chat", { text, section, source, client_id: clientId, ...extra }, { timeoutMs: 10 * 60_000 });
    return {
      reply: out.reply ?? "",
      id: out.id ?? 0,
      userId: out.user_id ?? 0,
      section: out.section ?? section,
      objects: out.objects ?? [],
      threads: out.threads ?? [],
    };
  }

  /** One hub setting, like `speaking_mode`, or null when it was never set. */
  async setting(key: string): Promise<string | null> {
    const out = await this.request<{ value?: string | null }>("GET", `/v1/settings/${encodeURIComponent(key)}`);
    return out.value ?? null;
  }

  async putSetting(key: string, value: string): Promise<void> {
    await this.request("PUT", `/v1/settings/${encodeURIComponent(key)}`, { value });
  }

  async stopTurn(section: string): Promise<boolean> {
    const out = await this.request<{ stopped: boolean }>("POST", "/v1/turns/stop", { section });
    return out.stopped;
  }

  async answerQuestion(id: string, answer: { choices?: number[]; text?: string }): Promise<void> {
    await this.request("POST", `/v1/questions/${encodeURIComponent(id)}/answer`, answer);
  }

  /**
   * Answers a question and waits for the turn it starts, like chat: the hub stores his pick as
   * a message and runs Diablo's reply before it answers.
   */
  async answerAndWait(
    id: string,
    answer: { choices?: number[]; text?: string },
    clientId: string,
    source: string,
  ): Promise<{ reply: string; id: number }> {
    const out = await this.request<{ reply?: string; id?: number }>(
      "POST",
      `/v1/questions/${encodeURIComponent(id)}/answer`,
      { ...answer, source, client_id: clientId },
      { timeoutMs: 10 * 60_000 },
    );
    return { reply: out.reply ?? "", id: out.id ?? 0 };
  }

  /**
   * Where a stored file lives. The hub wants the token as a header here, so React Native passes
   * `headers` to its Image source and the web fetches it into a blob URL.
   */
  file(fileId: string): { uri: string; headers: Record<string, string> } {
    return { uri: `${this.base}/v1/files/${encodeURIComponent(fileId)}`, headers: this.headers(true) };
  }

  /** The event stream as a URL with the token in it, for the browser's EventSource. */
  eventsUrl(ping?: number): string {
    const q = new URLSearchParams();
    if (this.token) q.set("token", this.token);
    if (ping) q.set("ping", String(ping));
    return `${this.base}/v1/events?${q}`;
  }

  /**
   * Holds the event stream open until signal aborts or the hub closes it. Resolves when it
   * ends, rejects on an HTTP error. The caller reconnects.
   */
  async events(onEvent: (event: HubEvent) => void, opts: { ping?: number; signal?: AbortSignal } = {}): Promise<void> {
    const q = opts.ping ? `?ping=${opts.ping}` : "";
    const response = await this.fetchFn(`${this.base}/v1/events${q}`, {
      headers: this.headers(true),
      signal: opts.signal,
    });
    if (!response.ok) throw new HubError(response.status, `events ${response.status}`);
    const feed = sseParser(onEvent);
    const body = response.body;
    if (!body) {
      feed(await response.text());
      return;
    }
    const reader = body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      feed(decoder.decode(value, { stream: true }));
    }
  }

  private headers(auth: boolean, json = false): Record<string, string> {
    const h: Record<string, string> = {};
    if (json) h["Content-Type"] = "application/json";
    if (auth && this.token) h[TOKEN_HEADER] = this.token;
    return h;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    opts: { auth?: boolean; timeoutMs?: number } = {},
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? this.timeoutMs);
    let response: Response;
    try {
      response = await this.fetchFn(`${this.base}${path}`, {
        method,
        headers: this.headers(opts.auth !== false, body !== undefined),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (e) {
      throw new HubError(0, controller.signal.aborted ? "the hub did not answer in time" : `cannot reach the hub: ${String(e)}`);
    } finally {
      clearTimeout(timer);
    }
    const text = await response.text();
    if (!response.ok) {
      let detail: string | undefined;
      try {
        detail = JSON.parse(text).detail;
      } catch {
        // not JSON
      }
      throw new HubError(response.status, typeof detail === "string" ? detail : `the hub answered ${response.status}`);
    }
    return (text ? JSON.parse(text) : {}) as T;
  }
}

/**
 * Picks the first hub in `bases` that answers health, in order: the Pi, then the standby.
 * The standby answers only while the Pi is down, so this moves between them on its own.
 */
export async function pickHub(bases: string[], token: string | null, options: Options = {}): Promise<Hub | null> {
  for (const base of bases) {
    const hub = new Hub(base, token, options);
    try {
      await hub.ping();
      return hub;
    } catch {
      // try the next
    }
  }
  return null;
}
