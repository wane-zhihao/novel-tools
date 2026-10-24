import { existsSync } from "node:fs";
import { join } from "node:path";
import {
  MAX_TEXT_BYTES, backupChapter, chapterFiles, countVisibleChars, findChapterFile, normalizeText,
  readText, resolveInsideBook, sha256, toPosix, writeAtomic,
} from "./book-files.mjs";
import { relative } from "node:path";

export class ContentConflictError extends Error {
  constructor(current) {
    super("正文已被其他窗口修改");
    this.name = "ContentConflictError";
    this.current = current;
  }
}

export class ContentError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ContentError";
    this.code = code;
  }
}

function emptyChapter() {
  return {
    exists: false,
    filePath: null,
    relPath: null,
    content: "",
    sha256: sha256(""),
    words: 0,
  };
}

export function readChapterContent(bookDir, chapter) {
  if (!Number.isInteger(chapter) || chapter < 1 || chapter > 9999) {
    throw new ContentError("invalid_chapter", "章节号必须是 1–9999 的整数");
  }
  const files = chapterFiles(bookDir, chapter);
  if (!files.length) return emptyChapter();
  if (files.length > 1) {
    throw new ContentError(
      "ambiguous_chapter",
      `第${chapter}章存在多个正文文件，请先合并/删除多余文件：${files.map((file) => file.split(/[\\/]/).at(-1)).join("、")}`,
    );
  }
  const file = files[0];
  const content = normalizeText(readText(file));
  return {
    exists: true,
    filePath: file,
    relPath: toPosix(relative(bookDir, file)),
    content,
    sha256: sha256(content),
    words: countVisibleChars(content),
  };
}

function safeTitle(title, chapter) {
  const cleaned = String(title ?? "")
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "")
    .replace(/^[. ]+|[. ]+$/g, "")
    .slice(0, 40);
  return cleaned || `第${String(chapter).padStart(3, "0")}章`;
}

export function saveChapterContent({
  bookDir,
  chapter,
  content,
  baseSha = null,
  title = null,
  committed = false,
  allowCommittedEdit = false,
}) {
  if (!Number.isInteger(chapter) || chapter < 1 || chapter > 9999) {
    throw new ContentError("invalid_chapter", "章节号必须是 1–9999 的整数");
  }
  const normalized = normalizeText(content);
  if (Buffer.byteLength(normalized, "utf8") > MAX_TEXT_BYTES) {
    throw new ContentError("too_large", "单章正文超过 2MB");
  }
  const current = readChapterContent(bookDir, chapter);
  if (baseSha !== null && baseSha !== undefined && baseSha !== current.sha256) {
    throw new ContentConflictError(current);
  }
  if (committed && !allowCommittedEdit) {
    throw new ContentError("committed_edit_required", "本章已封板，修改前需要确认“解除封板”");
  }
  if (!current.exists && !normalized.trim()) {
    return { ...current, skipped: true };
  }
  const relPath = current.relPath
    || `正文/第${String(chapter).padStart(3, "0")}章_${safeTitle(title, chapter)}.md`;
  const target = resolveInsideBook(bookDir, relPath);
  const backup = current.exists ? backupChapter(bookDir, target, relPath) : null;
  writeAtomic(target, normalized);
  return {
    exists: true,
    filePath: target,
    relPath,
    content: normalized,
    sha256: sha256(normalized),
    words: countVisibleChars(normalized),
    previous: current.exists ? current.content : null,
    backup,
  };
}

export function chapterExists(bookDir, chapter) {
  return Boolean(findChapterFile(bookDir, chapter));
}

export function chapterPathFor(bookDir, chapter) {
  const file = findChapterFile(bookDir, chapter);
  return file ? join(bookDir, "正文", file.split(/[\\/]/).at(-1)) : null;
}
