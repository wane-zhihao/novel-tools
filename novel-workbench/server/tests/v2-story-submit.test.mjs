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

    const packaged = packageSubmission(db, book, { channel: "七猫" });
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
