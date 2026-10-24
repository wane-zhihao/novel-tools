import assert from "node:assert/strict";
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
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

test("mock 运行时用真实 autopilot 推进一步并封住令牌", async () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-run-"));
  cpSync(resolve(FIXTURES, "chapter_e2e"), bookDir, { recursive: true });
  try {
    applyFakeEnv();
    const loaded = loadAgentConfig({ bookDir, env: {} });
    assert.deepEqual(loaded.errors, []);
    const config = structuredClone(loaded.config);

    const runtime = new AgentRuntime({
      bookDir,
      config,
      env: {},
      maxSteps: 1,
    });
    const result = await runtime.run();

    assert.equal(result.status, "max_steps");
    assert.equal(result.llm_rounds, 1);
    assert.equal(result.steps.length, 1);
    assert.equal(result.steps[0].node, "C3.N09");

    const state = JSON.parse(readFileSync(join(bookDir, "_pipeline", "state.json"), "utf8"));
    assert.equal(state.cursor, "C3.N10");
    assert.equal(state.nodes["C3.N09"].status, "done");
    assert.ok(!existsSync(join(bookDir, "_pipeline", ".step_token")));

    const outline = join(bookDir, "大纲", "细纲_第003章.md");
    assert.ok(existsSync(outline));
    assert.ok(statSync(outline).size >= 500);

    const serialized = JSON.stringify(result);
    assert.ok(!/token/i.test(serialized), "结果中不应出现 token");
    assert.ok(!serialized.includes("done_cmd"), "结果中不应出现 done_cmd");
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
});
