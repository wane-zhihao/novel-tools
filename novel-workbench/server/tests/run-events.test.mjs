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

// ── Phase 1.2 运行态可观测：区分「在跑」与「卡死」 ────────────────────
test("snapshot 暴露 current_node / chapter / node_started_at / heartbeat_at", () => withManager((db, manager) => {
  const { run, bookId } = fakeRun(db, "run-observable");
  manager.runs.set(bookId, run);

  // 还没任何事件：心跳回落到 startedAt，节点为空
  let snap = manager.snapshot(bookId);
  assert.equal(snap.current_node, null);
  assert.equal(snap.chapter, null);
  assert.ok(snap.heartbeat_at, "无事件时心跳应回落到开始时间");
  assert.ok(snap.elapsed_ms >= 0);
  assert.equal(snap.phase, "running");

  // 进入第 3 章某节点：节点名可解析出章号
  manager.push(run, { type: "node_started", node: "C3.N14" });
  snap = manager.snapshot(bookId);
  assert.equal(snap.current_node, "C3.N14");
  assert.equal(snap.chapter, 3);
  assert.ok(snap.node_started_at, "node_started_at 应来自该事件的 at");
  assert.equal(snap.heartbeat_at, snap.node_started_at);
  assert.ok(snap.heartbeat_age_ms >= 0);

  // 又过了若干事件：心跳前进到最新事件的时间，节点仍是 C3.N14（直到下一个 node_started）
  manager.push(run, { type: "tool_call", node: "C3.N14", tool: "write_evidence", ok: true });
  snap = manager.snapshot(bookId);
  assert.equal(snap.current_node, "C3.N14");
  // 精确比对而不是 notEqual：同一毫秒内两次 push 的时间戳可能相同
  assert.equal(snap.heartbeat_at, run.events.at(-1).at, "心跳应等于最新事件的时间");

  // 非章节节点（N03）没有章号，不该瞎猜
  manager.push(run, { type: "node_started", node: "N03" });
  snap = manager.snapshot(bookId);
  assert.equal(snap.current_node, "N03");
  assert.equal(snap.chapter, null);

  // 运行结束：current_node 清空，phase 变为终态，elapsed 冻结
  run.status = "done";
  run.finishedAt = new Date().toISOString();
  snap = manager.snapshot(bookId);
  assert.equal(snap.current_node, null, "结束后不该再显示在跑节点");
  assert.equal(snap.node_started_at, null);
  assert.equal(snap.phase, "finished");
  const frozen = snap.elapsed_ms;
  assert.ok(frozen >= 0);
}));

test("snapshot 的 phase 区分 running / stopping / blocked / failed", () => withManager((db, manager) => {
  const { run, bookId } = fakeRun(db, "run-phase");
  manager.runs.set(bookId, run);
  const phaseOf = (status) => {
    run.status = status;
    return manager.snapshot(bookId).phase;
  };
  assert.equal(phaseOf("running"), "running");
  assert.equal(phaseOf("stopping"), "stopping");
  assert.equal(phaseOf("blocked"), "blocked");
  assert.equal(phaseOf("failed"), "failed");
  assert.equal(phaseOf("done"), "finished");
  assert.equal(phaseOf("max_steps"), "finished");
}));

test("定时心跳：无新事件也能证明进程还活着，且不污染事件流", () => withManager((db) => {
  // 用短周期注入，避免测试等 15 秒
  const dataDir = mkdtempSync(join(tmpdir(), "wb-hb-"));
  const manager = new RunManager({ db, dataDir, heartbeatMs: 25 });
  const { run, bookId } = fakeRun(db, "run-heartbeat");
  manager.runs.set(bookId, run);
  manager.startHeartbeat(run);
  manager.push(run, { type: "node_started", node: "C3.N14" });
  const nodeAt = manager.snapshot(bookId).node_started_at;
  const eventsBefore = manager.snapshot(bookId).seq;

  // 等到心跳至少推进一次
  return new Promise((resolve) => {
    setTimeout(() => {
      const snap = manager.snapshot(bookId);
      assert.equal(snap.node_started_at, nodeAt, "节点开始时间不该被心跳改写");
      assert.ok(snap.heartbeat_at > nodeAt,
        `心跳应自行前进（node=${nodeAt} heartbeat=${snap.heartbeat_at}）`);
      assert.equal(snap.seq, eventsBefore, "心跳不得往事件流里塞东西");
      assert.ok(snap.heartbeat_age_ms < 1000, "心跳新鲜度应接近 0");
      manager.stopHeartbeat(run);
      assert.equal(run.heartbeatTimer, null, "停止后不应留下定时器");
      rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
      resolve();
    }, 120);
  });
}));

test("start() 自动启动心跳，dispose 不残留定时器", () => withManager((db) => {
  const dataDir = mkdtempSync(join(tmpdir(), "wb-hb2-"));
  const manager = new RunManager({ db, dataDir, heartbeatMs: 25 });
  const { bookId, run } = fakeRun(db, "run-hb-wiring");

  // 直接验证接线：不管这次运行是否秒败，start() 都必须去启动心跳。
  // （秒败时 execute 的 finally 会同步停掉定时器，所以不能靠 heartbeatTimer 反推。）
  let started = 0;
  const realStart = manager.startHeartbeat.bind(manager);
  manager.startHeartbeat = (target) => { started += 1; realStart(target); };

  const snapshot = manager.start({
    bookId, bookPath: run.bookPath, mode: "mock", maxSteps: 1,
    config: { roles: {}, providers: {}, budgets: {} },
  });
  assert.ok(snapshot, "start 应返回快照");
  assert.equal(started, 1, "start() 必须启动心跳，否则 UI 无法区分卡死");

  // dispose 必须清干净（含秒败后残留的情况）
  manager.dispose();
  for (const live of manager.runs.values()) {
    assert.equal(live.heartbeatTimer ?? null, null, "dispose 后不该有定时器残留");
  }
  rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}));
