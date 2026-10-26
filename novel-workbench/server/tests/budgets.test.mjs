import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { loadAgentConfig } from "../agent-config.mjs";
import { AgentRuntime } from "../agent/runtime.mjs";
import { applyFakeEnv } from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const FIXTURES = resolve(SKILL_DIR, "..", "_fixtures");

async function withBook(fn) {
  applyFakeEnv();
  const root = mkdtempSync(join(tmpdir(), "wb-budget-"));
  const bookDir = join(root, "book");
  cpSync(resolve(FIXTURES, "chapter_e2e"), bookDir, { recursive: true });
  try {
    return await fn({ root, bookDir, dataDir: join(root, "data") });
  } finally {
    try {
      rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
    } catch {
      // Windows 可能短暂占用临时文件；不因清理失败影响断言
    }
  }
}

test("completion token 超预算会立即停止", async () => {
  await withBook(async ({ bookDir, dataDir }) => {
    const loaded = loadAgentConfig({ bookDir, dataDir });
    const config = structuredClone(loaded.config);
    config.budgets.max_completion_tokens = 10;
      config.budgets.llm_rounds = 5;
    const runtime = new AgentRuntime({
      bookDir,
      config,
      dataDir,
      maxSteps: 1,
      runId: "budget-test-run",
      providerFactory: () => ({
        chat: async () => ({
          content: "超预算",
          toolCalls: [],
          usage: { prompt: 10, completion: 20 },
        }),
      }),
    });
    const result = await runtime.run();
    assert.equal(result.status, "budget_exhausted");
    assert.equal(result.llm_rounds, 1);
    const relay = JSON.parse(readFileSync(join(bookDir, "_pipeline", "relay.json"), "utf8"));
    assert.equal(relay.stop_reason, "budget_exhausted");
    assert.equal(relay.run_id, "budget-test-run");
    assert.equal(relay.cursor, result.book.cursor);
  });
});

test("节点超时不会无限重试", async () => {
  await withBook(async ({ bookDir, dataDir }) => {
    const loaded = loadAgentConfig({ bookDir, dataDir });
    const config = structuredClone(loaded.config);
    config.roles.writer.timeout_s = 1;
    config.roles.writer.max_attempts = 3;
    config.budgets.node_timeout_s = 1;
    const runtime = new AgentRuntime({
      bookDir,
      config,
      dataDir,
      maxSteps: 1,
      providerFactory: () => ({
        chat: ({ signal }) => new Promise((_, reject) => {
          signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
        }),
      }),
    });
    const result = await runtime.run();
    assert.equal(result.status, "failed");
    assert.match(result.error, /超时/);
    assert.equal(result.llm_rounds, 1);
  });
});
