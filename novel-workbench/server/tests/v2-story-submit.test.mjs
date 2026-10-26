import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { openDatabase } from "../db.mjs";
import { readStoryDoc, saveStoryDoc, storyIndex, StoryConflictError } from "../story.mjs";
import { packageSubmission, preflightSubmission } from "../submissions.mjs";

function makeBook(root, words = 20000) {
  const book = join(root, "book");
  for (const dir of ["正文", "大纲", "设定", "追踪", "投稿包"]) {
    mkdirSync(join(book, dir), { recursive: true });
  }
  writeFileSync(join(book, "设定", "角色.md"), "## 陈砚\n- 身份：厂办会计\n", "utf8");
  writeFileSync(join(book, "大纲", "卷纲_第一卷.md"), "- 主线：顶职名额\n", "utf8");
  writeFileSync(join(book, "大纲", "目录_前12章.md"), "1. 换名\n", "utf8");
  writeFileSync(join(book, "投稿包", "全文.md"), "# 全文\n" + "正文".repeat(100), "utf8");
  writeFileSync(join(book, "投稿包", "体检报告.md"), "# 体检\n", "utf8");
  writeFileSync(join(book, "投稿包", "投稿邮件.md"), "# 邮件\n", "utf8");
  return book;
}

test("故事库索引与文件保存", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-story-"));
  const book = makeBook(root);
  try {
    const index = storyIndex(book);
    assert.equal(index.groups.find((group) => group.id === "characters").docs[0].exists, true);
    const doc = readStoryDoc(book, "设定/角色.md");
    const saved = saveStoryDoc({
      bookPath: book, relPath: "设定/角色.md",
      content: `${doc.content}\n## 陈磊\n- 身份：弟弟\n`,
      baseSha: doc.sha256,
    });
    assert.ok(saved.words > doc.words);
    assert.throws(() => saveStoryDoc({
      bookPath: book, relPath: "设定/角色.md", content: "x", baseSha: "bad",
    }), StoryConflictError);
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("故事库大纲组按卷动态（多卷书的第二卷也可见）", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-story-vols-"));
  const book = join(root, "book");
  for (const dir of ["正文", "大纲", "设定", "追踪"]) mkdirSync(join(book, dir), { recursive: true });
  writeFileSync(join(book, "大纲", "卷纲_第一卷.md"), "- 主线：顶职\n", "utf8");
  writeFileSync(join(book, "大纲", "卷纲_第二卷.md"), "- 主线：南下\n", "utf8");
  writeFileSync(join(book, "大纲", "目录_第1卷.md"), "1. 换名\n", "utf8");
  writeFileSync(join(book, "大纲", "目录_第2卷.md"), "1. 南下\n", "utf8");
  writeFileSync(join(book, "大纲", "细纲_第001章.md"), "- 换名\n", "utf8");
  try {
    const index = storyIndex(book);
    const outline = index.groups.find((group) => group.id === "outline");
    const paths = outline.docs.map((doc) => doc.path);
    // 两卷卷纲与两卷目录都要出现在大纲组里（写死单卷时只会有一卷）
    assert.ok(paths.includes("大纲/卷纲_第一卷.md"), JSON.stringify(paths));
    assert.ok(paths.includes("大纲/卷纲_第二卷.md"), JSON.stringify(paths));
    assert.ok(paths.includes("大纲/目录_第1卷.md"), JSON.stringify(paths));
    assert.ok(paths.includes("大纲/目录_第2卷.md"), JSON.stringify(paths));
    // 第一卷排在第二卷之前（按卷号自然序，不是字典序 第1/第10/第2）
    assert.ok(paths.indexOf("大纲/卷纲_第一卷.md") < paths.indexOf("大纲/卷纲_第二卷.md"), JSON.stringify(paths));
    // 细纲仍走 chapter_outlines，不混进大纲组
    assert.ok(!paths.some((p) => p.includes("细纲_")), JSON.stringify(paths));
    assert.equal(index.chapter_outlines.length, 1);
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("投稿预检与不可变投稿包", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-submit-"));
  const bookPath = makeBook(root);
  const db = openDatabase({ dataDir: join(root, "data") });
  try {
    db.prepare(`
      INSERT INTO books (path, synced_at, title, chapters_committed, words_total)
      VALUES (?, ?, ?, ?, ?)
    `).run(bookPath, new Date().toISOString(), "测试书", 10, 20000);
    db.prepare(`
      INSERT INTO chapters (book_id, number, title, file_path, words, committed, dirty)
      VALUES (1, 1, '第一章', '正文/第001章_测试.md', 2000, 1, 0)
    `).run();
    const book = db.prepare("SELECT * FROM books WHERE id = 1").get();
    const ready = preflightSubmission(db, book);
    assert.equal(ready.ready, true, JSON.stringify(ready.checks));

    const packaged = packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true, true] });
    assert.ok(existsSync(join(bookPath, packaged.path, "MANIFEST.json")));
    const manifest = JSON.parse(readFileSync(join(bookPath, packaged.path, "MANIFEST.json"), "utf8"));
    assert.equal(manifest.channel, "七猫");
    assert.ok(manifest.files.some((file) => file.name === "全文.md"));

    db.prepare("UPDATE chapters SET dirty = 1 WHERE book_id = 1").run();
    const blocked = preflightSubmission(db, db.prepare("SELECT * FROM books WHERE id = 1").get());
    assert.equal(blocked.ready, false);
    assert.ok(blocked.checks.some((check) => check.id === "dirty" && !check.ok));
  } finally {
    db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

// ── 0.9 preflight 参数化：去掉「永久失败」的写死值 ──────────────────────
function makeMultiVolBook(root) {
  // 新书流水线的真实产物命名：卷纲_第N卷.md / 目录_第N卷.md（没有 目录_前12章.md）
  const book = join(root, "book");
  for (const dir of ["正文", "大纲", "设定", "追踪", "投稿包"]) {
    mkdirSync(join(book, dir), { recursive: true });
  }
  writeFileSync(join(book, "大纲", "卷纲_第一卷.md"), "- 主线：顶职名额\n", "utf8");
  writeFileSync(join(book, "大纲", "卷纲_第二卷.md"), "- 主线：南下\n", "utf8");
  writeFileSync(join(book, "大纲", "目录_第1卷.md"), "1. 换名\n", "utf8");
  writeFileSync(join(book, "投稿包", "全文.md"), "# 全文\n" + "正文".repeat(100), "utf8");
  writeFileSync(join(book, "投稿包", "体检报告.md"), "# 体检\n", "utf8");
  writeFileSync(join(book, "投稿包", "投稿邮件.md"), "# 邮件\n", "utf8");
  return book;
}

test("0.9 多卷书按实际大纲文件判定（不再写死 卷纲_第一卷+目录_前12章）", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-submit-multi-"));
  const bookPath = makeMultiVolBook(root);
  const db = openDatabase({ dataDir: join(root, "data") });
  try {
    db.prepare(`
      INSERT INTO books (path, synced_at, title, chapters_committed, words_total, target_words)
      VALUES (?, ?, ?, ?, ?, 600000)
    `).run(bookPath, new Date().toISOString(), "多卷书", 60, 610000);
    const book = db.prepare("SELECT * FROM books WHERE id = 1").get();
    const ready = preflightSubmission(db, book);
    assert.equal(ready.ready, true, JSON.stringify(ready.checks));
    // 投稿包应把扫到的所有大纲一起打包（确认 gate 全勾）
    const packaged = packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true, true] });
    const manifest = JSON.parse(readFileSync(join(bookPath, packaged.path, "MANIFEST.json"), "utf8"));
    const names = manifest.files.map((f) => f.name);
    assert.ok(names.includes("卷纲_第一卷.md") && names.includes("卷纲_第二卷.md"), JSON.stringify(names));
    assert.ok(names.includes("目录_第1卷.md"), JSON.stringify(names));
  } finally {
    db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("0.9 字数门槛用 target_words（25 万字书不再被 2 万卡死/放行错线）", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-submit-target-"));
  const bookPath = makeBook(root);
  const db = openDatabase({ dataDir: join(root, "data") });
  try {
    // 60 万字目标的书只写了 5 万字：按旧写死的 2 万会「可投」，按 target 应不可投
    db.prepare(`
      INSERT INTO books (path, synced_at, title, chapters_committed, words_total, target_words)
      VALUES (?, ?, ?, ?, ?, 600000)
    `).run(bookPath, new Date().toISOString(), "长篇", 25, 50000);
    let book = db.prepare("SELECT * FROM books WHERE id = 1").get();
    let result = preflightSubmission(db, book);
    const wordsCheck = result.checks.find((c) => c.id === "words");
    assert.equal(wordsCheck.ok, false, JSON.stringify(wordsCheck));
    assert.match(wordsCheck.label, /600,000/);
    assert.ok(wordsCheck.fix, "每个检查项应带 fix 修复指引");

    // target_words 为 null（老书/未同步）回退 20000，保持旧行为
    db.prepare("UPDATE books SET target_words = NULL, words_total = 20000 WHERE id = 1").run();
    book = db.prepare("SELECT * FROM books WHERE id = 1").get();
    result = preflightSubmission(db, book);
    assert.equal(result.checks.find((c) => c.id === "words").ok, true, "null target 应回退 2 万");
  } finally {
    db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("0.9 大纲缺失时 detail 说明缺什么类型 + fix 指路", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-submit-nooutline-"));
  const bookPath = makeBook(root);
  const db = openDatabase({ dataDir: join(root, "data") });
  try {
    // 删掉全部大纲
    rmSync(join(bookPath, "大纲", "卷纲_第一卷.md"), { force: true });
    rmSync(join(bookPath, "大纲", "目录_前12章.md"), { force: true });
    db.prepare(`
      INSERT INTO books (path, synced_at, title, chapters_committed, words_total)
      VALUES (?, ?, ?, ?, ?)
    `).run(bookPath, new Date().toISOString(), "测试书", 10, 20000);
    const book = db.prepare("SELECT * FROM books WHERE id = 1").get();
    const result = preflightSubmission(db, book);
    const outline = result.checks.find((c) => c.id === "outline");
    assert.equal(outline.ok, false);
    assert.ok(outline.fix, "缺大纲应带 fix");
    // packageSubmission 应抛 preflight_failed 而不是产出一个残缺包（确认已全勾，
    // 挡住它的必须是 preflight 而不是确认 gate）
    assert.throws(
      () => packageSubmission(db, book, { confirmations: [true, true, true, true] }),
      (error) => error.code === "preflight_failed");
  } finally {
    db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

// ── 0.10 确认 gate：防误投（一次评估机会） ─────────────────────────────
test("0.10 package 必须带 4 条人工确认，非全 true 拒绝并落盘审计", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-submit-confirm-"));
  const bookPath = makeBook(root);
  const db = openDatabase({ dataDir: join(root, "data") });
  try {
    db.prepare(`
      INSERT INTO books (path, synced_at, title, chapters_committed, words_total)
      VALUES (?, ?, ?, ?, ?)
    `).run(bookPath, new Date().toISOString(), "测试书", 10, 20000);
    const book = db.prepare("SELECT * FROM books WHERE id = 1").get();

    // 缺 confirmations → 拒绝（老调用方式不能静默通过）
    assert.throws(() => packageSubmission(db, book, { channel: "七猫" }),
      (error) => error.code === "confirmations_required");
    // 有 false → 拒绝
    assert.throws(() => packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, false, true] }),
      (error) => error.code === "confirmations_incomplete");
    // 长度不对 → 拒绝
    assert.throws(() => packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true] }),
      (error) => error.code === "confirmations_incomplete");

    // 全 true → 成功，且确认记录落盘 _pipeline/confirmations.json（审计）
    const packaged = packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true, true] });
    const auditFile = join(bookPath, "_pipeline", "confirmations.json");
    assert.ok(existsSync(auditFile), "确认记录必须落盘");
    const audit = JSON.parse(readFileSync(auditFile, "utf8"));
    assert.ok(Array.isArray(audit.items) && audit.items.length === 1);
    assert.equal(audit.items[0].package_path, packaged.path);
    assert.equal(audit.items[0].confirmations.length, 4);
    assert.ok(audit.items[0].at);
  } finally {
    db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});
