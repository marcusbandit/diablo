/** The slice of SQLite the store needs. expo-sqlite in the app, bun:sqlite in the tests. */
export type Param = string | number | null;

export interface Sql {
  exec(source: string): void;
  run(source: string, params?: Param[]): { changes: number };
  all<T>(source: string, params?: Param[]): T[];
  first<T>(source: string, params?: Param[]): T | null;
  tx(task: () => void): void;
}
