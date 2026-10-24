import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";
import { startWorkbench } from "../index.mjs";
import { ensurePipelineConfig } from "../settings.mjs";
import { applyFakeEnv } from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const FIXTURES = resolve(SKILL_DIR, "..", "_fixtures");

let workspace;
let tempRoot;
let bookDir;
let base;
let bookId;

async function request(path, options = {}) {
  const response = await fetch(`${base}${path}`, options);
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { status: response.status, body };
}

before(async () => {
  tempRoot = mkdtempSync(join(tmpdir(), "wb-resume-"));
  bookDir = join(tempRoot, "book");
  cpSync(resolve(FIXTURES, "chapter_e2e"), bookDir, { recursive: true });
  const dataDir = join(tempRoot, "data");
  applyFakeEnv();
  const settings = ensurePipelineConfig({ dataDir, rootsOverride: [tempRoot] });
  // 模拟崩溃：先发一个 token，让节点停在 running
  execFileSync(settings.python, [settings.autopilot, "next", "--book", bookDir], {
    env: { ...process.env, WORKBENCH_CONFIG: settings.pipeline_config_path },
    stdio: "ignore",
  });
  assert.equal(existsSync(join(bookDir, "_pipeline", ".step_token")), true);
  workspace = await startWorkbench({ port: 0, roots: [tempRoot], dataDir });
  base = workspace.url;
  const books = await request("/api/books");
  bookId = books.body.books[0].id;
});

after(async () => {
  await workspace?.close();
  rmSync(tempRoot, { recursive: true, force: true });
});

test("startWorkbench 能识别残留 token 并拒绝启动", async () => {
  const status = await request(`/api/books/${bookId}/run`);
  assert.equal(status.status, 200);
  assert.equal(status.body.needs_resume, true);

  const started = await request(`/api/books/${bookId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "mock", max_steps: 1 }),
  });
  assert.equal(started.status, 409);
  assert.equal(started.body.error.code, "needs_resume");
});

test("resume 清掉 token 后可继续运行", async () => {
  const resumed = await request(`/api/books/${bookId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  assert.equal(resumed.status, 200);
  assert.equal(existsSync(join(bookDir, "_pipeline", ".step_token")), false);

  const started = await request(`/api/books/${bookId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "mock", max_steps: 1 }),
  });
  assert.equal(started.status, 202);

  const deadline = Date.now() + 30_000;
  let run = null;
  while (Date.now() < deadline) {
    const status = await request(`/api/books/${bookId}/run?since=0`);
    run = status.body.run;
    if (run && !["running", "stopping"].includes(run.status)) break;
    await new Promise((accept) => setTimeout(accept, 150));
  }
  assert.ok(run);
  assert.equal(run.status, "max_steps");
  assert.equal(existsSync(join(bookDir, "_pipeline", ".step_token")), false);
});
