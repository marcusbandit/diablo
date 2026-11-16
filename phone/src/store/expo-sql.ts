import { openDatabaseSync } from "expo-sqlite";

import type { Param, Sql } from "./sql";

export function openExpoSql(name = "diablo.db"): Sql {
  const db = openDatabaseSync(name);
  db.execSync("PRAGMA journal_mode = WAL");
  return {
    exec: (source) => db.execSync(source),
    run: (source, params: Param[] = []) => db.runSync(source, params),
    all: <T,>(source: string, params: Param[] = []) => db.getAllSync<T>(source, params),
    first: <T,>(source: string, params: Param[] = []) => db.getFirstSync<T>(source, params),
    tx: (task) => db.withTransactionSync(task),
  };
}
