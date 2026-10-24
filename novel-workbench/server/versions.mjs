import { existsSync, readdirSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import {
  countVisibleChars, normalizeText, readText, resolveInsideBook, sha256, toPosix, writeAtomic,
} from "./book-files.mjs";
import { readChapterContent, saveChapterContent } from "./content.mjs";

const KEEP_VERSIONS = 50;

function padChapter(chapter) {
  return String(chapter).padStart(3, "0");
}

function safeSource(source) {
  return String(source || "manual").replace(/[^a-zA-Z0-9_-]/g, "-").slice(0, 40);
}

function stamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

export function recordVersion(db, {
  bookDir, bookId, chapter, content, source = "manual", kind = null, pinned = false,
}) {
  const normalized = normalizeText(content);
  const sha = sha256(normalized);
  const latest = db.prepare(
    "SELECT * FROM versions WHERE book_id = ? AND chapter = ? ORDER BY id DESC LIMIT 1",
  ).get(bookId, chapter);
  if (latest && latest.sha256 === sha && !pinned) return latest;

  const rel = `_pipeline/versions/第${padChapter(chapter)}章/${stamp()}_${safeSource(source)}.md`;
  const target = resolveInsideBook(bookDir, rel);
  writeAtomic(target, normalized);
  const result = db.prepare(`
    INSERT INTO versions (book_id, chapter, kind, source, file_path, sha256, words, parent_id, pinned, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    bookId, chapter, kind || source, source, rel, sha, countVisibleChars(normalized),
    latest?.id ?? null, pinned ? 1 : 0, new Date().toISOString(),
  );
  const id = Number(result.lastInsertRowid);
  db.prepare("UPDATE chapters SET current_version_id = ? WHERE book_id = ? AND number = ?")
    .run(id, bookId, chapter);
  pruneVersions(db, bookDir, bookId, chapter);
  return db.prepare("SELECT * FROM versions WHERE id = ?").get(id);
}

function pruneVersions(db, bookDir, bookId, chapter) {
  const rows = db.prepare(`
    SELECT id, file_path FROM versions
    WHERE book_id = ? AND chapter = ? AND pinned = 0
    ORDER BY id DESC
  `).all(bookId, chapter);
  for (const row of rows.slice(KEEP_VERSIONS)) {
    try {
      const file = join(bookDir, row.file_path);
      if (existsSync(file)) unlinkSync(file);
    } catch {}
    db.prepare("DELETE FROM versions WHERE id = ?").run(row.id);
  }
}

export function listVersions(db, bookId, chapter) {
  return db.prepare(`
    SELECT id, chapter, kind, source, file_path, sha256, words, parent_id, pinned, created_at
    FROM versions WHERE book_id = ? AND chapter = ?
    ORDER BY id DESC
  `).all(bookId, chapter);
}

export function getVersion(db, bookId, versionId) {
  const row = db.prepare("SELECT * FROM versions WHERE id = ? AND book_id = ?").get(versionId, bookId);
  if (!row) return null;
  return row;
}

export function versionContent(bookDir, version) {
  const file = join(bookDir, version.file_path);
  if (!existsSync(file)) throw new Error(`版本文件不存在：${version.file_path}`);
  return readText(file);
}

export function restoreVersion(db, { bookDir, bookId, chapter, versionId }) {
  const version = getVersion(db, bookId, versionId);
  if (!version) throw new Error("版本不存在");
  const content = versionContent(bookDir, version);
  const current = readChapterContent(bookDir, chapter);
  const saved = saveChapterContent({
    bookDir,
    chapter,
    content,
    baseSha: current.sha256,
    committed: false,
    allowCommittedEdit: true,
  });
  const row = recordVersion(db, {
    bookDir,
    bookId,
    chapter,
    content,
    source: `rollback-${versionId}`,
    kind: "rollback",
    pinned: true,
  });
  return { ...saved, version: row };
}

export function versionFiles(bookDir, chapter) {
  const dir = join(bookDir, "_pipeline", "versions", `第${padChapter(chapter)}章`);
  return existsSync(dir) ? readdirSync(dir).length : 0;
}
