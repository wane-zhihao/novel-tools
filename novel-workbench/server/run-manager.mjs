import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { AgentRuntime, resolveRuntimePaths } from "./agent/runtime.mjs";
import { defaultDataDir } from "./paths.mjs";
import { recordVersion } from "./versions.mjs";

const ACTIVE = new Set(["running", "stopping"]);
const MAX_EVENTS = 600;
// B12：事件按 seq 增量落库；内存只保留近窗，早期事件从 run_events 补读，不再永久丢失
const EVENT_FLUSH_SIZE = 20;
const EVENTS_JSON_FLUSH = 100;

export class RunConflictError extends Error {
  constructor(message) {
    super(message);
    this.name = "RunConflictError";
    this.code = "run_running";
  }
}

export class RunNeedsResumeError extends Error {
  constructor(message = "上次执行被中断，先恢复再继续") {
    super(message);
    this.name = "RunNeedsResumeError";
    this.code = "needs_resume";
  }
}

export function hasStepToken(bookPath) {
  return existsSync(join(bookPath, "_pipeline", ".step_token"));
}

export function markInterruptedRuns(db) {
  db.prepare(
    "UPDATE runs SET status = 'interrupted' WHERE action = 'agent_run' AND status = 'running'",
  ).run();
}

export class RunManager {
  constructor({ db, dataDir = defaultDataDir(), onFinished = null, onError = null } = {}) {
    this.db = db;
    this.dataDir = dataDir;
    this.onFinished = onFinished;
    this.onError = onError;
    this.runs = new Map();
  }

  active(bookId) {
    return ACTIVE.has(this.runs.get(bookId)?.status);
  }

  start({ bookId, bookPath, config, mode = "real", maxSteps = Infinity } = {}) {
    if (this.active(bookId)) throw new RunConflictError("这本书已有一次执行在进行");
    if (!this.active(bookId) && hasStepToken(bookPath)) {
      throw new RunNeedsResumeError();
    }
    const run = {
      id: randomUUID(),
      bookId,
      bookPath,
      mode,
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
    this.runs.set(bookId, run);
    this.db.prepare(`
      INSERT INTO runs (book_id, action, command, exit_code, node, kind, requested_at, executed_at, result_json, run_id, status, events_json)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      run.bookId, "agent_run", null, null, null, run.mode, run.startedAt,
      null, null, run.id, "running", "[]",
    );
    run.task = this.execute(run, config, maxSteps);
    return this.snapshot(bookId);
  }

  async execute(run, config, maxSteps) {
    try {
      const paths = resolveRuntimePaths({ dataDir: this.dataDir });
      const runtime = new AgentRuntime({
        bookDir: run.bookPath,
        config,
        signal: run.controller.signal,
        maxSteps,
        dataDir: this.dataDir,
        paths,
        readRoots: paths.readRoots,
        onEvent: (event) => this.push(run, event),
        onFileWrite: (change) => this.recordAiWrite(run, change),
      });
      const result = await runtime.run();
      run.status = result.status;
      run.result = result;
    } catch (error) {
      run.status = "failed";
      run.error = error.message;
    } finally {
      run.finishedAt = new Date().toISOString();
      try {
        this.persist(run);
      } catch (error) {
        this.onError?.(error);
      }
      try {
        this.onFinished?.(run);
      } catch (error) {
        this.onError?.(error);
      }
    }
  }

  recordAiWrite(run, change) {
    const normalized = String(change?.relativePath ?? "").replaceAll("\\", "/");
    const match = /^正文\/第(\d{3})章_/.exec(normalized);
    if (!match) return;
    const chapter = Number(match[1]);
    const node = change.node || "ai";
    try {
      if (typeof change.before === "string" && change.before !== change.after) {
        recordVersion(this.db, {
          bookDir: run.bookPath,
          bookId: run.bookId,
          chapter,
          content: change.before,
          source: `ai-before-${node}`,
          kind: "ai-before",
        });
      }
      const version = recordVersion(this.db, {
        bookDir: run.bookPath,
        bookId: run.bookId,
        chapter,
        content: change.after,
        source: node.includes("N14") || node.includes("N20f") ? "ai-rewrite" : "ai-draft",
        kind: node.includes("N14") || node.includes("N20f") ? "ai-rewrite" : "ai-draft",
      });
      const row = this.db.prepare(
        "SELECT committed FROM chapters WHERE book_id = ? AND number = ?",
      ).get(run.bookId, chapter);
      if (row?.committed) {
        this.db.prepare("UPDATE chapters SET dirty = 1, author_state = 'ai_edit' WHERE book_id = ? AND number = ?")
          .run(run.bookId, chapter);
      }
      this.push(run, {
        type: "ai_version",
        node,
        chapter,
        version_id: version?.id ?? null,
        words: version?.words ?? null,
      });
    } catch {}
  }

  push(run, event) {
    run.seq += 1;
    const record = { seq: run.seq, at: new Date().toISOString(), ...event };
    run.events.push(record);
    if (run.events.length > MAX_EVENTS) run.events.splice(0, run.events.length - MAX_EVENTS);
    run.pendingEvents.push(record);
    if (run.pendingEvents.length >= EVENT_FLUSH_SIZE) this.flushEvents(run);
    if (run.seq % EVENTS_JSON_FLUSH === 0) this.flushEventsJson(run);
  }

  /** 增量落库：每批 EVENT_FLUSH_SIZE 条 INSERT，崩溃最多丢一批未落库事件（内存仍有） */
  flushEvents(run) {
    if (!run.pendingEvents.length) return;
    const batch = run.pendingEvents;
    run.pendingEvents = [];
    const insert = this.db.prepare(
      "INSERT OR REPLACE INTO run_events (run_id, seq, event_json, created_at) VALUES (?, ?, ?, ?)",
    );
    try {
      this.db.exec("BEGIN");
      for (const record of batch) {
        insert.run(run.id, record.seq, JSON.stringify(record), record.at);
      }
      this.db.exec("COMMIT");
    } catch {
      try {
        this.db.exec("ROLLBACK");
      } catch {}
      // 落库失败不阻断执行；把这批放回，下次重试
      run.pendingEvents = [...batch, ...run.pendingEvents];
    }
  }

  /** events_json 供 /api/runs 与重启后的 last-run 回退读取，周期性刷新近窗 */
  flushEventsJson(run) {
    try {
      this.db.prepare("UPDATE runs SET events_json = ? WHERE run_id = ?")
        .run(JSON.stringify(run.events), run.id);
    } catch {}
  }

  stop(bookId) {
    const run = this.runs.get(bookId);
    if (!run || !ACTIVE.has(run.status)) return false;
    run.status = "stopping";
    this.push(run, { type: "stop_requested" });
    run.controller.abort();
    return true;
  }

  snapshot(bookId, since = 0) {
    const run = this.runs.get(bookId);
    if (!run) return null;
    const from = Number.isInteger(since) && since > 0 ? since : 0;
    const memory = run.events.filter((event) => event.seq > from);
    // 内存起点之前的早期事件从 run_events 补读，长跑不再丢 run_started 等历史
    const firstInMemory = run.events.length ? run.events[0].seq : Number.MAX_SAFE_INTEGER;
    let events = memory;
    if (from < firstInMemory) {
      try {
        const older = this.db.prepare(
          "SELECT event_json FROM run_events WHERE run_id = ? AND seq > ? AND seq < ? ORDER BY seq",
        ).all(run.id, from, firstInMemory);
        if (older.length) {
          events = [...older.map((row) => JSON.parse(row.event_json)), ...memory];
        }
      } catch {}
    }
    return {
      id: run.id,
      book_id: run.bookId,
      mode: run.mode,
      status: run.status,
      started_at: run.startedAt,
      finished_at: run.finishedAt,
      error: run.error,
      result: run.result,
      seq: run.seq,
      events,
    };
  }

  persist(run) {
    this.flushEvents(run);
    const lastNode = run.result?.steps?.at(-1)?.node ?? null;
    // events_json 写全量历史（run_events 即真相），重启后 UI 回退读取也不丢早期事件
    let eventsJson;
    try {
      const rows = this.db.prepare(
        "SELECT event_json FROM run_events WHERE run_id = ? ORDER BY seq",
      ).all(run.id);
      eventsJson = `[${rows.map((row) => row.event_json).join(",")}]`;
    } catch {
      eventsJson = JSON.stringify(run.events);
    }
    const result = this.db.prepare(`
      UPDATE runs SET
        exit_code = ?, node = ?, executed_at = ?, result_json = ?, status = ?, events_json = ?
      WHERE run_id = ?
    `).run(
      run.status === "done" ? 0 : 1,
      lastNode,
      run.finishedAt,
      JSON.stringify(run.result ?? { error: run.error }),
      run.status,
      eventsJson,
      run.id,
    );
    if (!result.changes) {
      this.db.prepare(`
        INSERT INTO runs (book_id, action, command, exit_code, node, kind, requested_at, executed_at, result_json, run_id, status, events_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        run.bookId, "agent_run", null, run.status === "done" ? 0 : 1, lastNode, run.mode,
        run.startedAt, run.finishedAt, JSON.stringify(run.result ?? { error: run.error }),
        run.id, run.status, eventsJson,
      );
    }
  }
}
