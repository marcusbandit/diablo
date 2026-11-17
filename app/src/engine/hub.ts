import type { Block, BusyTurn, ChatReply, Fact, Health, HubEvent, Message, Paired, Question, Section, Task } from "./wire";

export const TOKEN_HEADER = "X-Diablo-Token";

/** A failed call. `status` 0 means the hub never answered. */
export class HubError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "HubError";
  }
}

/** The platform's fetch. The phone passes expo/fetch, whose body can be read as it arrives. */
export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

const LONG = 10 * 60_000;

/**
 * The hub's HTTP API at one base URL: the Pi, the standby, or `""` for the same origin in the
 * browser. Every call but health and pairing carries the token.
 */
export class Hub {
  constructor(
    readonly base: string,
    readonly token: string | null,
    private readonly fetchFn: Fetch,
    private readonly timeoutMs = 10_000,
  ) {}

  /** Milliseconds the hub took to answer health, or throws. */
  async ping(timeoutMs = 3000): Promise<number> {
    const started = Date.now();
    await this.call<Health>("GET", "/v1/health", undefined, { auth: false, timeoutMs });
    return Date.now() - started;
  }

  pair(code: string, name: string): Promise<Paired> {
    return this.call("POST", "/v1/pair", { code, name }, { auth: false });
  }

  /** A browser that came in through Diablo's tailnet name pairs with no code. */
  pairTailnet(name: string): Promise<Paired> {
    return this.call("POST", "/v1/pair/tailnet", { name }, { auth: false });
  }

  async revokeDevice(id: number): Promise<void> {
    await this.call("DELETE", `/v1/devices/${id}`);
  }

  async sections(): Promise<Section[]> {
    return (await this.call<{ sections?: Section[] }>("GET", "/v1/sections")).sections ?? [];
  }

  /** Oldest first. With no `after` it is the newest page; `before` pages backwards. */
  async messages(section: string, page: { after?: number; before?: number; limit?: number } = {}): Promise<Message[]> {
    const q = new URLSearchParams({ limit: String(page.limit ?? 200) });
    if (page.after) q.set("after", String(page.after));
    if (page.before) q.set("before", String(page.before));
    return (await this.call<{ messages?: Message[] }>("GET", `/v1/sections/${enc(section)}/messages?${q}`)).messages ?? [];
  }

  /** One turn. The hub never runs the same client id twice, so a retry is safe. */
  async chat(section: string, text: string, clientId: string, source: string): Promise<ChatReply> {
    const out = await this.call<Partial<ChatReply>>("POST", "/v1/chat", { text, section, source, client_id: clientId }, { timeoutMs: LONG });
    return { reply: out.reply ?? "", id: out.id ?? 0, user_id: out.user_id ?? 0, section: out.section ?? section, objects: out.objects ?? [], answered: out.answered };
  }

  async stopTurn(section: string): Promise<boolean> {
    return (await this.call<{ stopped?: boolean }>("POST", "/v1/turns/stop", { section })).stopped ?? false;
  }

  /** Questions that still wait, newest first. */
  async questions(): Promise<Question[]> {
    return (await this.call<{ questions?: Question[] }>("GET", "/v1/questions")).questions ?? [];
  }

  /** Answers a question and waits for the turn it starts, like chat. */
  async answer(id: string, answer: { choices?: number[]; text?: string }, clientId: string, source: string): Promise<ChatReply> {
    const out = await this.call<Partial<ChatReply>>(
      "POST",
      `/v1/questions/${enc(id)}/answer`,
      { choices: answer.choices ?? [], text: answer.text ?? "", source, client_id: clientId },
      { timeoutMs: LONG },
    );
    return { reply: out.reply ?? "", id: out.id ?? 0, user_id: out.user_id ?? 0, section: out.section ?? "", objects: out.objects ?? [], answered: out.answered };
  }

  async tasks(): Promise<Task[]> {
    return (await this.call<{ tasks?: Task[] }>("GET", "/v1/tasks")).tasks ?? [];
  }

  async taskSeen(id: string): Promise<void> {
    await this.call("POST", `/v1/tasks/${enc(id)}/seen`);
  }

  async busy(): Promise<BusyTurn[]> {
    return (await this.call<{ turns?: BusyTurn[] }>("GET", "/v1/busy")).turns ?? [];
  }

  async facts(q = "", limit = 100): Promise<Fact[]> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (q) query.set("q", q);
    return (await this.call<{ facts?: Fact[] }>("GET", `/v1/facts?${query}`)).facts ?? [];
  }

  async panel(section: string): Promise<Block[]> {
    return (await this.call<{ objects?: Block[] }>("GET", `/v1/sections/${enc(section)}/panel`)).objects ?? [];
  }

  /** Runs a tap from a panel or a reply. */
  async act(act: Record<string, unknown>, section: string): Promise<{ message: string; panel: Block[] | null }> {
    const out = await this.call<{ message?: string; panel?: { objects?: Block[] } }>("POST", "/v1/act", { ...act, section });
    return { message: out.message ?? "", panel: out.panel?.objects ?? null };
  }

  /** A stored file, with the token header the hub wants. */
  file(id: string): { uri: string; headers: Record<string, string> } {
    return { uri: `${this.base}/v1/files/${enc(id)}`, headers: this.headers(true) };
  }

  /**
   * Holds the event stream open until `signal` aborts or the hub closes it, calling `onEvent`
   * per event. Resolves when it ends and rejects on an HTTP error; the caller reconnects.
   */
  async events(onEvent: (event: HubEvent) => void, opts: { ping?: number; signal?: AbortSignal; alive?: () => void } = {}): Promise<void> {
    const response = await this.fetchFn(`${this.base}/v1/events${opts.ping ? `?ping=${opts.ping}` : ""}`, {
      headers: this.headers(true),
      signal: opts.signal,
    });
    if (!response.ok) throw new HubError(response.status, `events ${response.status}`);
    opts.alive?.();
    const feed = sse(onEvent);
    if (!response.body) {
      feed(await response.text());
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      // Keepalive comments count too: they are how a silent hub shows it is still there.
      opts.alive?.();
      feed(decoder.decode(value, { stream: true }));
    }
  }

  private headers(auth: boolean, json = false): Record<string, string> {
    const h: Record<string, string> = {};
    if (json) h["Content-Type"] = "application/json";
    if (auth && this.token) h[TOKEN_HEADER] = this.token;
    return h;
  }

  private async call<T>(method: string, path: string, body?: unknown, opts: { auth?: boolean; timeoutMs?: number } = {}): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? this.timeoutMs);
    let response: Response;
    let text: string;
    try {
      response = await this.fetchFn(`${this.base}${path}`, {
        method,
        headers: this.headers(opts.auth !== false, body !== undefined),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      text = await response.text();
    } catch (e) {
      throw new HubError(0, controller.signal.aborted ? "the hub did not answer in time" : `cannot reach the hub: ${String(e)}`);
    } finally {
      clearTimeout(timer);
    }
    if (!response.ok) throw new HubError(response.status, detail(text) ?? `the hub answered ${response.status}`);
    try {
      return (text ? JSON.parse(text) : {}) as T;
    } catch {
      throw new HubError(response.status, "the hub sent something that is not JSON");
    }
  }
}

const enc = encodeURIComponent;

function detail(text: string): string | null {
  try {
    const d = JSON.parse(text).detail;
    return typeof d === "string" ? d : null;
  } catch {
    return null;
  }
}

/**
 * Turns chunks of a server-sent event stream into events. Chunks may split lines anywhere;
 * keepalive comments and lines that are not a JSON object with a `kind` are dropped.
 */
export function sse(onEvent: (event: HubEvent) => void): (chunk: string) => void {
  let pending = "";
  return (chunk) => {
    pending += chunk;
    for (let nl = pending.indexOf("\n"); nl >= 0; nl = pending.indexOf("\n")) {
      const line = pending.slice(0, nl).replace(/\r$/, "");
      pending = pending.slice(nl + 1);
      if (!line.startsWith("data:")) continue;
      try {
        const value = JSON.parse(line.slice(5).trim());
        if (value && typeof value === "object" && typeof value.kind === "string") onEvent(value);
      } catch {
        // not an event
      }
    }
  };
}
