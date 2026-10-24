import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { SCHEMA_VERSION, migrations } from "./schema.mjs";

function appliedVersion(db) {
  try {
    const row = db.prepare("SELECT MAX(version) AS version FROM schema_migrations").get();
    return Number(row?.version) || 0;
  } catch {
    return 0;
  }
}

export function openDatabase({ dataDir, file = "workbench.db" } = {}) {
  if (!dataDir) throw new Error("openDatabase 需要 dataDir");
  mkdirSync(dataDir, { recursive: true });
  const path = join(dataDir, file);
  const existed = existsSync(path);
  const db = new DatabaseSync(path);
  db.exec("PRAGMA journal_mode = WAL");
  db.exec("PRAGMA foreign_keys = ON");
  db.exec("PRAGMA busy_timeout = 5000");
  if (existed) {
    const current = appliedVersion(db);
    if (current < SCHEMA_VERSION) {
      const backup = `${path}.bak-v${current}`;
      if (!existsSync(backup)) {
        try {
          db.exec("PRAGMA wal_checkpoint(FULL)");
          copyFileSync(path, backup);
        } catch {}
      }
    }
  }
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
    // 覆盖全部业务表（含 migration4 新增的 versions/story_docs/approvals/analytics_events
    // 与 migration5 的 run_events），schema_migrations 不动
    for (const table of [
      "submissions", "runs", "run_events", "judgments", "findings", "nodes",
      "versions", "story_docs", "approvals", "analytics_events", "chapters", "books",
    ]) {
      db.exec(`DELETE FROM ${table}`);
    }
    db.exec("COMMIT");
  } catch (error) {
    db.exec("ROLLBACK");
    throw error;
  }
}
