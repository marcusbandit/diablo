import type { Disk } from "./disk";
import type { Block, Message, Question, Section, Task } from "./wire";

/** The device this app is paired as, and the hub that paired it. */
export type Device = { id: number; name: string; hub: string };

/** A line he wrote that the hub has not taken yet. */
export type Outgoing = { clientId: string; section: string; text: string; created: number; state: "queued" | "failed"; error: string | null };

/** How many of a section's newest messages survive a restart. */
export const KEEP = 500;

type Meta = { device: Device | null; hubs: string[]; cursors: Record<string, number> };
type Den = { questions: Question[]; tasks: Task[] };

const parse = <T,>(text: string | null, fallback: T): T => {
  if (!text) return fallback;
  try {
    return JSON.parse(text) as T;
  } catch {
    return fallback;
  }
};

/** A client id for a line: unique, and kept across every retry of that line. */
export function newClientId(prefix = "app"): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * The app's own copy of Diablo: sections, the messages it has fetched, the outbox, what needs
 * him and a little about the pairing. Screens draw from it at once, offline too; the network
 * only adds. It never holds the token.
 *
 * Changes stay in memory until `save()`, except the outbox, which is on disk before a line is
 * ever sent, so nothing he writes is lost to a crash.
 */
export class Store {
  private meta: Meta;
  private sectionList: Section[];
  private lines: Outgoing[];
  private den: Den;
  private bySection = new Map<string, Map<number, Message>>();
  private dirty = new Set<string>();

  private constructor(
    private readonly disk: Disk,
    raw: { meta: string | null; sections: string | null; outbox: string | null; den: string | null },
  ) {
    this.meta = { device: null, hubs: [], cursors: {}, ...parse<Partial<Meta>>(raw.meta, {}) };
    this.sectionList = parse<Section[]>(raw.sections, []);
    this.lines = parse<Outgoing[]>(raw.outbox, []);
    this.den = { questions: [], tasks: [], ...parse<Partial<Den>>(raw.den, {}) };
  }

  static async open(disk: Disk): Promise<Store> {
    const [meta, sections, outbox, den] = await Promise.all(["meta", "sections", "outbox", "den"].map((k) => disk.read(k)));
    const store = new Store(disk, { meta, sections, outbox, den });
    const ids = new Set([...store.sectionList.map((s) => s.id), ...store.lines.map((l) => l.section)]);
    await Promise.all(
      [...ids].map(async (id) => {
        const list = parse<Message[]>(await disk.read(`messages:${id}`), []);
        store.bySection.set(id, new Map(list.map((m) => [m.id, m])));
      }),
    );
    return store;
  }

  // ---- pairing --------------------------------------------------------------------------

  get device(): Device | null {
    return this.meta.device;
  }

  setDevice(device: Device | null): void {
    this.meta.device = device;
    this.dirty.add("meta");
  }

  /** Hubs he paired with that the build did not know, newest first. */
  get extraHubs(): string[] {
    return this.meta.hubs;
  }

  setExtraHubs(list: string[]): void {
    this.meta.hubs = list;
    this.dirty.add("meta");
  }

  // ---- sections and messages ------------------------------------------------------------

  get sections(): Section[] {
    return this.sectionList;
  }

  /** The hub's list is the truth: what it no longer lists is gone here too. */
  setSections(list: Section[]): void {
    this.sectionList = [...list].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
    this.dirty.add("sections");
  }

  /** A section's messages, oldest first. */
  messages(section: string): Message[] {
    const held = this.bySection.get(section);
    return held ? [...held.values()].sort((a, b) => a.id - b.id) : [];
  }

  /**
   * Keeps messages from the hub by id, a newer copy replacing an older one, and drops every
   * outbox line the hub already has: his message carries the client id it went with.
   * Returns how many were new.
   */
  addMessages(section: string, list: Message[]): number {
    if (!list.length) return 0;
    let held = this.bySection.get(section);
    if (!held) this.bySection.set(section, (held = new Map()));
    let added = 0;
    const taken = new Set<string>();
    for (const m of list) {
      if (!held.has(m.id)) added += 1;
      held.set(m.id, m);
      this.withdraw(held, m);
      const clientId = m.meta?.client_id;
      if (m.role === "user" && clientId != null) taken.add(String(clientId));
    }
    this.dirty.add(`messages:${section}`);
    if (taken.size && this.lines.some((l) => taken.has(l.clientId))) {
      this.lines = this.lines.filter((l) => !taken.has(l.clientId));
      this.dirty.add("outbox");
    }
    return added;
  }

  /**
   * The hub marks what an edit or a regenerate replaced with `superseded_by`, but only on
   * messages this app already pulled and never pulls again. So the new message withdraws them
   * here: an edit takes the old message and everything after it, a regenerate the old reply.
   */
  private withdraw(held: Map<number, Message>, m: Message): void {
    const edits = Number(m.meta?.edits ?? 0);
    const regenerates = Number(m.meta?.regenerates ?? 0);
    const mark = (id: number) => {
      const old = held.get(id);
      if (old && old.meta?.superseded_by == null) held.set(id, { ...old, meta: { ...old.meta, superseded_by: m.id } });
    };
    if (edits > 0) for (const id of held.keys()) if (id >= edits && id < m.id) mark(id);
    if (regenerates > 0) mark(regenerates);
  }

  /** The id the next pull of a section starts after, or null before its first pull. */
  cursor(section: string): number | null {
    return this.meta.cursors[section] ?? null;
  }

  setCursor(section: string, id: number): void {
    if (id > (this.meta.cursors[section] ?? 0)) {
      this.meta.cursors[section] = id;
      this.dirty.add("meta");
    }
  }

  oldest(section: string): number | null {
    const held = this.bySection.get(section);
    if (!held?.size) return null;
    return Math.min(...held.keys());
  }

  /** Puts a question's newest state into every stored reply that carries it. */
  patchQuestion(question: Question): void {
    for (const [section, held] of this.bySection) {
      for (const m of held.values()) {
        const objects = m.meta?.objects;
        if (!Array.isArray(objects) || !objects.some((o: Block) => o?.kind === "question" && o.id === question.id)) continue;
        const next = objects.map((o: Block) => (o?.kind === "question" && o.id === question.id ? { ...o, ...question, kind: "question" } : o));
        held.set(m.id, { ...m, meta: { ...m.meta, objects: next } });
        this.dirty.add(`messages:${section}`);
      }
    }
  }

  // ---- the outbox -----------------------------------------------------------------------

  /** Every line not taken yet, in the order he wrote them. */
  get outbox(): Outgoing[] {
    return this.lines;
  }

  async enqueue(section: string, text: string, clientId: string, created = Date.now() / 1000): Promise<Outgoing> {
    const line: Outgoing = { clientId, section, text, created, state: "queued", error: null };
    this.lines = [...this.lines, line];
    await this.writeOutbox();
    return line;
  }

  async markFailed(clientId: string, error: string): Promise<void> {
    this.lines = this.lines.map((l) => (l.clientId === clientId ? { ...l, state: "failed", error } : l));
    await this.writeOutbox();
  }

  async retry(clientId: string): Promise<void> {
    this.lines = this.lines.map((l) => (l.clientId === clientId ? { ...l, state: "queued", error: null } : l));
    await this.writeOutbox();
  }

  async drop(clientId: string): Promise<void> {
    this.lines = this.lines.filter((l) => l.clientId !== clientId);
    await this.writeOutbox();
  }

  private async writeOutbox(): Promise<void> {
    this.dirty.delete("outbox");
    await this.disk.write("outbox", JSON.stringify(this.lines));
  }

  // ---- what needs him -------------------------------------------------------------------

  get questions(): Question[] {
    return this.den.questions;
  }

  get tasks(): Task[] {
    return this.den.tasks;
  }

  setQuestions(list: Question[]): void {
    this.den = { ...this.den, questions: list };
    this.dirty.add("den");
  }

  setTasks(list: Task[]): void {
    this.den = { ...this.den, tasks: list };
    this.dirty.add("den");
  }

  /** One card from the event stream: replaces its old copy, or joins the list first. */
  patchTask(task: Task): void {
    this.setTasks([task, ...this.den.tasks.filter((t) => t.id !== task.id)]);
  }

  // ---- lifecycle ------------------------------------------------------------------------

  /** Forgets what one hub sent, for an app paired with a different one. The outbox stays. */
  forgetHub(): void {
    for (const id of this.bySection.keys()) this.dirty.add(`messages:${id}`);
    this.bySection.clear();
    this.meta.cursors = {};
    this.den = { questions: [], tasks: [] };
    this.sectionList = [];
    for (const key of ["meta", "den", "sections"]) this.dirty.add(key);
  }

  /** Writes every change since the last save. */
  async save(): Promise<void> {
    const keys = [...this.dirty];
    this.dirty.clear();
    await Promise.all(keys.map((key) => this.disk.write(key, this.serialize(key))));
  }

  private serialize(key: string): string {
    if (key === "meta") return JSON.stringify(this.meta);
    if (key === "sections") return JSON.stringify(this.sectionList);
    if (key === "outbox") return JSON.stringify(this.lines);
    if (key === "den") return JSON.stringify(this.den);
    return JSON.stringify(this.messages(key.slice("messages:".length)).slice(-KEEP));
  }
}
