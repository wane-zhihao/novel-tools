import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, renameSync, rmSync, statSync, unlinkSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { openDatabase } from "../db.mjs";
import { discoverBooks, rebuild, syncAll } from "../sync.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const FIXTURES = resolve(SKILL_DIR, "..", "_fixtures");

function sha256Of(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

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

test("指纹检出同 size 同 mtime 的内容变化与 rename 互换", () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-book-"));
  const dataDir = mkdtempSync(join(tmpdir(), "wb-sync-"));
  const db = openDatabase({ dataDir });
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
    const body = join(bookDir, "正文", "第001章_测试.md");
    writeFileSync(body, "一二三", "utf8");
    assert.equal(syncAll(db, [bookDir]).synced, 1);
    assert.equal(syncAll(db, [bookDir]).skipped, 1);

    // 同 size 同 mtime、内容不同：旧指纹（maxMtime:totalSize:count）会漏，新指纹必须检出
    // （先把 mtime 归整到整数毫秒，避开 NTFS 亚毫秒精度导致的 round-trip 误差）
    const stamp = new Date(Math.floor(statSync(body).mtimeMs));
    utimesSync(body, stamp, stamp);
    const before = statSync(body);
    writeFileSync(body, "四五六", "utf8");
    utimesSync(body, stamp, stamp);
    const after = statSync(body);
    assert.equal(after.size, before.size);
    assert.equal(after.mtimeMs, before.mtimeMs);
    assert.equal(syncAll(db, [bookDir]).synced, 1);

    // rename 互换：两个等大文件交换名字，size/mtime 多重集不变，逐文件指纹必须检出
    writeFileSync(join(bookDir, "正文", "a.md"), "甲甲甲甲", "utf8");
    writeFileSync(join(bookDir, "正文", "b.md"), "乙乙乙乙", "utf8");
    assert.equal(syncAll(db, [bookDir]).synced, 1);
    assert.equal(syncAll(db, [bookDir]).skipped, 1);
    renameSync(join(bookDir, "正文", "a.md"), join(bookDir, "正文", "tmp.md"));
    renameSync(join(bookDir, "正文", "b.md"), join(bookDir, "正文", "a.md"));
    renameSync(join(bookDir, "正文", "tmp.md"), join(bookDir, "正文", "b.md"));
    assert.equal(syncAll(db, [bookDir]).synced, 1);
  } finally {
    db.close();
    rmSync(bookDir, { recursive: true, force: true });
    rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("rebuild 从磁盘重建 versions，回写 dirty/author_state 并清空全部业务表", () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-book-"));
  const dataDir = mkdtempSync(join(tmpdir(), "wb-sync-"));
  const db = openDatabase({ dataDir });
  try {
    mkdirSync(join(bookDir, "_pipeline", "versions", "第001章"), { recursive: true });
    mkdirSync(join(bookDir, "正文"), { recursive: true });
    mkdirSync(join(bookDir, "追踪"), { recursive: true });
    writeFileSync(join(bookDir, "_pipeline", "state.json"), JSON.stringify({
      schema_version: 2,
      book: "《测试》",
      genre: "niandai",
      target_words: 20000,
      max_chapters: 12,
      cursor: "C1.N16",
      current_chapter: 1,
      chapters_committed: 1,
      words_total: 8,
      nodes: { "C1.N16": { status: "done", at: "2026-10-25T10:00:00+0800" } },
    }), "utf8");
    const content = "第一章正文内容。";
    writeFileSync(join(bookDir, "正文", "第001章_测试.md"), content, "utf8");
    writeFileSync(join(bookDir, "追踪", "_tracking-state.json"), JSON.stringify({
      wordcount_records: { "1": { actual: 8, body_sha256: sha256Of(content) } },
    }), "utf8");
    writeFileSync(join(bookDir, "_pipeline", "versions", "第001章", "2026-10-25T10-00-00-000Z_manual.md"), content, "utf8");

    const summary = rebuild(db, [bookDir]);
    assert.equal(summary.synced, 1);
    assert.equal(summary.errors.length, 0);

    const row = db.prepare("SELECT * FROM books").get();
    const versions = db.prepare("SELECT * FROM versions WHERE book_id = ?").all(row.id);
    assert.equal(versions.length, 1);
    assert.equal(versions[0].chapter, 1);
    assert.equal(versions[0].source, "manual");
    assert.equal(versions[0].kind, "manual");
    assert.equal(versions[0].sha256, sha256Of(content));
    assert.ok(versions[0].words > 0);
    assert.equal(versions[0].created_at, "2026-10-25T10:00:00.000Z");

    const chapter = db.prepare("SELECT * FROM chapters WHERE book_id = ?").get(row.id);
    assert.equal(chapter.current_version_id, versions[0].id);
    assert.equal(chapter.dirty, 0);
    assert.equal(chapter.author_state, "committed");

    // 封板后正文被改（追踪记录不变）→ 同步把 dirty/author_state 推导回写
    writeFileSync(join(bookDir, "正文", "第001章_测试.md"), `${content}改动`, "utf8");
    syncAll(db, [bookDir]);
    const dirtyRow = db.prepare("SELECT * FROM chapters WHERE book_id = ?").get(row.id);
    assert.equal(dirtyRow.dirty, 1);
    assert.equal(dirtyRow.author_state, "draft_edit");

    // resetData 覆盖全部业务表：垃圾行在再次 rebuild 后被清掉，真实数据从磁盘重建
    const junkAt = new Date().toISOString();
    db.prepare("INSERT INTO story_docs (book_id, kind, path, sha256, updated_at) VALUES (?, 'junk', 'junk/x.md', 'x', ?)")
      .run(row.id, junkAt);
    db.prepare("INSERT INTO analytics_events (book_id, type, payload_json, created_at) VALUES (?, 'junk', '{}', ?)")
      .run(row.id, junkAt);
    db.prepare("INSERT INTO approvals (book_id, run_id, node, status, created_at) VALUES (?, 'r', 'n', 'pending', ?)")
      .run(row.id, junkAt);
    db.prepare("INSERT INTO versions (book_id, chapter, file_path, sha256, created_at) VALUES (?, 9, 'junk.md', 'x', ?)")
      .run(row.id, junkAt);
    db.prepare("INSERT INTO run_events (run_id, seq, event_json, created_at) VALUES ('junk', 1, '{}', ?)")
      .run(junkAt);

    rebuild(db, [bookDir]);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM story_docs WHERE book_id = ? AND path = 'junk/x.md'").get(row.id).n, 0);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM analytics_events WHERE book_id = ?").get(row.id).n, 0);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM approvals WHERE book_id = ?").get(row.id).n, 0);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM versions WHERE book_id = ?").get(row.id).n, 1);
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM run_events").get().n, 0);
    const rebuilt = db.prepare("SELECT * FROM chapters WHERE book_id = ?").get(row.id);
    assert.equal(rebuilt.current_version_id, versions[0].id);
    assert.equal(rebuilt.dirty, 1);
  } finally {
    db.close();
    rmSync(bookDir, { recursive: true, force: true });
    rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});
