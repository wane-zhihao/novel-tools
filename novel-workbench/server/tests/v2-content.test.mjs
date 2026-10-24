import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { openDatabase } from "../db.mjs";
import {
  ContentConflictError, ContentError, readChapterContent, saveChapterContent,
} from "../content.mjs";
import { lineDiff, diffStats } from "../diff.mjs";
import { listVersions, recordVersion, restoreVersion, versionContent } from "../versions.mjs";

function withBook(fn) {
  const root = mkdtempSync(join(tmpdir(), "wb-v2-"));
  const book = join(root, "book");
  mkdirSync(join(book, "正文"), { recursive: true });
  mkdirSync(join(book, "_pipeline"), { recursive: true });
  writeFileSync(join(book, "正文", "第001章_测试.md"), "# 第一章\n\n顶职名单换了名字。", "utf8");
  try {
    return fn({ root, book, dataDir: join(root, "data") });
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

test("正文读取、保存、备份与乐观锁", () => withBook(({ book }) => {
  const current = readChapterContent(book, 1);
  assert.equal(current.exists, true);
  assert.ok(current.words > 0);

  assert.throws(() => saveChapterContent({
    bookDir: book, chapter: 1, content: "新内容", baseSha: "bad",
  }), ContentConflictError);

  const saved = saveChapterContent({
    bookDir: book, chapter: 1, content: "# 第一章\n\n改过的正文。", baseSha: current.sha256,
  });
  assert.ok(saved.words > 0);
  assert.ok(saved.backup);
  assert.equal(readFileSync(saved.filePath, "utf8"), "# 第一章\n\n改过的正文。");
  assert.throws(() => saveChapterContent({
    bookDir: book, chapter: 1, content: "x", baseSha: saved.sha256, committed: true,
  }), (error) => error instanceof ContentError && error.code === "committed_edit_required");
}));

test("版本记录、diff 与回滚", () => withBook(({ book, dataDir }) => {
  const db = openDatabase({ dataDir });
  try {
    db.prepare("INSERT INTO books (path, synced_at) VALUES (?, ?)").run(book, new Date().toISOString());
    const bookId = 1;
    const first = recordVersion(db, {
      bookDir: book, bookId, chapter: 1, content: "# 第一章\n\n原文。", source: "manual",
    });
    const second = recordVersion(db, {
      bookDir: book, bookId, chapter: 1, content: "# 第一章\n\n原文。\n\n新增一句。", source: "ai-draft",
    });
    assert.equal(listVersions(db, bookId, 1).length, 2);
    const lines = lineDiff(versionContent(book, first), versionContent(book, second));
    const stats = diffStats(lines);
    assert.equal(stats.added >= 1, true);
    assert.equal(stats.removed, 0);

    const restored = restoreVersion(db, {
      bookDir: book, bookId, chapter: 1, versionId: first.id,
    });
    assert.equal(restored.content, "# 第一章\n\n原文。");
    assert.equal(listVersions(db, bookId, 1).length, 3);
    assert.equal(readFileSync(join(book, "正文", "第001章_测试.md"), "utf8"), "# 第一章\n\n原文。");
  } finally {
    db.close();
  }
}));

test("diff 能识别替换与删除", () => {
  const lines = lineDiff("a\nb\nc", "a\nB\nc\nd");
  const stats = diffStats(lines);
  assert.equal(stats.added, 2);
  assert.equal(stats.removed, 1);
  assert.equal(lines.filter((line) => line.type === "same").length, 2);
});

test("多章文件冲突会明确报错", () => withBook(({ book }) => {
  writeFileSync(join(book, "正文", "第001章_第二份.md"), "duplicate", "utf8");
  assert.throws(() => readChapterContent(book, 1), (error) => error.code === "ambiguous_chapter");
}));
