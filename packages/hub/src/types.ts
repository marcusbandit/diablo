// The hub's wire types, as docs/api.md describes them.

export type Section = {
  id: string;
  name: string;
  icon: string;
  accent: string;
  brief: string;
  position: number;
  hidden: boolean;
  deleted: boolean;
  updated: number;
  panels?: string[];
};

export type Role = "user" | "assistant" | "system" | string;

export type Message = {
  id: number;
  role: Role;
  text: string;
  ts: number;
  meta?: Record<string, unknown> | null;
  threads?: string[];
};

/** A block from a reply's `objects` or a panel. Told apart by `kind`; unknown kinds are skipped. */
export type Block = { kind: string; [field: string]: unknown };

export type Paired = { token: string; deviceId: number; device: string; hub: string };

export type ChatReply = {
  reply: string;
  id: number;
  userId: number;
  section: string;
  objects: Block[];
  threads: { id: string; title: string }[];
};

export type Device = {
  id: number;
  name: string;
  kind?: string;
  created?: number;
  last_seen?: number | null;
  current?: boolean;
};

export type TaskState = "queued" | "working" | "waiting" | "done" | "failed";

/** Work Diablo runs or watches: a T3 thread or a job of his own, kept by the hub with no model turn. */
export type Task = {
  id: string;
  title: string;
  kind: "self_improvement" | "partner";
  machine: string;
  state: TaskState;
  step: string;
  started_at: number | null;
  finished_at: number | null;
  seen_at: number | null;
  updated_at: number | null;
  links: { pr: string; pr_number: number | null; pr_state: string; issue: string; thread: string };
  result: string;
  source: string;
};

/** A question Diablo asked that still waits, with where it was asked. */
export type Pending = Block & { id: string; section?: string | null; message?: number | null; conv?: string | null };

export type PairCode = { code: string; expires_at: number; ttl: number; url: string; link: string };

export type PairStatus =
  | { state: "unknown" | "expired" }
  | { state: "paired"; device: string }
  | { state: "waiting"; expires_at: number };

export type Health = { ok: boolean; role?: string; stt?: string; revision?: string };

export type TurnEvent = {
  kind: "turn";
  section: string;
  state: "thinking" | "doing" | "done" | "failed";
  step?: string;
  tool?: string;
  id?: number;
  source?: string;
};

export type HubEvent =
  | { kind: "message"; section: string; id?: number; source?: string }
  | { kind: "sections" }
  | { kind: "panel"; modules: string[] }
  | TurnEvent
  | { kind: string; [field: string]: unknown };

/** Marcus's own words on Diablo's character, the default he falls back to, and recent spoken attempts that were refused. */
export type Character = {
  text: string;
  default: string;
  refused: { ts?: number; said?: string; tool?: string; [field: string]: unknown }[];
};
