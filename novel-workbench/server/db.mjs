import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { migrations } from "./schema.mjs";

export function openDatabase({ dataDir, file = "workbench.db" } = {}) {
  if (!dataDir) throw new Error("openDatabase 需要 dataDir");
  mkdirSync(dataDir, { recursive: true });
  const db = new DatabaseSync(join(dataDir, file));
  db.exec("PRAGMA journal_mode = WAL");
  db.exec("PRAGMA foreign_keys = ON");
  db.exec("PRAGMA busy_timeout = 5000");
  migrate(db);
  return db;
}

export function migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      applied_at TEXT NOT NULL
    )
  `);
  const applied = new Set(
    db.prepare("SELECT version FROM schema_migrations").all().map((row) => row.version),
  );
  for (const migration of migrations) {
    if (applied.has(migration.version)) continue;
    db.exec("BEGIN");
    try {
      migration.up(db);
      db.prepare("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)").run(
        migration.version,
        migration.name,
        new Date().toISOString(),
      );
      db.exec("COMMIT");
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  }
}

/** 删掉所有业务数据，保留迁移记录；用于从文件全量重建。 */
export function resetData(db) {
  db.exec("BEGIN");
  try {
    for (const table of ["submissions", "runs", "judgments", "findings", "nodes", "chapters", "books"]) {
      db.exec(`DELETE FROM ${table}`);
    }
    db.exec("COMMIT");
  } catch (error) {
    db.exec("ROLLBACK");
    throw error;
  }
}
