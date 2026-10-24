import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { openDatabase } from "../db.mjs";
import { discoverBooks, rebuild, syncAll } from "../sync.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const FIXTURES = resolve(SKILL_DIR, "..", "_fixtures");

function withDb(fn) {
  const dataDir = mkdtempSync(join(tmpdir(), "wb-sync-"));
  const db = openDatabase({ dataDir });
  try {
    return fn(db);
  } finally {
    db.close();
    rmSync(dataDir, { recursive: true, force: true });
  }
}

function book(db, suffix) {
  const row = db.prepare("SELECT * FROM books WHERE path LIKE ?").get(`%${suffix}`);
  assert.ok(row, `找不到书 ${suffix}`);
  return row;
}

test("发现夹具书目录", () => {
  const books = discoverBooks([FIXTURES]);
  assert.equal(books.length, 3);
  assert.ok(books.some((path) => path.endsWith("chapter_e2e")));
});

test("同步三本夹具书并写入正确数值", () => withDb((db) => {
  const summary = syncAll(db, [FIXTURES]);
  assert.equal(summary.discovered, 3);
  assert.equal(summary.synced, 3);
  assert.equal(summary.errors.length, 0);

  const e2e = book(db, "chapter_e2e");
  assert.equal(e2e.cursor, "C3.N09");
  assert.equal(e2e.chapters_committed, 2);
  assert.equal(e2e.words_total, 4760);
  assert.equal(e2e.finished, 0);

  const chapters = db.prepare("SELECT * FROM chapters WHERE book_id = ? ORDER BY number").all(e2e.id);
  assert.equal(chapters.length, 2);
  assert.equal(chapters[0].title, "榜上换了名");
  assert.equal(chapters[1].title, "老会计的茶缸");
  assert.ok(chapters.every((chapter) => chapter.committed === 1));
  assert.ok(chapters.every((chapter) => chapter.words > 0));
  assert.ok(chapters.every((chapter) => chapter.sha256.length === 64));

  const nodes = db.prepare("SELECT * FROM nodes WHERE book_id = ?").all(e2e.id);
  assert.equal(nodes.length, 8);

  const findings = db.prepare(
    "SELECT * FROM findings WHERE book_id = ? AND active = 1",
  ).all(e2e.id);
  assert.equal(findings.length, 1);
  assert.equal(findings[0].scope, "chapter");
  assert.equal(findings[0].chapter, 2);
  assert.equal(findings[0].blocking, 1);
  assert.equal(findings[0].source, "N16");
  assert.ok(findings[0].resolved_at);

  const judgments = db.prepare("SELECT * FROM judgments WHERE book_id = ?").all(e2e.id);
  assert.equal(judgments.length, 3);

  const runs = db.prepare("SELECT * FROM runs WHERE book_id = ?").all(e2e.id);
  assert.equal(runs.length, 1);
  assert.equal(runs[0].action, "resume");

  const done = book(db, "workbench_e2e");
  assert.equal(done.cursor, "__done__");
  assert.equal(done.finished, 1);
  const endgame = db.prepare(
    "SELECT * FROM findings WHERE book_id = ? AND scope = 'endgame' AND active = 1",
  ).all(done.id);
  assert.equal(endgame.length, 1);
  assert.equal(endgame[0].chapter, -1);
  assert.equal(endgame[0].blocking, 1);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM judgments WHERE book_id = ?").get(done.id).n, 5);

  const loop = book(db, "chapter_loop");
  assert.equal(loop.cursor, "C1.N15");
  assert.ok(loop.blocked_reason);
}));

test("二次同步按指纹跳过，findings 保留 first_seen", () => withDb((db) => {
  syncAll(db, [FIXTURES]);
  const e2e = book(db, "chapter_e2e");
  const before = db.prepare(
    "SELECT first_seen_at FROM findings WHERE book_id = ? AND active = 1",
  ).get(e2e.id);

  const summary = syncAll(db, [FIXTURES]);
  assert.equal(summary.skipped, 3);
  assert.equal(summary.synced, 0);

  const after = db.prepare(
    "SELECT first_seen_at FROM findings WHERE book_id = ? AND active = 1",
  ).get(e2e.id);
  assert.equal(after.first_seen_at, before.first_seen_at);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM books").get().n, 3);
}));

test("findings 文件消失后只置 active=0，不删历史", () => withDb((db) => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-book-"));
  try {
    mkdirSync(join(bookDir, "_pipeline"), { recursive: true });
    mkdirSync(join(bookDir, "正文"), { recursive: true });
    mkdirSync(join(bookDir, "追踪"), { recursive: true });
    writeFileSync(join(bookDir, "_pipeline", "state.json"), JSON.stringify({
      schema_version: 2,
      book: "《测试》",
      genre: "niandai",
      target_words: 20000,
      max_chapters: 12,
      cursor: "N01",
      current_chapter: 0,
      chapters_committed: 0,
      words_total: 0,
      nodes: {},
    }), "utf8");
    writeFileSync(join(bookDir, "追踪", "_tracking-state.json"), JSON.stringify({ wordcount_records: {} }), "utf8");
    writeFileSync(join(bookDir, "正文", "第001章_测试.md"), "正文内容", "utf8");
    const findingsPath = join(bookDir, "_pipeline", "findings_C1.json");
    writeFileSync(findingsPath, JSON.stringify({
      chapter: 1,
      items: [{ id: "x", criterion: "c", level: "B", blocking: true }],
    }), "utf8");

    syncAll(db, [bookDir]);
    const row = db.prepare("SELECT * FROM findings").get();
    assert.equal(row.active, 1);

    unlinkSync(findingsPath);
    syncAll(db, [bookDir], { force: true });
    const after = db.prepare("SELECT * FROM findings WHERE id = ?").get(row.id);
    assert.equal(after.active, 0);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM findings").get().n, 1);
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
}));

test("强制同步与全量重建结果一致", () => withDb((db) => {
  syncAll(db, [FIXTURES]);
  const counts = () => ({
    books: db.prepare("SELECT COUNT(*) AS n FROM books").get().n,
    chapters: db.prepare("SELECT COUNT(*) AS n FROM chapters").get().n,
    nodes: db.prepare("SELECT COUNT(*) AS n FROM nodes").get().n,
    findings: db.prepare("SELECT COUNT(*) AS n FROM findings").get().n,
    judgments: db.prepare("SELECT COUNT(*) AS n FROM judgments").get().n,
  });
  const initial = counts();

  const forced = syncAll(db, [FIXTURES], { force: true });
  assert.equal(forced.synced, 3);
  assert.deepEqual(counts(), initial);

  const rebuilt = rebuild(db, [FIXTURES]);
  assert.equal(rebuilt.synced, 3);
  assert.deepEqual(counts(), initial);
}));
