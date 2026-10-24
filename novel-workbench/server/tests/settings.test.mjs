import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { effectiveSettings, saveSettings, SettingsError } from "../settings.mjs";
import { loadWorkbenchConfig, SKILL_DIR } from "../paths.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_SKILL_DIR = resolve(TEST_DIR, "..", "..");
const CONFIG = loadWorkbenchConfig();
const AUTOPILOT = join(REPO_SKILL_DIR, "scripts", "autopilot.py");

for (const key of Object.keys(process.env)) {
  if (key.startsWith("WORKBENCH_")) delete process.env[key];
}

function withDataDir(fn) {
  const dataDir = mkdtempSync(join(tmpdir(), "wb-settings-"));
  try {
    return fn(dataDir);
  } finally {
    rmSync(dataDir, { recursive: true, force: true });
  }
}

test("没有本地设置时使用仓库默认值并标记来源", () => withDataDir((dataDir) => {
  const settings = effectiveSettings({ dataDir, env: {} });
  assert.equal(settings.user_settings_present, false);
  assert.equal(settings.sources.python, "default");
  assert.equal(settings.sources.autopilot, "default");
  assert.equal(settings.exists.python, true);
  assert.equal(settings.exists.autopilot, true);
}));

test("--root 覆盖优先于本地设置并免除书库错误", () => withDataDir((dataDir) => {
  const root = join(dataDir, "books");
  mkdirSync(root);
  const settings = effectiveSettings({ dataDir, env: {}, rootsOverride: [root] });
  assert.deepEqual(settings.books_roots, [root]);
  assert.equal(settings.sources.books_roots, "cli");
  assert.equal(settings.errors.some((error) => error.includes("书库根目录")), false);
  assert.equal(settings.needs_setup, false);
}));

test("本地设置里的无效路径会阻止首次进入", () => withDataDir((dataDir) => {
  writeFileSync(join(dataDir, "settings.json"), JSON.stringify({
    schema_version: 1,
    books_root: join(dataDir, "missing-books"),
    python: CONFIG.PYTHON,
    autopilot: AUTOPILOT,
  }), "utf8");
  const settings = effectiveSettings({ dataDir, env: {} });
  assert.equal(settings.user_settings_present, true);
  assert.equal(settings.needs_setup, true);
  assert.ok(settings.errors.some((error) => error.includes("书库根目录不存在")));
}));

test("保存设置会创建书库目录并切换来源", () => withDataDir((dataDir) => {
  const root = join(dataDir, "books");
  const settings = saveSettings({
    dataDir,
    patch: { books_root: root, python: CONFIG.PYTHON, autopilot: AUTOPILOT, create_books_root: true },
  });
  assert.equal(existsSync(root), true);
  assert.equal(settings.sources.books_roots, "user");
  assert.equal(settings.needs_setup, false);
  assert.equal(settings.books_root, root);
  const stored = JSON.parse(readFileSync(join(dataDir, "settings.json"), "utf8"));
  assert.equal(stored.books_root, root);
  assert.equal(stored.schema_version, 1);
}));

test("保存无效 Python 会抛错且不写文件", () => withDataDir((dataDir) => {
  const root = join(dataDir, "books");
  mkdirSync(root);
  assert.throws(
    () => saveSettings({
      dataDir,
      patch: { books_root: root, python: join(dataDir, "nope.exe"), autopilot: AUTOPILOT },
    }),
    (error) => error instanceof SettingsError && error.errors.some((item) => item.includes("Python 不可执行")),
  );
  assert.equal(existsSync(join(dataDir, "settings.json")), false);
}));

test("books_root 覆盖会清掉旧的 books_roots", () => withDataDir((dataDir) => {
  const first = join(dataDir, "books-a");
  const second = join(dataDir, "books-b");
  mkdirSync(first);
  mkdirSync(second);
  saveSettings({
    dataDir,
    patch: { books_roots: [first], python: CONFIG.PYTHON, autopilot: AUTOPILOT },
  });
  const settings = saveSettings({
    dataDir,
    patch: { books_root: second, python: CONFIG.PYTHON, autopilot: AUTOPILOT },
  });
  assert.deepEqual(settings.books_roots, [second]);
  const stored = JSON.parse(readFileSync(join(dataDir, "settings.json"), "utf8"));
  assert.deepEqual(stored.books_roots, [second]);
  assert.equal(stored.books_root, second);
}));
