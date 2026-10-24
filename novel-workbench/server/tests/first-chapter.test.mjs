import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadAgentConfig } from "../agent-config.mjs";
import { AgentRuntime } from "../agent/runtime.mjs";
import { ensurePipelineConfig } from "../settings.mjs";
import { applyFakeEnv } from "./helpers.mjs";

test("空书用 mock 跑到 C1.N12 完成（通配证据可解析）", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "wb-first-"));
  const dataDir = join(root, "data");
  const bookDir = join(root, "book");
  try {
    applyFakeEnv();
    const settings = ensurePipelineConfig({ dataDir, rootsOverride: [root] });
    if (settings.errors.length) {
      t.skip(`本机缺少流水线依赖：${settings.errors.join("；")}`);
      return;
    }
    execFileSync(settings.python, [
      settings.autopilot, "init",
      "--title", "首章测试",
      "--genre", "niandai",
      "--target-words", "20000",
      "--max-chapters", "12",
      "--book", bookDir,
    ], {
      env: { ...process.env, WORKBENCH_CONFIG: settings.pipeline_config_path },
      stdio: "ignore",
    });

    const loaded = loadAgentConfig({ bookDir });
    assert.deepEqual(loaded.errors, []);
    const config = structuredClone(loaded.config);
    const runtime = new AgentRuntime({
      bookDir,
      config,
      dataDir,
      maxSteps: 9,
    });
    const result = await runtime.run();
    assert.equal(result.status, "max_steps", result.error ?? "");

    const state = JSON.parse(readFileSync(join(bookDir, "_pipeline", "state.json"), "utf8"));
    assert.equal(state.nodes["C1.N12"].status, "done");
    assert.ok(!existsSync(join(bookDir, "_pipeline", ".step_token")));

    const bodyDir = join(bookDir, "正文");
    const chapters = readdirSync(bodyDir).filter((name) => name.startsWith("第001章_") && name.endsWith(".md"));
    assert.equal(chapters.length, 1);
    assert.ok(statSync(join(bodyDir, chapters[0])).size >= 1800);

    const serialized = JSON.stringify(result);
    assert.ok(!/token/i.test(serialized));
    assert.ok(!serialized.includes("done_cmd"));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
