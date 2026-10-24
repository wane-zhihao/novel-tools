import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { deleteSecret, getSecret, secretInfo, setSecret } from "../secrets.mjs";

test("环境变量优先于本地密钥存储", () => {
  const dataDir = mkdtempSync(join(tmpdir(), "wb-secrets-"));
  try {
    assert.deepEqual(getSecret("X", { dataDir, env: { X: "from-env" } }), {
      value: "from-env",
      source: "env",
    });
  } finally {
    rmSync(dataDir, { recursive: true, force: true });
  }
});

test("密钥可加密存取、删除，且文件不含明文", () => {
  const dataDir = mkdtempSync(join(tmpdir(), "wb-secrets-"));
  const secret = "sk-test-do-not-leak-1234567890";
  try {
    const saved = setSecret("DEEPSEEK_API_KEY", secret, { dataDir });
    assert.equal(saved.configured, true);
    const stored = readFileSync(join(dataDir, "secrets.json"), "utf8");
    assert.ok(!stored.includes(secret), "密钥文件不应出现明文");
    assert.equal(secretInfo({ dataDir }).names.length, 1);

    const read = getSecret("DEEPSEEK_API_KEY", { dataDir, env: {} });
    assert.equal(read.value, secret);
    assert.equal(read.source, "store");

    deleteSecret("DEEPSEEK_API_KEY", { dataDir });
    assert.equal(getSecret("DEEPSEEK_API_KEY", { dataDir, env: {} }).value, null);
  } finally {
    rmSync(dataDir, { recursive: true, force: true });
  }
});
