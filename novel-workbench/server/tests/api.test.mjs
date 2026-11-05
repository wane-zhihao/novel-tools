import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";
import { startWorkbench } from "../index.mjs";
import { applyFakeEnv } from "./helpers.mjs";

// reveal 端点测试：覆盖打开命令，避免弹出真实资源管理器
process.env.WORKBENCH_REVEAL_CMD = process.execPath;
process.env.WORKBENCH_REVEAL_ARGS = JSON.stringify(["-e", "process.exit(0)"]);

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const FIXTURES = resolve(SKILL_DIR, "..", "_fixtures");

let workspace;
let tempRoot;
let bookDir;
let base;
let bookId;

async function request(path, options = {}) {
  const response = await fetch(`${base}${path}`, options);
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { status: response.status, body, headers: response.headers };
}

function post(path, payload, extraHeaders = {}) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(payload),
  });
}

async function waitRun(id, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { body } = await request(`/api/books/${id}/run?since=0`);
    const run = body?.run;
    if (run && !["running", "stopping"].includes(run.status)) return run;
    await new Promise((accept) => setTimeout(accept, 150));
  }
  throw new Error("等待运行结束超时");
}

before(async () => {
  applyFakeEnv();
  tempRoot = mkdtempSync(join(tmpdir(), "wb-api-"));
  bookDir = join(tempRoot, "联试书");
  cpSync(resolve(FIXTURES, "chapter_e2e"), bookDir, { recursive: true });
  workspace = await startWorkbench({
    port: 0,
    roots: [tempRoot],
    dataDir: join(tempRoot, "data"),
  });
  base = workspace.url;
});

after(async () => {
  await workspace?.close();
  rmSync(tempRoot, { recursive: true, force: true });
});

test("健康检查与书库列表", async () => {
  const health = await request("/api/health");
  assert.equal(health.status, 200);
  assert.equal(health.body.ok, true);
  assert.equal(health.body.books, 1);

  const books = await request("/api/books");
  assert.equal(books.status, 200);
  assert.equal(books.body.books.length, 1);
  bookId = books.body.books[0].id;
  assert.equal(books.body.books[0].title, "《联试之书》");
  assert.equal(books.body.books[0].chapters_committed, 2);
});

test("书详情包含章节、节点与 findings", async () => {
  const detail = await request(`/api/books/${bookId}`);
  assert.equal(detail.status, 200);
  assert.equal(detail.body.chapters.length, 2);
  assert.equal(detail.body.chapters[0].title, "榜上换了名");
  assert.ok(detail.body.nodes.length >= 8);
  assert.equal(detail.body.findings.filter((item) => item.active === 1 && item.blocking === 1).length, 1);
  assert.equal(detail.body.book.cursor, "C3.N09");
});

test("闸门报告走 gate_runner 实时", async () => {
  const report = await request(`/api/books/${bookId}/report?chapter=2`);
  assert.equal(report.status, 200);
  assert.equal(report.body.source, "gate_runner");
  assert.equal(report.body.gates.status, "pass");
  assert.ok(report.body.findings.items.length >= 1);

  const invalid = await request(`/api/books/${bookId}/report?chapter=abc`);
  assert.equal(invalid.status, 400);
});

test("mock 运行一步、事件流与历史入库", async () => {
  const started = await post(`/api/books/${bookId}/run`, { mode: "mock", max_steps: 1 });
  assert.equal(started.status, 202);
  assert.equal(started.body.run.status, "running");

  const run = await waitRun(bookId);
  assert.equal(run.status, "max_steps");
  assert.ok(run.events.some((event) => event.type === "node_started" && event.node === "C3.N09"));
  assert.ok(run.events.some((event) => event.type === "node_done" && event.node === "C3.N09"));
  assert.ok(!JSON.stringify(run.result).toLowerCase().includes("token"));

  const detail = await request(`/api/books/${bookId}`);
  assert.equal(detail.body.book.cursor, "C3.N10");
  assert.ok(!existsSync(join(bookDir, "_pipeline", ".step_token")));

  const history = await request(`/api/books/${bookId}/runs`);
  assert.equal(history.status, 200);
  assert.ok(history.body.runs.some((row) => row.action === "agent_run" && row.kind === "mock"));
});

test("停止接口在没有运行时返回 409", async () => {
  const stopped = await post(`/api/books/${bookId}/stop`, {});
  assert.equal(stopped.status, 409);
});

test("投稿记录以文件为真相并可更新", async () => {
  const created = await post(`/api/books/${bookId}/submissions`, {
    channel: "七猫",
    editor: "李编辑",
    feedback: "开篇节奏偏慢，第三章后再看。",
  });
  assert.equal(created.status, 201);
  const id = created.body.submission.id;
  assert.ok(existsSync(join(bookDir, "_pipeline", "submissions.json")));

  const updated = await request(`/api/books/${bookId}/submissions/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: "编辑反馈", next_action: "按反馈改前三章" }),
  });
  assert.equal(updated.status, 200);
  assert.equal(updated.body.submission.status, "编辑反馈");

  const list = await request(`/api/books/${bookId}/submissions`);
  assert.equal(list.body.submissions.length, 1);
  assert.equal(list.body.submissions[0].next_action, "按反馈改前三章");

  const stored = JSON.parse(readFileSync(join(bookDir, "_pipeline", "submissions.json"), "utf8"));
  assert.equal(stored.items.length, 1);
  assert.equal(stored.items[0].id, id);
});

test("Agent 配置读取、按书覆盖与非法回滚", async () => {
  const global = await request("/api/agents");
  assert.equal(global.status, 200);
  assert.deepEqual(global.body.errors, []);
  const config = structuredClone(global.body.config);

  const good = structuredClone(config);
  good.roles.writer.temperature = 0.6;
  const saved = await request("/api/agents", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "book", book_id: bookId, config: good }),
  });
  assert.equal(saved.status, 200);
  assert.equal(saved.body.config.roles.writer.temperature, 0.6);
  assert.ok(existsSync(join(bookDir, "_pipeline", "agents.json")));

  const bad = structuredClone(good);
  bad.roles.writer.provider = "nope";
  const rejected = await request("/api/agents", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "book", book_id: bookId, config: bad }),
  });
  assert.equal(rejected.status, 400);
  assert.ok(rejected.body.error.details.errors.some((error) => error.includes("不存在的 provider")));

  const after = await request(`/api/agents?book=${bookId}`);
  assert.equal(after.body.config.roles.writer.temperature, 0.6);

  const secret = structuredClone(good);
  secret.api_key = "sk-should-not-be-saved";
  const secretRejected = await request("/api/agents", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "book", book_id: bookId, config: secret }),
  });
  assert.equal(secretRejected.status, 400);
  assert.equal(secretRejected.body.error.code, "secret_not_allowed");
});

test("provider 密钥可写入且不回显", async () => {
  const secret = "sk-api-test-do-not-leak";
  const saved = await request("/api/providers/deepseek/secret", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: secret }),
  });
  assert.equal(saved.status, 200);
  assert.equal(saved.body.configured, true);
  assert.ok(!JSON.stringify(saved.body).includes(secret));

  const agents = await request("/api/agents");
  assert.equal(agents.status, 200);
  const deepseek = agents.body.providers.find((provider) => provider.name === "deepseek");
  assert.equal(deepseek.configured, true);
  assert.equal(deepseek.source, "store");
  assert.ok(!JSON.stringify(agents.body).includes(secret));

  const removed = await request("/api/providers/deepseek/secret", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  assert.equal(removed.status, 200);
  assert.equal(removed.body.configured, false);
});

test("跨站 Origin 的写请求被拒绝", async () => {
  const evil = await post(`/api/books/${bookId}/run`, { mode: "mock" }, { Origin: "https://evil.example" });
  assert.equal(evil.status, 403);
  assert.equal(evil.body.error.code, "invalid_origin");
});

test("正文保存、版本、diff 与回滚端点", async () => {
  const current = await request(`/api/books/${bookId}/chapters/2/content`);
  assert.equal(current.status, 200);
  assert.ok(current.body.content.length > 0);
  assert.ok(current.body.sha256);

  const saved = await request(`/api/books/${bookId}/chapters/2/content`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      base_sha: current.body.sha256,
      content: `${current.body.content}\n\n（端点测试补一句。）`,
      allow_committed_edit: true,
    }),
  });
  assert.equal(saved.status, 200);
  assert.equal(saved.body.dirty, true);
  assert.ok(saved.body.version?.id);

  const versions = await request(`/api/books/${bookId}/versions?chapter=2`);
  assert.equal(versions.status, 200);
  assert.ok(versions.body.versions.length >= 2);
  const [right, left] = versions.body.versions;
  const diff = await request(`/api/books/${bookId}/versions/${left.id}/diff/${right.id}`);
  assert.equal(diff.status, 200);
  assert.ok(diff.body.stats.changed >= 1);

  const conflict = await request(`/api/books/${bookId}/chapters/2/content`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_sha: "deadbeef", content: "x" }),
  });
  assert.equal(conflict.status, 409);
  assert.equal(conflict.body.error.code, "content_conflict");

  const restored = await request(`/api/books/${bookId}/versions/${left.id}/restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  assert.equal(restored.status, 200);
  assert.equal(restored.body.content, current.body.content);
});

test("故事库与投前检查端点", async () => {
  const story = await request(`/api/books/${bookId}/story`);
  assert.equal(story.status, 200);
  assert.ok(story.body.groups.length >= 3);

  const doc = await request(`/api/books/${bookId}/story/doc?path=${encodeURIComponent("设定/角色.md")}`);
  assert.equal(doc.status, 200);
  const saved = await request(`/api/books/${bookId}/story/doc`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path: "设定/角色.md",
      content: `${doc.body.content}\n\n## 端点测试\n- 身份：测试`,
      base_sha: doc.body.sha256,
    }),
  });
  assert.equal(saved.status, 200);

  const preflight = await request(`/api/books/${bookId}/submissions/preflight`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  assert.equal(preflight.status, 200);
  assert.equal(preflight.body.ready, false);
  assert.ok(preflight.body.checks.some((check) => check.id === "words" && !check.ok));
  // 0.9：每个检查项带 fix 修复指引（缺字数的那条必须有）
  const wordsCheck = preflight.body.checks.find((check) => check.id === "words");
  assert.ok(wordsCheck.fix, "未通过的检查项应带 fix");
});

test("投稿包端点：确认 gate 与 preflight 拦截（0.10/0.9）", async () => {
  // 不带 confirmations → 400 confirmations_required（先于 preflight 检查）
  const noConfirm = await post(`/api/books/${bookId}/submissions/package`, { channel: "七猫" });
  assert.equal(noConfirm.status, 400);
  assert.equal(noConfirm.body.error.code, "confirmations_required");

  // 确认没勾全 → 400 confirmations_incomplete
  const partial = await post(`/api/books/${bookId}/submissions/package`,
    { channel: "七猫", confirmations: [true, true, false, true] });
  assert.equal(partial.status, 400);
  assert.equal(partial.body.error.code, "confirmations_incomplete");

  // 全勾但 preflight 未过（fixture 字数不足且有阻断项）→ 400 preflight_failed
  const blocked = await post(`/api/books/${bookId}/submissions/package`,
    { channel: "七猫", confirmations: [true, true, true, true] });
  assert.equal(blocked.status, 400);
  assert.equal(blocked.body.error.code, "preflight_failed");
  assert.ok(Array.isArray(blocked.body.error.details.checks));
});

test("投稿端点同步失败时不得基于旧数据库继续处理", async () => {
  const statePath = join(bookDir, "_pipeline", "state.json");
  const originalState = readFileSync(statePath, "utf8");
  writeFileSync(statePath, "{ malformed json", "utf8");
  try {
    const preflight = await post(`/api/books/${bookId}/submissions/preflight`, {});
    assert.equal(preflight.status, 400);
    assert.equal(preflight.body.error.code, "submission_sync_failed");

    const packaged = await post(`/api/books/${bookId}/submissions/package`, {
      channel: "七猫", confirmations: [true, true, true, true],
    });
    assert.equal(packaged.status, 400);
    assert.equal(packaged.body.error.code, "submission_sync_failed");
  } finally {
    writeFileSync(statePath, originalState, "utf8");
  }
});

test("投稿包缺必需文件时返回明确客户端错误", async () => {
  const reportsPath = join(bookDir, "投稿包", "体检报告.md");
  const statePath = join(bookDir, "_pipeline", "state.json");
  const findingsPath = join(bookDir, "_pipeline", "findings_C2.json");
  const trackingPath = join(bookDir, "追踪", "_tracking-state.json");
  const fullTextPath = join(bookDir, "投稿包", "全文.md");
  const originals = [statePath, findingsPath, trackingPath].map((path) => readFileSync(path, "utf8"));
  const originalFullText = existsSync(fullTextPath) ? readFileSync(fullTextPath, "utf8") : null;
  const tracking = JSON.parse(originals[2]);
  const chapterNames = readdirSync(join(bookDir, "正文")).filter((name) => /^第\d{3}章_.*\.md$/.test(name)).sort();
  const chapterBodies = chapterNames.map((name) => readFileSync(join(bookDir, "正文", name), "utf8"));
  chapterNames.forEach((name, index) => {
    const number = String(Number(name.slice(1, 4)));
    const body = chapterBodies[index];
    tracking.wordcount_records[number].actual = body.replace(/\s+/g, "").length;
    tracking.wordcount_records[number].body_sha256 = createHash("sha256").update(body, "utf8").digest("hex");
  });
  writeFileSync(trackingPath, JSON.stringify(tracking), "utf8");
  writeFileSync(fullTextPath, `${chapterBodies.map((body) => body.trim()).join("\n\n")}\n`, "utf8");
  const submissionsDir = join(bookDir, "_pipeline", "submissions");
  const previousPackages = existsSync(submissionsDir) ? new Set(readdirSync(submissionsDir)) : new Set();
  writeFileSync(reportsPath, "# 体检\n", "utf8");
  const state = JSON.parse(originals[0]);
  state.target_words = 1;
  state.chapters_committed = 2;
  writeFileSync(statePath, JSON.stringify(state), "utf8");
  const findings = JSON.parse(originals[1]);
  findings.items = [];
  writeFileSync(findingsPath, JSON.stringify(findings), "utf8");
  rmSync(reportsPath, { force: true });
  try {
    const response = await post(`/api/books/${bookId}/submissions/package`, {
      channel: "七猫", confirmations: [true, true, true, true],
    });
    assert.equal(response.status, 400);
    assert.equal(response.body.error.code, "manifest_incomplete");
    assert.match(response.body.error.message, /体检报告\.md/);
    assert.ok(response.body.error.details.missing.includes("投稿包/体检报告.md"));
    const afterPackages = existsSync(submissionsDir) ? readdirSync(submissionsDir) : [];
    assert.deepEqual(afterPackages.sort(), [...previousPackages].sort(), "失败打包不能留下投稿包目录");
  } finally {
    writeFileSync(statePath, originals[0], "utf8");
    writeFileSync(findingsPath, originals[1], "utf8");
    writeFileSync(trackingPath, originals[2], "utf8");
    if (originalFullText == null) rmSync(fullTextPath, { force: true });
    else writeFileSync(fullTextPath, originalFullText, "utf8");
    writeFileSync(reportsPath, "# 体检\n", "utf8");
  }
});

test("投稿包 reveal 端点：路径校验与本机打开", async () => {
  const pkgName = "2026-10-25_七猫";
  const pkgDir = join(bookDir, "_pipeline", "submissions", pkgName);
  mkdirSync(pkgDir, { recursive: true });
  writeFileSync(join(pkgDir, "全文.md"), "# 全文\n", "utf8");
  writeFileSync(join(pkgDir, "MANIFEST.json"), "{}\n", "utf8");

  const revealed = await post(`/api/books/${bookId}/submissions/${encodeURIComponent(pkgName)}/reveal`, { file: "全文.md" });
  assert.equal(revealed.status, 200);
  assert.equal(revealed.body.opened, true);

  const openedDir = await post(`/api/books/${bookId}/submissions/${encodeURIComponent(pkgName)}/reveal`, {});
  assert.equal(openedDir.status, 200);

  const missing = await post(`/api/books/${bookId}/submissions/${encodeURIComponent("不存在的投稿包")}/reveal`, {});
  assert.equal(missing.status, 404);
  assert.equal(missing.body.error.code, "package_not_found");

  const traversal = await post(`/api/books/${bookId}/submissions/..%2F..%2Fsettings/reveal`, {});
  assert.equal(traversal.status, 400);
  assert.equal(traversal.body.error.code, "invalid_package");

  const badFile = await post(`/api/books/${bookId}/submissions/${encodeURIComponent(pkgName)}/reveal`, { file: "../state.json" });
  assert.equal(badFile.status, 400);
  assert.equal(badFile.body.error.code, "invalid_file");
});

test("未知书与未知接口返回 404", async () => {
  const missingBook = await request("/api/books/9999");
  assert.equal(missingBook.status, 404);
  const missingApi = await request("/api/nope");
  assert.equal(missingApi.status, 404);
});

test("until_chapter 参数校验：非法值拒绝，合法值放行到启动（0.9/1.3）", async () => {
  const bad = await post(`/api/books/${bookId}/run`, { mode: "mock", until_chapter: "abc" });
  assert.equal(bad.status, 400);
  assert.equal(bad.body.error.code, "invalid_until_chapter");

  const zero = await post(`/api/books/${bookId}/run`, { mode: "mock", until_chapter: 0 });
  assert.equal(zero.status, 400);
  assert.equal(zero.body.error.code, "invalid_until_chapter");

  // 合法值不该被参数校验拦下（是否 202 取决于当时是否有运行/令牌，两种都算通过校验）
  const ok = await post(`/api/books/${bookId}/run`, { mode: "mock", until_chapter: 2, max_steps: 1 });
  assert.ok([202, 409].includes(ok.status), JSON.stringify(ok.body));
  if (ok.status === 409) {
    assert.ok(["run_running", "needs_resume"].includes(ok.body.error.code), JSON.stringify(ok.body));
    return;
  }
  // 起了后台 run 就必须等它到终态，否则 teardown 关库会撞「database is not open」
  await post(`/api/books/${bookId}/stop`);
  const deadline = Date.now() + 20_000;
  for (;;) {
    const { run } = await request(`/api/books/${bookId}/run?since=0`).then((r) => r.body);
    if (!run || !["running", "stopping"].includes(run.status)) break;
    if (Date.now() > deadline) break;
    await new Promise((r) => setTimeout(r, 150));
  }
});

test("manuscript 暴露 dirty_count / last_committed_chapter / blocked_reason（1.4）", async () => {
  const { body } = await request(`/api/books/${bookId}/manuscript`);
  const chapters = body.chapters ?? [];
  // 三个字段必须存在（值可以是 null/0），否则 UI 只能自己数、自己猜
  assert.ok("dirty_count" in body, "缺 dirty_count");
  assert.ok("last_committed_chapter" in body, "缺 last_committed_chapter");
  assert.ok("blocked_reason" in body, "缺 blocked_reason");

  const expectedDirty = chapters.filter((c) => c.dirty).length;
  assert.equal(body.dirty_count, expectedDirty, "dirty_count 应与章节列表自洽");

  const committed = chapters.filter((c) => c.committed).map((c) => c.number);
  const expectedLast = committed.length ? Math.max(...committed) : null;
  assert.equal(body.last_committed_chapter, expectedLast, "last_committed_chapter 应是最新已定稿章");
});

test("manuscript 暴露 drift：state 与章节表不一致时必须说出来（1.5）", async () => {
  const { body } = await request(`/api/books/${bookId}/manuscript`);
  assert.ok(body.drift, "缺 drift 字段");
  assert.equal(typeof body.drift.ok, "boolean");
  assert.ok(Array.isArray(body.drift.items));

  const chapters = body.chapters ?? [];
  const sumWords = chapters.reduce((sum, c) => sum + (Number(c.words) || 0), 0);
  const committedCount = chapters.filter((c) => c.committed).length;

  // fixture 的 state 与章节表本就一致 → 不该报假警
  if (body.drift.ok) {
    assert.equal(body.drift.items.length, 0);
  } else {
    // 报了就必须给两个数字，让人能判断哪个是对的
    for (const item of body.drift.items) {
      assert.ok(item.id && item.detail, JSON.stringify(item));
      assert.ok("state" in item && "actual" in item, `drift 项应同时给 state 与 actual：${JSON.stringify(item)}`);
    }
  }
  // 交叉校验：drift.ok 应当真的反映这两组数字是否相等
  const consistent = Number(body.words_total_state ?? NaN) === sumWords
    && Number(body.chapters_committed_state ?? NaN) === committedCount;
  if (consistent) assert.equal(body.drift.ok, true, "数字一致却报 drift");
});

test("drift 仍以 state 缓存披露：sync 修正 DB 字数后 API 继续显示差异（Prove-It）", async () => {
  const statePath = join(bookDir, "_pipeline", "state.json");
  const originalState = readFileSync(statePath, "utf8");
  try {
    // 造出真实漂移：state 说 9288 字 4 章，正文账本实际是 4760 字 2 章。
    const state = JSON.parse(originalState);
    state.words_total = 9288;
    state.chapters_committed = 4;
    writeFileSync(statePath, JSON.stringify(state), "utf8");

    const { body } = await request(`/api/books/${bookId}/manuscript`);
    assert.equal(body.drift.ok, false, "刷新 DB 后 state 漂移仍必须可见");
    assert.equal(body.words_total_state, 9288, "字段必须展示 state 文件原值，而非已修正的 DB 镜像");
    const words = body.drift.items.find((i) => i.id === "words_total");
    assert.ok(words, JSON.stringify(body.drift.items));
    assert.equal(words.state, 9288);
    assert.equal(words.actual, 4760);
    assert.match(words.detail, /9288/);
    const chapters = body.drift.items.find((i) => i.id === "chapters_committed");
    assert.ok(chapters, JSON.stringify(body.drift.items));
    assert.equal(chapters.state, 4);
    assert.equal(chapters.actual, 2);
  } finally {
    writeFileSync(statePath, originalState, "utf8");
  }
});

test("manuscript 在 state 不可读时必须披露无法核对，而不是报「账本一致」", async () => {
  const statePath = join(bookDir, "_pipeline", "state.json");
  const originalState = readFileSync(statePath, "utf8");
  try {
    writeFileSync(statePath, "{ broken json", "utf8");
    const { body } = await request(`/api/books/${bookId}/manuscript`);
    assert.equal(body.drift.ok, false, "state 读不出来时不得宣称无漂移");
    const unreadable = body.drift.items.find((item) => item.id === "state_unreadable");
    assert.ok(unreadable, `drift 里必须有「state 不可读」项：${JSON.stringify(body.drift.items)}`);
    assert.match(unreadable.detail, /state/);
    assert.equal(body.words_total_state, null, "state 不可读时不得把 0 冒充 state 原值");
    assert.equal(body.chapters_committed_state, null);
  } finally {
    writeFileSync(statePath, originalState, "utf8");
  }
});

test("manuscript 对缺失/非数值的 state 账本字段报无法核对，而不是拿 0 冒充原值", async () => {
  const statePath = join(bookDir, "_pipeline", "state.json");
  const originalState = readFileSync(statePath, "utf8");
  try {
    const state = JSON.parse(originalState);
    delete state.words_total;
    state.chapters_committed = "abc";
    writeFileSync(statePath, JSON.stringify(state), "utf8");
    const { body } = await request(`/api/books/${bookId}/manuscript`);
    assert.equal(body.drift.ok, false);
    const words = body.drift.items.find((item) => item.id === "words_total_missing");
    assert.ok(words, `应报 words_total 缺账项：${JSON.stringify(body.drift.items)}`);
    assert.equal(words.state, null);
    const committed = body.drift.items.find((item) => item.id === "chapters_committed_missing");
    assert.ok(committed, `chapters_committed 非数值也应报缺账项：${JSON.stringify(body.drift.items)}`);
    assert.equal(body.words_total_state, null);
    assert.equal(body.chapters_committed_state, null);
  } finally {
    writeFileSync(statePath, originalState, "utf8");
  }
});

test("开书字段落盘：platform / vol_size / one_line 必须进 state.json（Phase 2）", async () => {
  const folder = "开书字段书";
  const created = await post("/api/books", {
    title: "开书字段书",
    genre: "shuangwen",
    platform: "zongheng",
    vol_size: 20,
    target_words: 20000,
    one_line: "重回九零，从厂办开始",
    folder,
  });
  assert.equal(created.status, 201, JSON.stringify(created.body));

  const state = JSON.parse(readFileSync(join(tempRoot, folder, "_pipeline", "state.json"), "utf8"));
  assert.equal(state.platform, "zongheng", "platform 没落盘");
  assert.equal(state.plan?.vol_size, 20, "vol_size 没进 plan");
  assert.equal(state.one_line, "重回九零，从厂办开始", "one_line 没落盘");

  // 非法值必须拒绝，而不是静默回落默认值
  const badPlatform = await post("/api/books", { title: "坏平台", genre: "shuangwen", platform: "起点", folder: "坏平台" });
  assert.equal(badPlatform.status, 400);
  assert.equal(badPlatform.body.error.code, "invalid_platform");

  const badVol = await post("/api/books", { title: "坏卷", genre: "shuangwen", vol_size: 999, folder: "坏卷" });
  assert.equal(badVol.status, 400);
  assert.equal(badVol.body.error.code, "invalid_vol_size");
});

test("开书平台名单以流水线配置为唯一事实源（A 修法）", async () => {
  // 仓库 config.json 的 PLATFORMS 只有 qimao/zongheng——表单和校验都不该出现第三个
  const hardCoded = await post("/api/books", { title: "番茄越界书", genre: "shuangwen", platform: "fanqie", folder: "番茄越界书" });
  assert.equal(hardCoded.status, 400);
  assert.equal(hardCoded.body.error.code, "invalid_platform");
  // 错误消息该列出真实可用项，而不是写死一份可能漂移的名单
  assert.match(hardCoded.body.error.message, /qimao|zongheng/);

  const health = await request("/api/health");
  const platforms = health.body.platforms;
  assert.ok(Array.isArray(platforms) && platforms.length >= 1, "health 应暴露 platforms 列表");
  assert.ok(platforms.some((p) => p.key === "qimao"), "qimao 必须在列");
  assert.ok(!platforms.some((p) => p.key === "fanqie"), "fanqie 未接入流水线，不该出现在可选项");
  // 题材也得跟着平台走：前端拿这份 genres 重填下拉，才不会给纵横报「年代」
  const qimao = platforms.find((p) => p.key === "qimao");
  assert.equal(qimao.ready, true, "qimao 有 GENRE_BASE，应标为可用");
  assert.ok(qimao.genres.some((g) => g.key === "niandai"), "qimao 的题材名单应从 GENRES 派生");
  const zongheng = platforms.find((p) => p.key === "zongheng");
  assert.ok(zongheng.genres.some((g) => g.key === "dushi"), "纵横的题材名单应与七猫不同源");
  assert.ok(!zongheng.genres.some((g) => g.key === "niandai"), "纵横不该出现七猫专属题材");
});
