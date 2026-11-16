import type { Block, Message, Section } from "@diablo/hub";

import type { Sql } from "./sql";

/** What a conversation shows: a message from the hub, or one of his lines still in the outbox. */
export type Shown = {
  key: string;
  id: number | null;
  section: string;
  role: string;
  text: string;
  /** Unix seconds. */
  ts: number;
  meta: Record<string, unknown> | null;
  state: "sent" | "queued" | "failed";
  clientId: string | null;
  error?: string | null;
};

export type Outgoing = { clientId: string; seq: number; section: string; text: string; created: number; state: "queued" | "failed"; error: string | null };

type MessageRow = { id: number; section: string; role: string; text: string; ts: number; meta: string | null };
type OutboxRow = { client_id: string; seq: number; section: string; text: string; created: number; state: string; error: string | null };

const SCHEMA = `
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sections (id TEXT PRIMARY KEY, position INTEGER NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  section TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  ts REAL NOT NULL,
  meta TEXT
);
CREATE INDEX IF NOT EXISTS messages_section ON messages (section, id);
CREATE TABLE IF NOT EXISTS outbox (
  client_id TEXT PRIMARY KEY,
  seq INTEGER NOT NULL,
  section TEXT NOT NULL,
  text TEXT NOT NULL,
  created REAL NOT NULL,
  state TEXT NOT NULL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS panels (section TEXT PRIMARY KEY, json TEXT NOT NULL, generated REAL);
`;

const parse = <T,>(text: string | null, fallback: T): T => {
  if (!text) return fallback;
  try {
    return JSON.parse(text) as T;
  } catch {
    return fallback;
  }
};

export const now = () => Date.now() / 1000;

/** A client id for a turn: unique, and stable across retries of the same line. */
export function newClientId(): string {
  const random = Math.random().toString(36).slice(2, 10);
  return `phone-${Date.now().toString(36)}-${random}`;
}

/**
 * The phone's own copy of Diablo: sections, every message it has fetched, the outbox, the
 * panels and a few settings. Screens read it first and draw at once; the network only adds.
 * The device token is never kept here.
 */
export class Store {
  constructor(private readonly db: Sql) {
    db.exec(SCHEMA);
  }

  get(key: string): string | null {
    return this.db.first<{ value: string }>("SELECT value FROM kv WHERE key = ?", [key])?.value ?? null;
  }

  set(key: string, value: string | null): void {
    if (value == null) this.db.run("DELETE FROM kv WHERE key = ?", [key]);
    else this.db.run("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", [key, value]);
  }

  sections(): Section[] {
    return this.db.all<{ json: string }>("SELECT json FROM sections ORDER BY position, id").map((r) => parse<Section>(r.json, {} as Section));
  }

  /** The hub's list is the truth: what it no longer lists is gone here too. */
  saveSections(list: Section[]): void {
    this.db.tx(() => {
      this.db.run("DELETE FROM sections");
      list.forEach((s, i) => this.db.run("INSERT INTO sections (id, position, json) VALUES (?, ?, ?)", [s.id, s.position ?? i, JSON.stringify(s)]));
    });
  }

  /** Newest `limit` of a section, oldest first: hub messages, then his lines still waiting. */
  messages(section: string, limit = 300): Shown[] {
    const rows = this.db
      .all<MessageRow>("SELECT id, section, role, text, ts, meta FROM messages WHERE section = ? ORDER BY id DESC LIMIT ?", [section, limit])
      .reverse();
    const shown: Shown[] = [];
    for (const r of rows) {
      const meta = parse<Record<string, unknown> | null>(r.meta, null);
      if (meta && meta.superseded_by != null) continue;
      shown.push({
        key: `r${r.id}`,
        id: r.id,
        section: r.section,
        role: r.role,
        text: r.text,
        ts: r.ts,
        meta,
        state: "sent",
        clientId: typeof meta?.client_id === "string" ? meta.client_id : null,
      });
    }
    for (const o of this.outbox(section)) {
      shown.push({ key: `o${o.clientId}`, id: null, section: o.section, role: "user", text: o.text, ts: o.created, meta: null, state: o.state, clientId: o.clientId, error: o.error });
    }
    return shown;
  }

  /**
   * Keeps messages from the hub, by id, and drops any outbox line the hub already has: a user
   * message carries the client id it was sent with. Returns how many were new.
   */
  addMessages(section: string, messages: Message[]): number {
    let added = 0;
    this.db.tx(() => {
      for (const m of messages) {
        const meta = m.meta ?? null;
        if (!this.has(m.id)) added += 1;
        this.db.run(
          "INSERT INTO messages (id, section, role, text, ts, meta) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET text = excluded.text, meta = excluded.meta, role = excluded.role",
          [m.id, section, m.role, m.text, m.ts, meta ? JSON.stringify(meta) : null],
        );
        const clientId = meta && meta.client_id != null ? String(meta.client_id) : null;
        if (m.role === "user" && clientId) this.db.run("DELETE FROM outbox WHERE client_id = ?", [clientId]);
      }
    });
    return added;
  }

  has(id: number): boolean {
    return this.db.first<{ id: number }>("SELECT id FROM messages WHERE id = ?", [id]) != null;
  }

  /** The id the next pull of a section starts after, or null before its first pull. */
  cursor(section: string): number | null {
    const v = this.get(`cursor:${section}`);
    return v == null ? null : Number(v);
  }

  setCursor(section: string, id: number): void {
    const was = this.cursor(section) ?? 0;
    if (id > was) this.set(`cursor:${section}`, String(id));
  }

  oldest(section: string): number | null {
    return this.db.first<{ id: number | null }>("SELECT MIN(id) AS id FROM messages WHERE section = ?", [section])?.id ?? null;
  }

  lastAssistant(section: string): Shown | null {
    const r = this.db.first<MessageRow>(
      "SELECT id, section, role, text, ts, meta FROM messages WHERE section = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1",
      [section],
    );
    if (!r) return null;
    return { key: `r${r.id}`, id: r.id, section, role: r.role, text: r.text, ts: r.ts, meta: parse(r.meta, null), state: "sent", clientId: null };
  }

  /** Replies newer than the last one he had on screen. */
  unread(section: string): number {
    const read = Number(this.get(`read:${section}`) ?? 0);
    return this.db.first<{ n: number }>("SELECT COUNT(*) AS n FROM messages WHERE section = ? AND role = 'assistant' AND id > ?", [section, read])?.n ?? 0;
  }

  markRead(section: string): void {
    const last = this.db.first<{ id: number | null }>("SELECT MAX(id) AS id FROM messages WHERE section = ?", [section])?.id;
    if (last != null) this.set(`read:${section}`, String(last));
  }

  /** Replaces one question block wherever a stored reply carries it. */
  patchQuestion(question: Block): boolean {
    const id = typeof question.id === "string" ? question.id : null;
    if (!id) return false;
    const rows = this.db.all<MessageRow>("SELECT id, section, role, text, ts, meta FROM messages WHERE meta LIKE ?", [`%${id}%`]);
    let changed = false;
    for (const r of rows) {
      const meta = parse<Record<string, unknown> | null>(r.meta, null);
      const objects = Array.isArray(meta?.objects) ? (meta!.objects as Block[]) : null;
      if (!meta || !objects) continue;
      const next = objects.map((o) => (o.kind === "question" && o.id === id ? { ...o, ...question } : o));
      meta.objects = next;
      this.db.run("UPDATE messages SET meta = ? WHERE id = ?", [JSON.stringify(meta), r.id]);
      changed = true;
    }
    return changed;
  }

  enqueue(section: string, text: string, clientId = newClientId()): Outgoing {
    const seq = (this.db.first<{ s: number | null }>("SELECT MAX(seq) AS s FROM outbox")?.s ?? 0) + 1;
    const created = now();
    this.db.run("INSERT INTO outbox (client_id, seq, section, text, created, state, error) VALUES (?, ?, ?, ?, ?, 'queued', NULL)", [clientId, seq, section, text, created]);
    return { clientId, seq, section, text, created, state: "queued", error: null };
  }

  outbox(section?: string): Outgoing[] {
    const rows = section
      ? this.db.all<OutboxRow>("SELECT * FROM outbox WHERE section = ? ORDER BY seq", [section])
      : this.db.all<OutboxRow>("SELECT * FROM outbox ORDER BY seq");
    return rows.map((r) => ({ clientId: r.client_id, seq: r.seq, section: r.section, text: r.text, created: r.created, state: r.state === "failed" ? "failed" : "queued", error: r.error }));
  }

  /** The oldest line still waiting, across every section: they go in the order he wrote them. */
  nextQueued(): Outgoing | null {
    return this.outbox().find((o) => o.state === "queued") ?? null;
  }

  queuedCount(): number {
    return this.db.first<{ n: number }>("SELECT COUNT(*) AS n FROM outbox WHERE state = 'queued'")?.n ?? 0;
  }

  markFailed(clientId: string, error: string): void {
    this.db.run("UPDATE outbox SET state = 'failed', error = ? WHERE client_id = ?", [error, clientId]);
  }

  retry(clientId: string): void {
    this.db.run("UPDATE outbox SET state = 'queued', error = NULL WHERE client_id = ?", [clientId]);
  }

  dropOutgoing(clientId: string): void {
    this.db.run("DELETE FROM outbox WHERE client_id = ?", [clientId]);
  }

  panel(section: string): { blocks: Block[]; generated: number | null } | null {
    const r = this.db.first<{ json: string; generated: number | null }>("SELECT json, generated FROM panels WHERE section = ?", [section]);
    return r ? { blocks: parse<Block[]>(r.json, []), generated: r.generated } : null;
  }

  savePanel(section: string, blocks: Block[], generated: number | null = now()): void {
    this.db.run(
      "INSERT INTO panels (section, json, generated) VALUES (?, ?, ?) ON CONFLICT(section) DO UPDATE SET json = excluded.json, generated = excluded.generated",
      [section, JSON.stringify(blocks), generated],
    );
  }

  /** Forgets everything the hub sent: for a phone paired to a different hub. */
  clearHubData(): void {
    this.db.tx(() => {
      this.db.run("DELETE FROM messages");
      this.db.run("DELETE FROM panels");
      this.db.run("DELETE FROM kv WHERE key LIKE 'cursor:%' OR key LIKE 'read:%'");
    });
  }
}
