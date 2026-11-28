import type { Block } from "./engine/wire";

export type Exercise = { id: string; name: string; group: string; timed: boolean; aliases?: string[]; point?: string };

export type Session = {
  id: string;
  exercise: string;
  name: string;
  timed: boolean;
  sets: number[];
  weights?: (number | null)[];
  weightKg?: number;
  weightKind?: "total" | "added" | "assisted";
  at: string;
  open: boolean;
  suggest?: number;
};

export type DayPlan = {
  day: string;
  weekday: string;
  focus: string;
  label: string;
  reason: string;
  rest: boolean;
  gentle: boolean;
  soreParts: string[];
  soreTendons: string[];
  hurtingTendons: { name: string; label: string; side: string }[];
  hurtingMuscles: { name: string; label: string; side: string }[];
  goals: string[];
};

export type Logger = {
  current: Session | null;
  last: { exercise: string | null; name: string; did: number; timed: boolean } | null;
  today: Session[];
  setsToday: number;
  recent: Exercise[];
  defaults: Exercise[];
  library: Exercise[];
  plan: DayPlan | null;
};

const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const num = (v: unknown, d = 0) => (typeof v === "number" && Number.isFinite(v) ? v : d);
const str = (v: unknown) => (typeof v === "string" ? v : "");

function exercise(v: unknown): Exercise | null {
  const e = v as Record<string, unknown> | null;
  if (!e || !str(e.id) || !str(e.name)) return null;
  const out: Exercise = {
    id: str(e.id),
    name: str(e.name),
    group: str(e.group),
    timed: e.timed === true,
    aliases: arr<string>(e.aliases).filter((a) => typeof a === "string"),
  };
  if (str(e.point)) out.point = str(e.point);
  return out;
}

function session(v: unknown): Session | null {
  const s = v as Record<string, unknown> | null;
  if (!s || !str(s.id)) return null;
  const wk = str(s.weight_kind);
  return {
    id: str(s.id),
    exercise: str(s.exercise),
    name: str(s.name) || "Exercise",
    timed: s.timed === true,
    sets: arr<unknown>(s.sets).filter((n): n is number => typeof n === "number"),
    weights: Array.isArray(s.weights) ? s.weights.map((x) => (typeof x === "number" ? x : null)) : undefined,
    weightKg: typeof s.weight_kg === "number" ? s.weight_kg : undefined,
    weightKind: wk === "added" || wk === "assisted" || wk === "total" ? wk : undefined,
    at: str(s.at),
    open: s.open === true,
    suggest: typeof s.suggest === "number" ? s.suggest : undefined,
  };
}

function readPlan(v: unknown): DayPlan | null {
  const p = v as Record<string, unknown> | null;
  if (!p || !str(p.focus)) return null;
  const spots = (raw: unknown) =>
    arr<Record<string, unknown>>(raw)
      .filter((x) => x && str(x.name))
      .map((x) => ({ name: str(x.name), label: str(x.label) || str(x.name), side: str(x.side) }));
  return {
    day: str(p.day),
    weekday: str(p.weekday),
    focus: str(p.focus),
    label: str(p.label) || str(p.focus),
    reason: str(p.reason),
    rest: p.rest === true,
    gentle: p.gentle === true,
    soreParts: arr<string>(p.sore_parts).filter((x) => typeof x === "string"),
    soreTendons: arr<string>(p.sore_tendons).filter((x) => typeof x === "string"),
    hurtingTendons: spots(p.hurting_tendons),
    hurtingMuscles: spots(p.hurting_muscles),
    goals: arr<string>(p.goals).filter((x) => typeof x === "string"),
  };
}

const list = (v: unknown) => arr<unknown>(v).map(exercise).filter((e): e is Exercise => e !== null);

export function readLogger(blocks: Block[] | null): Logger | null {
  const b = blocks?.find((x) => x && x.kind === "sets") as Record<string, unknown> | undefined;
  if (!b) return null;
  const last = b.last as Record<string, unknown> | null;
  return {
    current: session(b.current),
    last: last && str(last.name) ? { exercise: str(last.exercise) || null, name: str(last.name), did: num(last.did, 10), timed: last.timed === true } : null,
    today: arr<unknown>(b.today).map(session).filter((s): s is Session => s !== null),
    setsToday: num(b.sets_today),
    recent: list(b.recent),
    defaults: list(b.defaults),
    library: list(b.library),
    plan: readPlan(b.plan),
  };
}

const squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
const words = (s: string) => s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

function alike(a: string, b: string): number {
  const d: number[][] = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array<number>(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
  }
  return 1 - d[a.length][b.length] / Math.max(a.length, b.length, 1);
}

export function searchExercises(library: Exercise[], query: string, limit = 12): Exercise[] {
  const key = squash(query);
  if (!key) return [];
  const typed = words(query);
  const scored: { rank: number; e: Exercise }[] = [];
  for (const e of library) {
    const names = [e.name, ...(e.aliases ?? [])];
    const keys = names.map(squash);
    let rank: number;
    if (keys.some((k) => k === key || k === `${key}s` || `${k}s` === key)) rank = 0;
    else if (keys[0].startsWith(key)) rank = 1;
    else if (keys.some((k) => k.startsWith(key))) rank = 1.5;
    else if (typed.every((w) => names.some((n) => words(n).some((part) => part.startsWith(w))))) rank = 2;
    else if (keys.some((k) => k.includes(key))) rank = 3;
    else {
      const best = Math.max(...keys.map((k) => alike(key, k)));
      if (best < 0.7) continue;
      rank = 4 - best;
    }
    scored.push({ rank, e });
  }
  scored.sort((a, b) => a.rank - b.rank || a.e.name.length - b.e.name.length || a.e.name.localeCompare(b.e.name));
  return scored.slice(0, limit).map((s) => s.e);
}

export const amount = (n: number, timed: boolean) => (timed ? `${n} s` : `${n}`);

export function formatWeight(kg?: number | null, kind?: "total" | "added" | "assisted"): string {
  if (!kg || kg <= 0) return "";
  const val = Number.isInteger(kg) ? String(kg) : String(Math.round(kg * 10) / 10);
  if (kind === "added") return `+${val} kg`;
  if (kind === "assisted") return `-${val} kg`;
  return `${val} kg`;
}

export function logAct(
  exercise: string,
  sets: number,
  reps: number,
  weightKg?: number,
  weightKind?: "total" | "added" | "assisted",
): Record<string, unknown> {
  const extra: Record<string, unknown> = {};
  if (typeof weightKg === "number" && Number.isFinite(weightKg) && weightKg > 0) {
    extra.weight_kg = Math.round(weightKg * 100) / 100;
    if (weightKind) extra.weight_kind = weightKind;
  }
  return sets === 1
    ? { module: "workout", action: "set", args: { exercise, did: reps, ...extra } }
    : { module: "workout", action: "sets", args: { exercise, reps: Array.from({ length: sets }, () => reps), ...extra } };
}

export const clamp = (n: number, low: number, high: number) => Math.max(low, Math.min(high, Math.round(n)));
