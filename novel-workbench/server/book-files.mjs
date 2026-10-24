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

export function chapterFiles(bookDir, chapter) {
  const bodyDir = join(bookDir, "正文");
  if (!existsSync(bodyDir)) return [];
  const prefix = `第${String(chapter).padStart(3, "0")}章`;
  return readdirSync(bodyDir)
    .filter((name) => name.startsWith(prefix) && name.endsWith(".md"))
    .sort()
    .map((name) => join(bodyDir, name));
}

export function findChapterFile(bookDir, chapter) {
  const files = chapterFiles(bookDir, chapter);
  return files.length === 1 ? files[0] : null;
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
