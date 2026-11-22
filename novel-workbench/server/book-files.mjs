import { createHash } from "node:crypto";
import {
  copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync,
  statSync, unlinkSync, writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";

export const MAX_TEXT_BYTES = 2_000_000;

export function toPosix(path) {
  return String(path).split(sep).join("/");
}

export function insideRoot(root, target) {
  const rel = relative(root, target);
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel));
}

export function realInside(root, target) {
  let probe = target;
  while (!existsSync(probe)) {
    const parent = dirname(probe);
    if (parent === probe) break;
    probe = parent;
  }
  try {
    const realProbe = realpathSync(probe);
    const realRoot = realpathSync(root);
    return insideRoot(realRoot, realProbe);
  } catch {
    return false;
  }
}

export function resolveInsideBook(bookDir, relPath) {
  if (typeof relPath !== "string" || !relPath.trim()) throw new Error("路径不能为空");
  const target = resolve(bookDir, relPath);
  if (!insideRoot(bookDir, target) || !realInside(bookDir, target)) {
    throw new Error(`路径越界：${relPath}`);
  }
  return target;
}

export function sha256(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export function countVisibleChars(text) {
  return String(text).replace(/\s+/g, "").length;
}

export const CJK_REGEX = /[\u4e00-\u9fff]/g;

export function countCjkChars(text) {
  return (String(text ?? "").match(CJK_REGEX) ?? []).length;
}

export function readText(path) {
  return readFileSync(path, "utf8");
}

export function normalizeText(text) {
  return String(text ?? "").replace(/\r\n?/g, "\n");
}

export function writeAtomic(target, text) {
  mkdirSync(dirname(target), { recursive: true });
  const temp = `${target}.${process.pid}.${Date.now()}.${Math.random().toString(36).slice(2, 8)}.tmp`;
  writeFileSync(temp, text, "utf8");
  renameSync(temp, target);
}

// PRD M2 / 上游 wordcount_core.py:279：改稿流程会产出 `第NNN章_标题_原稿_<日期|ts>.md`
// 之类的备份稿，它们不是「本章现役正文」，不能进章节记录、全文合成或字数统计。
// 备份稿命名判定（单一事实源）——`_原稿_` 中缀、`.bak`/`~` 后缀、`_backup`。
export function isBackupChapterFile(name) {
  return /_原稿(?:_|\.)/.test(name)
    || /\.(?:bak|backup)(?:\.md)?$/i.test(name)
    || /_backup(?:_|\b)/i.test(name)
    || name.endsWith("~");
}

// T-2b：章号解析。旧实现四处各写一遍 `/^第(\d{3})章…/`，只认三位补零，
// 个人作者导入的「第1章_开局.md」「第10章：供奉堂」被静默忽略 → 该章在
// DB / 书架 / 字数 / 全文里彻底隐形（与 DL-01 叠加即完整的静默缺章链）。
// 现按 TDD §T-2b 规则：文件名里的「第N章」是章号来源，N 允许 1–4 位；
// 分隔符认 `_` / 空格 / `：` / `:`（纵横语料实测同库混用）。
const CHAPTER_NAME = /^第(\d{1,4})章(?:[_ 　:：](.*))?\.md$/;

/**
 * 从正文文件名解析章号与标题。
 * @returns {{number: number, title: string} | null} 不是章节文件则返回 null
 */
export function parseChapterFileName(name) {
  const match = CHAPTER_NAME.exec(name);
  if (!match) return null;
  const number = Number(match[1]);
  if (!Number.isInteger(number) || number < 1) return null;
  return { number, title: match[2] ?? "" };
}

/**
 * 现役正文文件判定（单一事实源）：章节命名 + 非备份稿。
 * 备份稿（`_原稿_`/`.bak`/`_backup`）不是本章正文，见 isBackupChapterFile。
 */
export function isChapterBodyFile(name) {
  return parseChapterFileName(name) !== null && !isBackupChapterFile(name);
}

/**
 * 列出一本书的现役章节文件：按章号升序。
 * 同章多份现役稿（如 `第001章_标题.md` 与 `第001章_标题_v2.md`）全部返回，
 * 由调用方判定歧义——不静默挑一个。
 *
 * 次级键用**码点比较**而非 localeCompare：后者依运行环境 locale 而变，
 * 会与 Python 侧的 str 比较分叉，两侧给出不同的章节顺序。
 */
export function chapterBodyFiles(bookDir) {
  const bodyDir = join(bookDir, "正文");
  if (!existsSync(bodyDir)) return [];
  const out = [];
  for (const name of readdirSync(bodyDir)) {
    const parsed = parseChapterFileName(name);
    if (!parsed || isBackupChapterFile(name)) continue;
    out.push({ ...parsed, name, path: join(bodyDir, name) });
  }
  return out.sort((a, b) => a.number - b.number || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/**
 * 取某章的现役正文文件。
 * @returns {string | null} 该章没有现役稿返回 null；有多个候选返回 null（歧义，不猜）
 */
export function chapterFile(bookDir, chapter) {
  const hits = chapterBodyFiles(bookDir).filter((item) => item.number === chapter);
  return hits.length === 1 ? hits[0].path : null;
}

/**
 * 取正文开头声明的章号（若声明了）。
 * TDD §T-2b 规则 2：章号以正文标题内的「第N章」为准，文件名前缀只作辅助。
 *
 * 只看**首个标题行**（`# …`）而不是正文任意位置：正文里出现「第3章的伏笔」
 * 这类回叙引用不代表本章是第 3 章。首个标题行没有章号时返回 null——
 * 真实夹具首行常是「# 榜上换了名」这种纯标题。
 */
export function chapterNumberInBody(text) {
  const head = String(text ?? "").slice(0, 400);
  const heading = /^\s*#{1,6}\s*(.+)$/m.exec(head);
  const scope = heading ? heading[1] : "";
  const match = /第\s*(\d{1,4})\s*章/.exec(scope);
  if (!match) return null;
  const number = Number(match[1]);
  return Number.isInteger(number) && number >= 1 ? number : null;
}

export function chapterFiles(bookDir, chapter) {
  const bodyDir = join(bookDir, "正文");
  if (!existsSync(bodyDir)) return [];
  return chapterBodyFiles(bookDir)
    .filter((item) => item.number === chapter)
    .map((item) => item.path);
}

export function findChapterFile(bookDir, chapter) {
  return chapterFile(bookDir, chapter);
}

export function chapterRelPath(bookDir, chapterOrFile) {
  const file = typeof chapterOrFile === "number" ? findChapterFile(bookDir, chapterOrFile) : chapterOrFile;
  return file ? toPosix(relative(bookDir, file)) : null;
}

export function backupChapter(bookDir, target, relPath) {
  if (!existsSync(target)) return null;
  try {
    const backupDir = join(bookDir, "_pipeline", "backups", "正文");
    mkdirSync(backupDir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const name = relPath.split("/").at(-1);
    const backup = join(backupDir, `${name}.${stamp}.bak`);
    copyFileSync(target, backup);
    const files = readdirSync(backupDir).filter((entry) => entry.endsWith(".bak")).sort();
    for (const old of files.slice(0, Math.max(0, files.length - 50))) {
      unlinkSync(join(backupDir, old));
    }
    return backup;
  } catch {
    return null;
  }
}

export function fileInfo(path) {
  const stat = statSync(path);
  return { size: stat.size, mtime: stat.mtime.toISOString() };
}
