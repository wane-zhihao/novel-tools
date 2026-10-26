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
  // 大纲组不写死路径：多卷书会有 卷纲_第一卷/第二卷… 与 目录_第1卷/第2卷…，
  // 写死单卷会让第二卷起在故事页完全不可见。文档由 outlineDocs() 动态补齐。
  { id: "outline", label: "大纲", paths: [] },
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

// 中文卷号 → 数字，用于自然排序（第10卷不能排在第二卷前面）。认不出的排最后。
const CN_DIGITS = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };

function volumeOrder(name) {
  const match = /^(卷纲|目录)_第(.+?)[卷部]\.md$/.exec(name);
  // 老书的 目录_前12章.md 不按卷分，固定排在同组最后
  if (!match) return { kind: 2, rank: Number.POSITIVE_INFINITY };
  const raw = match[2];
  let rank;
  if (/^\d+$/.test(raw)) rank = Number(raw);
  else if (raw.length === 1) rank = CN_DIGITS[raw] ?? Number.POSITIVE_INFINITY;
  else if (/^十[一二三四五六七八九]$/.test(raw)) rank = 10 + (CN_DIGITS[raw[1]] ?? 0);
  else if (/^[二三四五六七八九]十[一二三四五六七八九]?$/.test(raw)) {
    rank = (CN_DIGITS[raw[0]] ?? 0) * 10 + (raw.length === 3 ? CN_DIGITS[raw[2]] ?? 0 : 0);
  } else rank = Number.POSITIVE_INFINITY;
  // 卷纲排在目录前
  return { kind: match[1] === "卷纲" ? 0 : 1, rank };
}

// 动态扫大纲：多卷书的卷纲/目录全部收录（写死单卷会让第二卷起在故事页消失）。
// 认两种命名：新书 卷纲_第N卷.md / 目录_第N卷.md，老书 目录_前12章.md。
function outlineDocs(bookPath) {
  const dir = join(bookPath, "大纲");
  if (!existsSync(dir)) return [];
  const names = readdirSync(dir)
    .filter((name) => /^(卷纲|目录)_.+\.md$/.test(name) && !name.startsWith("细纲_"))
    .sort((a, b) => {
      const left = volumeOrder(a);
      const right = volumeOrder(b);
      if (left.kind !== right.kind) return left.kind - right.kind;
      if (left.rank !== right.rank) return left.rank - right.rank;
      return a.localeCompare(b, "zh-CN");
    });
  return names.map((name) => docInfo(bookPath, `大纲/${name}`, "outline"));
}

export function storyIndex(bookPath) {
  const groups = GROUPS.map((group) => ({
    id: group.id,
    label: group.label,
    docs: group.id === "outline"
      ? outlineDocs(bookPath)
      : group.paths.map((path) => docInfo(bookPath, path, group.id)),
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
