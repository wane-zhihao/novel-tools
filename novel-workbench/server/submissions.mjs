import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const MIN_WORDS = 20000;
const REQUIRED_OUTLINES = ["大纲/卷纲_第一卷.md", "大纲/目录_前12章.md"];

function readJson(path, fallback) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return fallback;
  }
}

function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

export function preflightSubmission(db, book) {
  const checks = [];
  const words = Number(book.words_total) || 0;
  checks.push({
    id: "words",
    label: "已封板正文 ≥ 20,000 字",
    ok: words >= MIN_WORDS,
    detail: `当前 ${words} 字`,
  });
  const dirty = db.prepare("SELECT COUNT(*) AS n FROM chapters WHERE book_id = ? AND dirty = 1").get(book.id).n;
  checks.push({ id: "dirty", label: "没有未重新定稿的改动", ok: dirty === 0, detail: dirty ? `${dirty} 章待定稿` : "全部已定稿" });
  const blocking = db.prepare(
    "SELECT COUNT(*) AS n FROM findings WHERE book_id = ? AND active = 1 AND blocking = 1",
  ).get(book.id).n;
  checks.push({ id: "findings", label: "没有阻塞问题", ok: blocking === 0, detail: blocking ? `${blocking} 条` : "0 条" });
  const missing = REQUIRED_OUTLINES.filter((path) => !existsSync(join(book.path, path)));
  checks.push({
    id: "outline",
    label: "卷纲与目录齐全",
    ok: missing.length === 0,
    detail: missing.length ? `缺 ${missing.join("、")}` : "齐全",
  });
  const last = db.prepare(
    "SELECT status FROM submissions WHERE book_id = ? ORDER BY COALESCE(submitted_at, created_at) DESC LIMIT 1",
  ).get(book.id);
  checks.push({
    id: "one_editor",
    label: "没有正在评估的其他编辑",
    ok: !last || !["已投稿", "待回复", "已读"].includes(last.status),
    detail: last ? `最近状态：${last.status}` : "尚未投稿",
  });
  return { ready: checks.every((check) => check.ok), checks, words, dirty, blocking };
}

function safeSegment(value) {
  return String(value || "channel").replace(/[\\/:*?"<>|\s]+/g, "-").slice(0, 40);
}

export function packageSubmission(db, book, { channel = "七猫" } = {}) {
  const preflight = preflightSubmission(db, book);
  if (!preflight.ready) {
    const failed = preflight.checks.filter((check) => !check.ok).map((check) => check.label);
    const error = new Error(`投前检查未通过：${failed.join("；")}`);
    error.code = "preflight_failed";
    error.checks = preflight.checks;
    throw error;
  }
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const relDir = `_pipeline/submissions/${stamp}_${safeSegment(channel)}`;
  const dir = join(book.path, relDir);
  mkdirSync(dir, { recursive: true });
  const files = ["投稿包/全文.md", "投稿包/体检报告.md", "投稿包/投稿邮件.md", ...REQUIRED_OUTLINES];
  const manifest = { schema_version: 1, created_at: new Date().toISOString(), channel, words: preflight.words, files: [] };
  for (const rel of files) {
    const source = join(book.path, rel);
    if (!existsSync(source)) continue;
    const name = rel.split("/").at(-1);
    const target = join(dir, name);
    copyFileSync(source, target);
    manifest.files.push({
      name,
      source: rel,
      sha256: sha256File(target),
      bytes: readFileSync(target).length,
    });
  }
  writeFileSync(join(dir, "MANIFEST.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  const full = manifest.files.find((file) => file.name === "全文.md");
  return {
    path: relDir,
    manifest,
    manuscript_version: `${book.chapters_committed}章/${preflight.words}字/${(full?.sha256 ?? "").slice(0, 12)}`,
  };
}

export function readSubmissionEvents(bookPath, submissionId) {
  const file = join(bookPath, "_pipeline", "submissions.json");
  const doc = readJson(file, { items: [] });
  const items = Array.isArray(doc) ? doc : doc.items ?? [];
  return items.find((item) => String(item.id) === String(submissionId))?.events ?? [];
}

export function appendSubmissionEvent(bookPath, submissionId, event) {
  const file = join(bookPath, "_pipeline", "submissions.json");
  const doc = readJson(file, { items: [] });
  const items = Array.isArray(doc) ? doc : doc.items ?? [];
  const item = items.find((entry) => String(entry.id) === String(submissionId));
  if (!item) {
    const error = new Error("投稿记录不存在");
    error.code = "submission_not_found";
    throw error;
  }
  item.events = item.events ?? [];
  item.events.push({ at: new Date().toISOString(), ...event });
  writeFileSync(file, `${JSON.stringify({ schema_version: 1, items }, null, 2)}\n`, "utf8");
  return item.events;
}
