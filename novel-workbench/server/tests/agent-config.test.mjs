import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadAgentConfig } from "../agent-config.mjs";

function withBookOverride(override, fn) {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-book-"));
  mkdirSync(join(bookDir, "_pipeline"), { recursive: true });
  writeFileSync(join(bookDir, "_pipeline", "agents.json"), JSON.stringify(override, null, 2), "utf8");
  try {
    return fn(bookDir);
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
}

test("默认配置合法，且默认全部是 mock", () => {
  const result = loadAgentConfig({ env: {} });
  assert.deepEqual(result.errors, []);
  assert.ok(result.warnings.some((warning) => warning.includes("mock")));
  assert.equal(result.config.roles.writer.model, "mock-writer");
  assert.equal(result.config.roles.judge.model, "mock-judge");
  const mock = result.providers.find((provider) => provider.name === "mock");
  assert.equal(mock.configured, true);
  const deepseek = result.providers.find((provider) => provider.name === "deepseek");
  assert.equal(deepseek.configured, false);
});

test("按书覆盖只改指定字段", () => withBookOverride(
  { roles: { writer: { model: "mock-writer-v2", temperature: 0.5 } } },
  (bookDir) => {
    const result = loadAgentConfig({ bookDir, env: {} });
    assert.deepEqual(result.errors, []);
    assert.equal(result.config.roles.writer.model, "mock-writer-v2");
    assert.equal(result.config.roles.writer.temperature, 0.5);
    assert.equal(result.config.roles.writer.provider, "mock");
    assert.equal(result.config.roles.judge.model, "mock-judge");
    assert.ok(result.overridePath?.endsWith("agents.json"));
  },
));

test("指向不存在的 provider 会报错", () => withBookOverride(
  { roles: { writer: { provider: "nope" } } },
  (bookDir) => {
    const result = loadAgentConfig({ bookDir, env: {} });
    assert.ok(result.errors.some((error) => error.includes("不存在的 provider")));
  },
));

test("writer 与 judge 同模型会告警", () => withBookOverride(
  { roles: { judge: { provider: "mock", model: "mock-writer" } } },
  (bookDir) => {
    const result = loadAgentConfig({ bookDir, env: {} });
    assert.deepEqual(result.errors, []);
    assert.ok(result.warnings.some((warning) => warning.includes("同一个 provider/model")));
  },
));

test("密钥只读环境变量，响应里不出现密钥值", () => {
  const secret = "sk-test-secret-value";
  const result = loadAgentConfig({ env: { DEEPSEEK_API_KEY: secret } });
  const deepseek = result.providers.find((provider) => provider.name === "deepseek");
  assert.equal(deepseek.configured, true);
  assert.equal(deepseek.env, "DEEPSEEK_API_KEY");
  assert.ok(!JSON.stringify(result).includes(secret));
});
