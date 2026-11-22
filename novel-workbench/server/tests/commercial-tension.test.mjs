import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { detectPython } from "../settings.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));

test("七猫商业签约级张力探针与模板套件回归测试（自检 / 模板完备 / 黄金三章 / 开篇阻断 / 终审全绿）", (t) => {
  const python = detectPython();
  if (!python) {
    t.skip("本机没有可执行的 Python");
    return;
  }
  const output = execFileSync(python, [join(TEST_DIR, "test_commercial_tension.py")], {
    encoding: "utf8",
    timeout: 60_000,
    env: { ...process.env, FAKE_NODE: process.execPath, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
  });
  assert.match(output, /ALL COMMERCIAL TENSION TESTS PASSED!/);
});
