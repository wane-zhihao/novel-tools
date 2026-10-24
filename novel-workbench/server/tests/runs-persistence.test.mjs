import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { openDatabase } from "../db.mjs";
import { markInterruptedRuns } from "../run-manager.mjs";

test("启动时把残留 running 运行标为 interrupted", () => {
  const dataDir = mkdtempSync(join(tmpdir(), "wb-runs-"));
  const db = openDatabase({ dataDir });
  try {
    db.prepare("INSERT INTO books (path, synced_at) VALUES (?, ?)").run("C:/tmp/book", "2026-10-25T00:00:00Z");
    db.prepare(`
      INSERT INTO runs (book_id, action, command, exit_code, node, kind, requested_at, executed_at, result_json, run_id, status, events_json)
      VALUES (1, 'agent_run', NULL, NULL, NULL, 'mock', '2026-10-25T00:00:00.000Z', NULL, NULL, 'run-1', 'running', '[]')
    `).run();
    db.prepare(`
      INSERT INTO runs (book_id, action, command, exit_code, node, kind, requested_at, executed_at, result_json, run_id, status, events_json)
      VALUES (1, 'agent_run', NULL, 0, 'C1.N12', 'mock', '2026-10-25T00:01:00.000Z', '2026-10-25T00:02:00.000Z', '{}', 'run-2', 'done', '[]')
    `).run();

    markInterruptedRuns(db);
    const rows = db.prepare("SELECT run_id, status FROM runs ORDER BY run_id").all();
    assert.deepEqual(rows.map((row) => [row.run_id, row.status]), [
      ["run-1", "interrupted"],
      ["run-2", "done"],
    ]);
  } finally {
    db.close();
    try {
      rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    } catch {}
  }
});
