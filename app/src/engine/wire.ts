// What the hub sends, as docs/api.md describes it. Only the fields the app reads are typed;
// everything else rides along untouched.

/** A block from a reply's `objects` or a panel, told apart by `kind`. Unknown kinds are skipped. */
export type Block = { kind: string; [field: string]: unknown };

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

export type Message = {
  id: number;
  role: "user" | "assistant" | "system" | string;
  text: string;
  /** Unix seconds. */
  ts: number;
  meta?: Record<string, unknown> | null;
  threads?: string[];
};

export type QuestionState = "pending" | "answered" | "expired" | "cancelled" | "superseded";

/** A question Diablo asked with choices, and where it was asked. A mail approval is one too. */
export type Question = Block & {
  id: string;
  header: string;
  question: string;
  options: { label: string; description?: string }[];
  recommended: number | null;
  multi: boolean;
  allow_text: boolean;
  state: QuestionState;
  created?: number;
  expires?: number;
  section?: string | null;
  message?: number | null;
  conv?: string | null;
};

export type TaskState = "queued" | "working" | "waiting" | "done" | "failed";

/** Work Diablo runs or watches: a T3 thread or a card of his own. */
export type Task = {
  id: string;
  title: string;
  kind: string;
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

/** One turn the hub runs right now, whatever started it. */
export type BusyTurn = { id: string | number; conv: string; origin: string; since: number; words: string; step: string };

/** One thing Diablo remembers. */
export type Fact = { id: number; ts: number; text: string; tags: string[] };

export type ChatReply = {
  reply: string;
  id: number;
  user_id: number;
  section: string;
  objects: Block[];
  /** The question this line answered, when it named one of its options. */
  answered?: string;
};

export type Paired = { token: string; device: { id: number; name: string }; hub: string };

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
  | { kind: "message"; section: string; id?: number }
  | { kind: "sections" }
  | { kind: "panel"; modules: string[] }
  | TurnEvent
  | { kind: "question"; question: Question; message?: number; section?: string }
  | { kind: "task"; change: string; task: Task }
  | { kind: "busy"; turns: BusyTurn[]; at: number }
  | { kind: string; [field: string]: unknown };
