import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { ensurePipelineConfig } from "../settings.mjs";
import { applyFakeEnv } from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const FIXTURES = resolve(SKILL_DIR, "..", "_fixtures");

test("拒绝未验收的陈旧证据并跨 resume 复用同 hash", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-stale-"));
  const book = join(root, "book");
  cpSync(resolve(FIXTURES, "chapter_e2e"), book, { recursive: true });
  const dataDir = join(root, "data");
  try {
    applyFakeEnv();
    const settings = ensurePipelineConfig({ dataDir, rootsOverride: [root] });
    const env = { ...process.env, WORKBENCH_CONFIG: settings.pipeline_config_path };
    execFileSync(settings.python, [settings.autopilot, "next", "--book", book], { env, stdio: "ignore" });
    const token = JSON.parse(readFileSync(join(book, "_pipeline", ".step_token"), "utf8"));

    const evidence = join(book, "大纲", "细纲_第003章.md");
    writeFileSync(evidence, "- 核心事件：测试\n" + "内容".repeat(400), "utf8");
    const old = new Date(Date.now() - 3_600_000);
    utimesSync(evidence, old, old);

    let stale = true;
    try {
      execFileSync(settings.python, [
        settings.autopilot, "done", "--book", book,
        "--node", token.node, "--token", token.token,
        "--evidence", "大纲/细纲_第003章.md",
      ], { env, encoding: "utf8", stdio: "pipe" });
      stale = false;
    } catch (error) {
      const output = `${error.stdout ?? ""}${error.stderr ?? ""}`;
      assert.match(output, /stale_evidence/);
    }
    assert.equal(stale, true, "旧证据不应通过 done");

    const now = new Date();
    utimesSync(evidence, now, now);
    const output = execFileSync(settings.python, [
      settings.autopilot, "done", "--book", book,
      "--node", token.node, "--token", token.token,
      "--evidence", "大纲/细纲_第003章.md",
    ], { env, encoding: "utf8" });
    assert.match(output, /"ok": true/);

    const statePath = join(book, "_pipeline", "state.json");
    const state = JSON.parse(readFileSync(statePath, "utf8"));
    state.cursor = token.node;
    writeFileSync(statePath, JSON.stringify(state), "utf8");
    execFileSync(settings.python, [settings.autopilot, "resume", "--book", book, "--force"], {
      env, encoding: "utf8", stdio: "pipe",
    });
    execFileSync(settings.python, [settings.autopilot, "next", "--book", book], { env, stdio: "ignore" });
    const resumedToken = JSON.parse(readFileSync(join(book, "_pipeline", ".step_token"), "utf8"));
    utimesSync(evidence, old, old);
    const acceptedOutput = execFileSync(settings.python, [
      settings.autopilot, "done", "--book", book,
      "--node", resumedToken.node, "--token", resumedToken.token,
      "--evidence", "大纲/细纲_第003章.md",
    ], { env, encoding: "utf8" });
    assert.match(acceptedOutput, /"ok": true/);
    assert.match(acceptedOutput, /"reused_evidence": true/);
    const acceptedState = JSON.parse(readFileSync(statePath, "utf8"));
    assert.equal(Object.keys(acceptedState.evidence_ledger).some((key) => key.startsWith(`${resumedToken.node}::`)), true);

    acceptedState.cursor = resumedToken.node;
    writeFileSync(statePath, JSON.stringify(acceptedState), "utf8");
    execFileSync(settings.python, [settings.autopilot, "resume", "--book", book, "--force"], {
      env, encoding: "utf8", stdio: "pipe",
    });
    execFileSync(settings.python, [settings.autopilot, "next", "--book", book], { env, stdio: "ignore" });
    const changedToken = JSON.parse(readFileSync(join(book, "_pipeline", ".step_token"), "utf8"));
    writeFileSync(evidence, "- 已修改：不应复用旧 hash\n" + "变更内容".repeat(400), "utf8");
    utimesSync(evidence, old, old);
    let changedRejected = false;
    try {
      execFileSync(settings.python, [
        settings.autopilot, "done", "--book", book,
        "--node", changedToken.node, "--token", changedToken.token,
        "--evidence", "大纲/细纲_第003章.md",
      ], { env, encoding: "utf8", stdio: "pipe" });
    } catch (error) {
      changedRejected = true;
      assert.match(`${error.stdout ?? ""}${error.stderr ?? ""}`, /stale_evidence/);
    }
    assert.equal(changedRejected, true, "已验收文件内容改变后，旧 mtime 证据仍应拒绝");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
