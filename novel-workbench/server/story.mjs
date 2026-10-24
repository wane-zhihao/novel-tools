import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import {
  MAX_TEXT_BYTES, backupChapter, countVisibleChars, normalizeText, readText,
  resolveInsideBook, sha256, toPosix, writeAtomic,
} from "./book-files.mjs";

export class StoryConflictError extends Error {
  constructor(current) {
    super("设定已被其他窗口修改");
    this.name = "StoryConflictError";
    this.current = current;
  }
}

const GROUPS = [
  { id: "characters", label: "人物", paths: ["设定/角色.md"] },
  {
    id: "world",
    label: "世界",
    paths: ["设定/题材定位.md", "设定/文风.md", "设定/世界观.md", "设定/势力.md"],
  },
  { id: "outline", label: "大纲", paths: ["大纲/卷纲_第一卷.md", "大纲/目录_前12章.md"] },
  { id: "foreshadow", label: "伏笔", paths: ["追踪/伏笔.md"] },
];

function docInfo(bookPath, relPath, kind) {
  const file = join(bookPath, relPath);
  if (!existsSync(file)) {
    return { path: relPath, kind, exists: false, title: relPath.split("/").at(-1).replace(/\.md$/, ""), words: 0, sha256: null };
  }
  const content = normalizeText(readText(file));
  return {
    path: relPath,
    kind,
    exists: true,
    title: relPath.split("/").at(-1).replace(/\.md$/, ""),
    words: countVisibleChars(content),
    sha256: sha256(content),
  };
}

export function storyIndex(bookPath) {
  const groups = GROUPS.map((group) => ({
    id: group.id,
    label: group.label,
    docs: group.paths.map((path) => docInfo(bookPath, path, group.id)),
  }));
  const chapterOutlines = existsSync(join(bookPath, "大纲"))
    ? readdirSync(join(bookPath, "大纲"))
      .filter((name) => name.startsWith("细纲_") && name.endsWith(".md"))
      .sort()
      .map((name) => docInfo(bookPath, `大纲/${name}`, "outline"))
    : [];
  return { groups, chapter_outlines: chapterOutlines };
}

export function readStoryDoc(bookPath, relPath) {
  const target = resolveInsideBook(bookPath, relPath);
  if (!existsSync(target)) return { path: relPath, exists: false, content: "", sha256: sha256(""), words: 0 };
  const content = normalizeText(readText(target));
  return { path: relPath, exists: true, content, sha256: sha256(content), words: countVisibleChars(content) };
}

export function saveStoryDoc({ bookPath, relPath, content, baseSha = null }) {
  const normalized = normalizeText(content);
  if (Buffer.byteLength(normalized, "utf8") > MAX_TEXT_BYTES) throw new Error("文档超过 2MB");
  const current = readStoryDoc(bookPath, relPath);
  if (baseSha !== null && baseSha !== undefined && baseSha !== current.sha256) {
    throw new StoryConflictError(current);
  }
  const target = resolveInsideBook(bookPath, relPath);
  if (current.exists) backupChapter(bookPath, target, toPosix(relative(bookPath, target)));
  writeAtomic(target, normalized);
  return {
    path: relPath,
    exists: true,
    content: normalized,
    sha256: sha256(normalized),
    words: countVisibleChars(normalized),
  };
}

function foreshadowFromState(bookPath) {
  const path = join(bookPath, "追踪", "_tracking-state.json");
  if (!existsSync(path)) return [];
  try {
    const state = JSON.parse(readFileSync(path, "utf8"));
    const book = state.foreshadow ?? {};
    return Object.entries(book).map(([name, value]) => ({
      name,
      status: value?.status ?? "未知",
      planted_at: value?.planted_at ?? value?.chapter ?? null,
      due_at: value?.due_at ?? value?.target_chapter ?? null,
    }));
  } catch {
    return [];
  }
}

export function foreshadowItems(bookPath) {
  const doc = readStoryDoc(bookPath, "追踪/伏笔.md");
  if (!doc.exists) return foreshadowFromState(bookPath);
  const items = [];
  for (const line of doc.content.split("\n")) {
    const text = line.replace(/^\s*[-*+]\s*/, "").trim();
    if (!text) continue;
    const status = /已回收|回收/.test(text) ? "已回收"
      : /逾期|超期/.test(text) ? "逾期"
        : /未回收|待回收/.test(text) ? "待回收" : "记录";
    items.push({ text, status });
  }
  return items;
}

export function timelineDocs(bookPath) {
  const dir = join(bookPath, "追踪", "时间线");
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((name) => name.endsWith(".md"))
    .sort()
    .map((name) => docInfo(bookPath, `追踪/时间线/${name}`, "timeline"));
}

export function upsertStoryIndex(db, bookId, index) {
  const statement = db.prepare(`
    INSERT INTO story_docs (book_id, kind, path, sha256, updated_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(book_id, path) DO UPDATE SET
      kind = excluded.kind, sha256 = excluded.sha256, updated_at = excluded.updated_at
  `);
  const now = new Date().toISOString();
  for (const group of index.groups) {
    for (const doc of group.docs) {
      statement.run(bookId, doc.kind, doc.path, doc.sha256, now);
    }
  }
  for (const doc of index.chapter_outlines) {
    statement.run(bookId, "outline", doc.path, doc.sha256, now);
  }
}
