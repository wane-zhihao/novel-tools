import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { AgentRuntime, resolveRuntimePaths } from "./agent/runtime.mjs";
import { defaultDataDir } from "./paths.mjs";

const ACTIVE = new Set(["running", "stopping"]);
const MAX_EVENTS = 600;

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

  push(run, event) {
    run.seq += 1;
    run.events.push({ seq: run.seq, at: new Date().toISOString(), ...event });
    if (run.events.length > MAX_EVENTS) run.events.splice(0, run.events.length - MAX_EVENTS);
    if (run.seq % 20 === 0) {
      try {
        this.db.prepare("UPDATE runs SET events_json = ? WHERE run_id = ?")
          .run(JSON.stringify(run.events.slice(-200)), run.id);
      } catch {}
    }
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
      events: run.events.filter((event) => event.seq > since),
    };
  }

  persist(run) {
    const lastNode = run.result?.steps?.at(-1)?.node ?? null;
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
      JSON.stringify(run.events.slice(-200)),
      run.id,
    );
    if (!result.changes) {
      this.db.prepare(`
        INSERT INTO runs (book_id, action, command, exit_code, node, kind, requested_at, executed_at, result_json, run_id, status, events_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        run.bookId, "agent_run", null, run.status === "done" ? 0 : 1, lastNode, run.mode,
        run.startedAt, run.finishedAt, JSON.stringify(run.result ?? { error: run.error }),
        run.id, run.status, JSON.stringify(run.events.slice(-200)),
      );
    }
  }
}
