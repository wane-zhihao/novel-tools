import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { detectPython } from "../settings.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));

test("Python 契约：N13 状态矩阵 / N16 幂等 / N21 不可投", (t) => {
  const python = detectPython();
  if (!python) {
    t.skip("本机没有可执行的 Python");
    return;
  }
  const output = execFileSync(python, [join(TEST_DIR, "python_contracts.py")], {
    encoding: "utf8",
    timeout: 60_000,
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
  });
  assert.match(output, /PY_CONTRACTS_OK/);
});
