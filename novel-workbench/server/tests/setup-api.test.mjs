import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";
import { startWorkbench } from "../index.mjs";
import { loadWorkbenchConfig } from "../paths.mjs";
import { FAKEBIN } from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const CONFIG = loadWorkbenchConfig();
const AUTOPILOT = join(SKILL_DIR, "scripts", "autopilot.py");

let workspace;
let tempRoot;
let dataDir;
let booksRoot;
let base;

async function request(path, options = {}) {
  const response = await fetch(`${base}${path}`, options);
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { status: response.status, body };
}

before(async () => {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("WORKBENCH_")) delete process.env[key];
  }
  tempRoot = mkdtempSync(join(tmpdir(), "wb-setup-"));
  dataDir = join(tempRoot, "data");
  booksRoot = join(tempRoot, "books");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "settings.json"), JSON.stringify({
    schema_version: 1,
    books_root: join(tempRoot, "missing-books"),
    python: join(tempRoot, "missing-python.exe"),
    autopilot: AUTOPILOT,
  }), "utf8");
  workspace = await startWorkbench({ port: 0, dataDir });
  base = workspace.url;
});

after(async () => {
  await workspace?.close();
  rmSync(tempRoot, { recursive: true, force: true });
});

test("首次运行被识别为需要设置", async () => {
  const health = await request("/api/health");
  assert.equal(health.status, 200);
  assert.equal(health.body.needs_setup, true);
  assert.ok(health.body.version);

  const settings = await request("/api/settings");
  assert.equal(settings.status, 200);
  assert.equal(settings.body.settings.needs_setup, true);
  assert.ok(settings.body.settings.errors.length >= 2);
});

test("保存有效路径后退出首次运行并同步书库", async () => {
  const saved = await request("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      books_root: booksRoot,
      python: CONFIG.PYTHON,
      autopilot: AUTOPILOT,
      story_long_write: FAKEBIN,
      create_books_root: true,
    }),
  });
  assert.equal(saved.status, 200);
  assert.equal(saved.body.settings.needs_setup, false);
  assert.deepEqual(saved.body.settings.books_roots, [booksRoot]);
  assert.equal(saved.body.settings.sources.books_roots, "user");

  const books = await request("/api/books");
  assert.equal(books.status, 200);
  assert.deepEqual(books.body.roots, [booksRoot]);
  assert.equal(books.body.books.length, 0);

  const health = await request("/api/health");
  assert.equal(health.body.needs_setup, false);
});

test("无效路径被拒绝且保留原设置", async () => {
  const rejected = await request("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ books_root: join(tempRoot, "still-missing"), create_books_root: false }),
  });
  assert.equal(rejected.status, 400);
  assert.equal(rejected.body.error.code, "invalid_settings");

  const settings = await request("/api/settings");
  assert.deepEqual(settings.body.settings.books_roots, [booksRoot]);
});
