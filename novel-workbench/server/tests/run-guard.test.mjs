import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";
import { startWorkbench } from "../index.mjs";
import { applyFakeEnv } from "./helpers.mjs";

// real 模式前置校验：杜绝「mode=real 但角色仍是 mock」把占位文本静默写进正文。
const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURES = resolve(TEST_DIR, "..", "..", "..", "_fixtures");

let workspace;
let tempRoot;
let bookDir;
let base;
let bookId;

async function post(path, payload = {}, headers = {}) {
  const response = await fetch(`${base}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  return { status: response.status, body };
}

before(async () => {
  applyFakeEnv();
  tempRoot = mkdtempSync(join(tmpdir(), "wb-run-guard-"));
  bookDir = join(tempRoot, "guardbook");
  cpSync(resolve(FIXTURES, "chapter_e2e"), bookDir, { recursive: true });
  workspace = await startWorkbench({ port: 0, roots: [tempRoot], dataDir: join(tempRoot, "data") });
  base = workspace.url;
  const list = await fetch(`${base}/api/books`).then((r) => r.json());
  bookId = list.books[0].id;
});

after(async () => {
  await workspace?.close();
  rmSync(tempRoot, { recursive: true, force: true });
  delete process.env.DEEPSEEK_API_KEY;
});

function writeBookAgents(config) {
  mkdirSync(join(bookDir, "_pipeline"), { recursive: true });
  writeFileSync(join(bookDir, "_pipeline", "agents.json"), JSON.stringify(config), "utf8");
}

test("real 模式 + 默认全 mock 角色：拒绝启动（演练除外）", async () => {
  const res = await post(`/api/books/${bookId}/run`, { mode: "real", max_steps: 1 });
  assert.equal(res.status, 400);
  assert.equal(res.body.error.code, "ai_roles_not_configured", JSON.stringify(res.body.error));
  assert.equal(res.body.error.details.role, "writer", JSON.stringify(res.body.error.details));
  // 拒绝必须发生在任何 LLM 调用之前：不写正文、不留节点在途状态
  const { readdirSync } = await import("node:fs");
  assert.equal(readdirSync(join(bookDir, "正文")).length, 2, "被拒的运行不该产出新正文");
});

test("real 模式 + 角色指向缺密钥的服务：provider_key_missing", async () => {
  delete process.env.DEEPSEEK_API_KEY;
  writeBookAgents({
    roles: { writer: {
      provider: "deepseek", model: "deepseek-chat", temperature: 0.8,
      timeout_s: 300, max_attempts: 2, max_tokens: 4096,
    } },
  });
  try {
    const res = await post(`/api/books/${bookId}/run`, { mode: "real", max_steps: 1 });
    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, "provider_key_missing", JSON.stringify(res.body.error));
    assert.equal(res.body.error.details.env, "DEEPSEEK_API_KEY");
  } finally {
    rmSync(join(bookDir, "_pipeline", "agents.json"), { force: true });
  }
});

test("mock 模式不受前置校验影响（演练入口保留）", async () => {
  const res = await post(`/api/books/${bookId}/run`, { mode: "mock", max_steps: 1 });
  assert.equal(res.status, 202, JSON.stringify(res.body));
  await post(`/api/books/${bookId}/stop`);
});
