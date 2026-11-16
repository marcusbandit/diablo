import { Database } from "bun:sqlite";

import type { Param, Sql } from "../src/store/sql";

/** The store's SQL over bun:sqlite, in memory, for the tests. */
export function memorySql(): Sql {
  const db = new Database(":memory:");
  return {
    exec: (source) => db.exec(source),
    run: (source, params: Param[] = []) => ({ changes: db.query(source).run(...params).changes }),
    all: <T,>(source: string, params: Param[] = []) => db.query(source).all(...params) as T[],
    first: <T,>(source: string, params: Param[] = []) => (db.query(source).get(...params) as T | null) ?? null,
    tx: (task) => db.transaction(task)(),
  };
}
