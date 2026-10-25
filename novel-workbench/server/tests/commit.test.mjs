import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";
import { startWorkbench } from "../index.mjs";
import { applyFakeEnv } from "./helpers.mjs";

// 单章定稿端点：HTTP 契约层。核心算法在 python_contracts 的 8 条用例里，
// 这里只验证「接线 + 错误码 → 状态码映射 + 幂等对外表现」。
const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const FIXTURES = resolve(SKILL_DIR, "..", "_fixtures");

const TXN = {
  schema_version: 1,
  mode: "append",
  chapter: 3,
  chapter_title: "测试章",
  expected_state_revision: 2,
  delta: {
    result: "测试结果摘要",
    character_changes: [],
    foreshadow_changes: [],
    timeline_events: [],
    constraints: [],
    next_chapter_commitments: [],
    retired_context_items: [],
    retired_characters: [],
  },
  context: {
    position: { volume: "第一卷", volume_start_chapter: 1, story_time: "1990 年", scene: "厂办" },
    long_term_constraints: [],
    active_character_names: [],
    continuity_risks: [],
  },
  character_snapshots: {},
};

let workspace;
let tempRoot;
let happyDir;
let bareDir;
let happyId;
let bareId;

async function request(path, options = {}) {
  const response = await fetch(`${workspace.url}${path}`, options);
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { status: response.status, body };
}

function post(path, payload = {}) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function prepareBook(dir, { withTxn }) {
  cpSync(resolve(FIXTURES, "chapter_e2e"), dir, { recursive: true });
  writeFileSync(join(dir, "正文", "第003章_测试.md"), "他把名单又看了一遍。".repeat(200), "utf8");
  if (withTxn) {
    const work = join(dir, ".story", "work", "第003章");
    mkdirSync(work, { recursive: true });
    writeFileSync(join(work, "tracking.json"), JSON.stringify(TXN), "utf8");
  }
}

before(async () => {
  applyFakeEnv();
  tempRoot = mkdtempSync(join(tmpdir(), "wb-commit-"));
  happyDir = join(tempRoot, "有事务");
  bareDir = join(tempRoot, "无事务");
  prepareBook(happyDir, { withTxn: true });
  prepareBook(bareDir, { withTxn: false });
  workspace = await startWorkbench({ port: 0, roots: [tempRoot], dataDir: join(tempRoot, "data") });
  const { body } = await request("/api/books");
  const find = (dir) => body.books.find((item) => item.path === dir)?.id;
  happyId = find(happyDir);
  bareId = find(bareDir);
  assert.ok(happyId && bareId, `书籍未入库：${JSON.stringify(body.books.map((b) => b.path))}`);
});

after(async () => {
  await workspace?.close();
  rmSync(tempRoot, { recursive: true, force: true });
});

test("缺追踪事务：409 tracking_missing，且不生成空事务", async () => {
  const res = await post(`/api/books/${bareId}/chapters/3/commit`);
  assert.equal(res.status, 409);
  assert.equal(res.body.error.code, "tracking_missing");
  const txn = join(bareDir, ".story", "work", "第003章", "tracking.json");
  assert.throws(() => readFileSync(txn), "绝不能自动建空事务");
});

test("跳章定稿：409 chapter_gap", async () => {
  const res = await post(`/api/books/${bareId}/chapters/5/commit`);
  assert.equal(res.status, 409);
  assert.equal(res.body.error.code, "chapter_gap");
});

test("流水线卡在节点上：409 chapter_busy", async () => {
  writeFileSync(join(bareDir, "_pipeline", ".step_token"),
    JSON.stringify({ token: "abc", node: "C3.N12", issued_at: "2026-10-26T00:00:00+0800" }), "utf8");
  try {
    const res = await post(`/api/books/${bareId}/chapters/3/commit`);
    assert.equal(res.status, 409);
    assert.equal(res.body.error.code, "chapter_busy");
  } finally {
    rmSync(join(bareDir, "_pipeline", ".step_token"), { force: true });
  }
});

test("append 定稿成功：账本前进、返回新章节数", async () => {
  const res = await post(`/api/books/${happyId}/chapters/3/commit`);
  assert.equal(res.status, 200, JSON.stringify(res.body));
  assert.equal(res.body.committed, true);
  assert.equal(res.body.already, false);
  assert.equal(res.body.mode, "append");
  assert.equal(res.body.book.chapters_committed, 3);
  assert.equal(res.body.book.next_chapter, 4);
  const ledger = JSON.parse(readFileSync(join(happyDir, "追踪", "_tracking-state.json"), "utf8"));
  assert.equal(ledger.last_committed_chapter, 3);
});

test("同内容重复定稿：200 already，revision 不变", async () => {
  const before = JSON.parse(readFileSync(join(happyDir, "追踪", "_tracking-state.json"), "utf8"));
  const res = await post(`/api/books/${happyId}/chapters/3/commit`);
  assert.equal(res.status, 200, JSON.stringify(res.body));
  assert.equal(res.body.already, true);
  const after = JSON.parse(readFileSync(join(happyDir, "追踪", "_tracking-state.json"), "utf8"));
  assert.equal(after.state_revision, before.state_revision);
});

test("闸门阻断：422 gate_blocked，账本不动", async () => {
  process.env.FAKE_STORY_STATUS = "blocked";
  try {
    const before = readFileSync(join(bareDir, "追踪", "_tracking-state.json"), "utf8");
    const work = join(bareDir, ".story", "work", "第003章");
    mkdirSync(work, { recursive: true });
    writeFileSync(join(work, "tracking.json"), JSON.stringify(TXN), "utf8");
    const res = await post(`/api/books/${bareId}/chapters/3/commit`);
    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, "gate_blocked");
    assert.equal(readFileSync(join(bareDir, "追踪", "_tracking-state.json"), "utf8"), before);
  } finally {
    delete process.env.FAKE_STORY_STATUS;
  }
});
