import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { openDatabase } from "../db.mjs";
import { RunManager } from "../run-manager.mjs";

function withManager(fn) {
  const dataDir = mkdtempSync(join(tmpdir(), "wb-run-events-"));
  const db = openDatabase({ dataDir });
  const manager = new RunManager({ db, dataDir });
  try {
    return fn(db, manager);
  } finally {
    db.close();
    rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

function fakeRun(db, id) {
  db.prepare("INSERT INTO books (path, synced_at) VALUES (?, ?)").run(`C:/tmp/${id}`, new Date().toISOString());
  const bookId = db.prepare("SELECT id FROM books WHERE path = ?").get(`C:/tmp/${id}`).id;
  db.prepare(`
    INSERT INTO runs (book_id, action, kind, requested_at, run_id, status, events_json)
    VALUES (?, 'agent_run', 'mock', ?, ?, 'running', '[]')
  `).run(bookId, new Date().toISOString(), id);
  const run = {
    id,
    bookId,
    bookPath: `C:/tmp/${id}`,
    mode: "mock",
    status: "running",
    startedAt: new Date().toISOString(),
    finishedAt: null,
    error: null,
    result: null,
    seq: 0,
    events: [],
    pendingEvents: [],
    controller: new AbortController(),
  };
  return { run, bookId };
}

test("事件超内存上限后 snapshot 仍能从 DB 补全早期历史", () => withManager((db, manager) => {
  const { run, bookId } = fakeRun(db, "run-overflow");
  manager.runs.set(bookId, run);
  for (let i = 1; i <= 1500; i += 1) {
    manager.push(run, { type: "tick", index: i });
  }

  // 内存仍保留上限，不无限增长
  assert.equal(run.events.length, 600);
  assert.equal(run.events[0].seq, 901);

  // DB 增量落库覆盖全量
  assert.equal(
    db.prepare("SELECT COUNT(*) AS n FROM run_events WHERE run_id = ?").get(run.id).n,
    1500,
  );

  // since=0：早期 900 条从 DB 读，晚期 600 条走内存，seq 连续无重复
  const all = manager.snapshot(bookId, 0);
  assert.equal(all.events.length, 1500);
  assert.equal(all.events[0].seq, 1);
  assert.equal(all.events[0].type, "tick");
  assert.equal(all.events.at(-1).seq, 1500);

  // since 落在内存区间内：只返回内存
  const tail = manager.snapshot(bookId, 1495);
  assert.equal(tail.events.length, 5);
  assert.equal(tail.events[0].seq, 1496);

  // since 低于内存起点：DB 段 + 内存段拼接
  const mixed = manager.snapshot(bookId, 900);
  assert.equal(mixed.events.length, 600);
  assert.equal(mixed.events[0].seq, 901);
}));

test("persist 把全量事件写入 events_json 并清空待落库批次", () => withManager((db, manager) => {
  const { run, bookId } = fakeRun(db, "run-persist");
  manager.runs.set(bookId, run);
  for (let i = 1; i <= 1300; i += 1) {
    manager.push(run, { type: "tick", index: i });
  }
  run.status = "done";
  run.result = { status: "done", steps: [] };
  run.finishedAt = new Date().toISOString();
  manager.persist(run);

  const row = db.prepare("SELECT events_json, status FROM runs WHERE run_id = ?").get(run.id);
  assert.equal(row.status, "done");
  const events = JSON.parse(row.events_json);
  assert.equal(events.length, 1300);
  assert.equal(events[0].seq, 1);
  assert.equal(events.at(-1).seq, 1300);
  assert.equal(run.pendingEvents.length, 0);
}));

test("未 flush 的尾部批次在 persist 时补落库", () => withManager((db, manager) => {
  const { run, bookId } = fakeRun(db, "run-tail");
  manager.runs.set(bookId, run);
  for (let i = 1; i <= 25; i += 1) {
    manager.push(run, { type: "tick", index: i });
  }
  // 25 条：20 条已批量落库，5 条还在待落库批次
  assert.equal(run.pendingEvents.length, 5);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM run_events WHERE run_id = ?").get(run.id).n, 20);

  run.status = "failed";
  run.error = "boom";
  run.finishedAt = new Date().toISOString();
  manager.persist(run);

  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM run_events WHERE run_id = ?").get(run.id).n, 25);
  assert.equal(run.pendingEvents.length, 0);
  const row = db.prepare("SELECT status, exit_code FROM runs WHERE run_id = ?").get(run.id);
  assert.equal(row.status, "failed");
  assert.equal(row.exit_code, 1);
}));
