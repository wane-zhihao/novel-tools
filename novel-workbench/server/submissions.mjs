import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const DEFAULT_MIN_WORDS = 20000;   // target_words 缺失（老书/未同步）时的回退线
const CONFIRMATION_COUNT = 4;      // 与 submit.js HUMAN_CONFIRM 一一对应

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

// 0.9：大纲要求改为「扫目录」——卷纲_第N卷.md（中文或阿拉伯数字卷号）与
// 目录_*.md 各有至少一份即合格。新书流水线产出 目录_第N卷.md，老书产出
// 目录_前12章.md，两种命名都认，多卷书不再被「缺 目录_前12章.md」永久卡死。
function scanOutlines(bookPath) {
  const dir = join(bookPath, "大纲");
  let entries = [];
  try {
    entries = readdirSync(dir);
  } catch {
    return { volumes: [], catalogs: [] };
  }
  return {
    volumes: entries.filter((n) => /^卷纲_第.+卷\.md$/.test(n)),
    catalogs: entries.filter((n) => /^目录_.+\.md$/.test(n)),
  };
}

export function preflightSubmission(db, book) {
  const checks = [];
  const words = Number(book.words_total) || 0;
  // 0.9：门槛跟随这本书自己的目标字数，不再写死 2 万
  const minWords = Number(book.target_words) > 0 ? Number(book.target_words) : DEFAULT_MIN_WORDS;
  checks.push({
    id: "words",
    label: `已定稿正文 ≥ ${minWords.toLocaleString("zh-CN")} 字`,
    ok: words >= minWords,
    detail: `当前 ${words.toLocaleString("zh-CN")} 字`,
    fix: words >= minWords ? null : "继续写并定稿，或在开书时把目标字数改成实际计划",
  });
  const dirty = db.prepare("SELECT COUNT(*) AS n FROM chapters WHERE book_id = ? AND dirty = 1").get(book.id).n;
  checks.push({
    id: "dirty", label: "没有未重新定稿的改动",
    ok: dirty === 0,
    detail: dirty ? `${dirty} 章待定稿` : "全部已定稿",
    fix: dirty ? "去稿纸把改过的章「重新定稿」" : null,
  });
  const blocking = db.prepare(
    "SELECT COUNT(*) AS n FROM findings WHERE book_id = ? AND active = 1 AND blocking = 1",
  ).get(book.id).n;
  checks.push({
    id: "findings", label: "没有阻塞问题",
    ok: blocking === 0,
    detail: blocking ? `${blocking} 条` : "0 条",
    fix: blocking ? "去「审」处理阻断项后重新定稿" : null,
  });
  const { volumes, catalogs } = scanOutlines(book.path);
  const missingTypes = [];
  if (!volumes.length) missingTypes.push("卷纲（大纲/卷纲_第N卷.md）");
  if (!catalogs.length) missingTypes.push("章节目录（大纲/目录_*.md）");
  checks.push({
    id: "outline",
    label: "卷纲与目录齐全",
    ok: missingTypes.length === 0,
    detail: missingTypes.length
      ? `缺 ${missingTypes.join("、")}`
      : `卷纲 ${volumes.length} 份 · 目录 ${catalogs.length} 份`,
    fix: missingTypes.length ? "让流水线补产大纲，或手动在 大纲/ 下按命名规则补文件" : null,
  });
  const last = db.prepare(
    "SELECT status FROM submissions WHERE book_id = ? ORDER BY COALESCE(submitted_at, created_at) DESC LIMIT 1",
  ).get(book.id);
  checks.push({
    id: "one_editor",
    label: "没有正在评估的其他编辑",
    ok: !last || !["已投稿", "待回复", "已读"].includes(last.status),
    detail: last ? `最近状态：${last.status}` : "尚未投稿",
    fix: last && !["已投稿", "待回复", "已读"].includes(last.status) ? null
      : "同一本书同时只投一位编辑；等上一位出结果或明确拒绝后再投",
  });
  return { ready: checks.every((check) => check.ok), checks, words, dirty, blocking, min_words: minWords };
}

function safeSegment(value) {
  return String(value || "channel").replace(/[\\/:*?"<>|\s]+/g, "-").slice(0, 40);
}

export function packageSubmission(db, book, { channel = "七猫", confirmations = null } = {}) {
  // 0.10 确认 gate：投稿包会随邮件直接发出去，每本书只有一次签约评估机会。
  // 4 条人工确认必须全为 true 才打包；确认记录落盘留审计。
  if (!Array.isArray(confirmations)) {
    const error = new Error("打包前必须逐条确认 4 项人工检查");
    error.code = "confirmations_required";
    throw error;
  }
  if (confirmations.length !== CONFIRMATION_COUNT || confirmations.some((value) => value !== true)) {
    const error = new Error(`人工确认未全部勾选（${confirmations.filter((v) => v === true).length}/${CONFIRMATION_COUNT}）`);
    error.code = "confirmations_incomplete";
    throw error;
  }
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
  // 0.9：打包实际存在的全部大纲（多卷书带全所有卷纲/目录），不再按写死清单漏文件
  const { volumes, catalogs } = scanOutlines(book.path);
  const outlineFiles = [
    ...volumes.map((name) => `大纲/${name}`),
    ...catalogs.map((name) => `大纲/${name}`),
  ];
  const files = ["投稿包/全文.md", "投稿包/体检报告.md", "投稿包/投稿邮件.md", ...outlineFiles];
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

  // 确认审计：谁、何时、对哪个包勾了哪 4 条（追加式，绝不覆盖历史）
  const auditPath = join(book.path, "_pipeline", "confirmations.json");
  const audit = readJson(auditPath, { items: [] });
  const items = Array.isArray(audit?.items) ? audit.items : [];
  items.push({
    at: new Date().toISOString(),
    channel,
    package_path: relDir,
    confirmations: confirmations.slice(),
    words: preflight.words,
  });
  mkdirSync(join(book.path, "_pipeline"), { recursive: true });
  writeFileSync(auditPath, `${JSON.stringify({ schema_version: 1, items }, null, 2)}\n`, "utf8");

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
