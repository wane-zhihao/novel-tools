import { createHash, randomUUID } from "node:crypto";
import {
  copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync,
} from "node:fs";
import { basename, join, resolve } from "node:path";
import {
  countCjkChars, countVisibleChars, isBackupChapterFile, parseChapterFileName, resolveInsideBook,
} from "./book-files.mjs";

const DEFAULT_MIN_WORDS = 20000;   // target_words 缺失（老书/未同步）时的回退线
const CONFIRMATION_COUNT = 4;      // 与 submit.js HUMAN_CONFIRM 一一对应
const MAX_COMMITTED_CHAPTERS = 500;

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

function readBookJson(bookPath, relPath) {
  try {
    return JSON.parse(readFileSync(join(bookPath, relPath), "utf8"));
  } catch {
    return null;
  }
}

function checkBodyIntegrity(db, book) {
  const rows = db.prepare(
    "SELECT number, file_path, words, committed, sha256 FROM chapters WHERE book_id = ? ORDER BY number",
  ).all(book.id);
  const state = readBookJson(book.path, "_pipeline/state.json");
  const tracking = readBookJson(book.path, "追踪/_tracking-state.json");
  const records = tracking?.wordcount_records && typeof tracking.wordcount_records === "object"
    ? tracking.wordcount_records
    : {};
  const errors = [];
  const rowByNumber = new Map(rows.map((row) => [row.number, row]));
  const diskByNumber = new Map();
  let diskFiles = [];
  const bodyDir = join(book.path, "正文");

  try {
    if (resolveInsideBook(book.path, "正文") !== resolve(book.path, "正文")) {
      throw new Error("正文路径不规范");
    }
    diskFiles = readdirSync(bodyDir);
  } catch {
    errors.push("正文目录不存在、越界或无法读取");
  }
  for (const name of diskFiles) {
    // T-2/T-2b：与 sync 同一判定（排除 _原稿_ 备份稿，章号认 1–4 位）。
    // 两端口径必须一致，否则未补零命名的合法书会被这里误判成「缺章」。
    const parsed = parseChapterFileName(name);
    if (!parsed || isBackupChapterFile(name)) continue;
    const number = parsed.number;
    const names = diskByNumber.get(number) ?? [];
    names.push(name);
    diskByNumber.set(number, names);
  }
  for (const [number, names] of diskByNumber) {
    if (names.length > 1) {
      errors.push(`第${String(number).padStart(3, "0")}章存在多份磁盘正文：${names.join("、")}`);
    }
  }

  const countValue = (doc, key) => {
    if (!doc || !Object.hasOwn(doc, key)) return null;
    return Number(doc[key]);
  };
  const committedCount = countValue(state, "chapters_committed");
  const trackingCommittedCount = countValue(tracking, "last_committed_chapter");
  const validCount = (count) => Number.isSafeInteger(count) && count >= 0 && count <= MAX_COMMITTED_CHAPTERS;
  if (committedCount === null || !validCount(committedCount)) {
    errors.push(`state 定稿章数无效或超出支持范围（0–${MAX_COMMITTED_CHAPTERS}）`);
  }
  if (trackingCommittedCount === null || !validCount(trackingCommittedCount)) {
    errors.push(`tracking 定稿章数无效或超出支持范围（0–${MAX_COMMITTED_CHAPTERS}）`);
  }
  const committedNumbers = new Set();
  let verifiedWords = 0;
  let verifiedCjkWords = 0;
  const largestValidCount = Math.max(
    validCount(committedCount) ? committedCount : 0,
    validCount(trackingCommittedCount) ? trackingCommittedCount : 0,
  );
  for (let number = 1; number <= largestValidCount; number += 1) {
    committedNumbers.add(number);
  }
  if (committedCount !== trackingCommittedCount) {
    errors.push(`定稿章数账本不一致：state ${committedCount} 章、tracking ${trackingCommittedCount} 章`);
  }
  for (const row of rows) {
    if (row.committed) committedNumbers.add(Number(row.number));
  }
  for (const number of committedNumbers) {
    const row = rowByNumber.get(number);
    const names = diskByNumber.get(number);
    if (!row || !names?.length) {
      errors.push(`第${String(number).padStart(3, "0")}章缺少章节记录或磁盘正文`);
      continue;
    }
    let diskSha;
    let body;
    try {
      const filePath = resolveInsideBook(book.path, row.file_path);
      const canonicalPath = resolve(book.path, "正文", names[0]);
      if (!diskFiles.length || filePath !== canonicalPath) {
        errors.push(`第${String(number).padStart(3, "0")}章章节路径未指向正文目录中的现役正文`);
        continue;
      }
      body = readFileSync(filePath, "utf8");
      diskSha = sha256File(filePath);
    } catch {
      errors.push(`第${String(number).padStart(3, "0")}章章节路径无效或磁盘正文无法读取`);
      continue;
    }
    if (diskSha !== row.sha256) {
      errors.push(`第${String(number).padStart(3, "0")}章磁盘正文与章节记录不一致`);
    }
    const actualWords = countVisibleChars(body);
    const actualCjk = countCjkChars(body);
    verifiedWords += actualWords;
    verifiedCjkWords += actualCjk;
    if (Number(row.words) !== actualWords) {
      errors.push(`第${String(number).padStart(3, "0")}章字数记录 ${row.words} 与正文复算 ${actualWords} 不一致`);
    }
    const committedSha = records[String(number)]?.body_sha256;
    if (typeof committedSha !== "string" || !committedSha) {
      errors.push(`第${String(number).padStart(3, "0")}章缺少 body_sha256 定稿摘要`);
    } else if (diskSha !== committedSha) {
      errors.push(`第${String(number).padStart(3, "0")}章正文与定稿摘要不一致`);
    }
  }

  const fullPath = join(book.path, "投稿包", "全文.md");
  if (existsSync(fullPath)) {
    // Python _merge_full/N17g 用 encoding="utf-8" 读章节：UTF-8 BOM 原样保留且
    // strip() 不删它，导致首章带 BOM 时会多冠一行书名。校验两侧都剥 BOM，
    // 并容忍「裸全文」与「冠书名全文」两种生成产物，其余任何出入都算不一致。
    const norm = (text) => text.replace(/\r\n?/g, "\n").replaceAll("﻿", "");
    try {
      // 排序与 Python body_files() 一致：先章号后文件名（码点比较，与 Python str 同语义）。
      // 字符串序会把「第10章」排在「第2章」前面——那会让全文的章节顺序错乱。
      const chapterFiles = diskFiles
        .filter((name) => parseChapterFileName(name) && !isBackupChapterFile(name))
        .sort((a, b) => {
          const na = parseChapterFileName(a).number;
          const nb = parseChapterFileName(b).number;
          return na - nb || (a < b ? -1 : a > b ? 1 : 0);
        });
      const body = chapterFiles
        .map((name) => norm(readFileSync(join(book.path, "正文", name), "utf8")).trim())
        .join("\n\n");
      const title = state?.book || basename(book.path);
      const actualFull = norm(readFileSync(fullPath, "utf8"));
      const candidates = [`${body}\n`, `# ${title}\n\n${body}\n`];
      if (!candidates.includes(actualFull)) {
        errors.push("投稿包/全文.md 与当前正文目录章节内容不一致");
      }
    } catch {
      errors.push("投稿包/全文.md 或章节正文无法读取");
    }
  }

  const expectedDiskCount = committedCount;
  const committedRows = rows.filter((row) => row.committed).length;
  if (committedRows !== expectedDiskCount || diskByNumber.size !== expectedDiskCount
    || rows.length !== diskByNumber.size) {
    const known = new Set([...rowByNumber.keys(), ...diskByNumber.keys()]);
    const missing = [...known].filter((number) =>
      Boolean(rowByNumber.get(number)) !== Boolean(diskByNumber.get(number))
      || number > committedCount,
    ).sort((a, b) => a - b);
    const chapterList = missing.length
      ? `；差异章节：${missing.map((number) => `第${String(number).padStart(3, "0")}章`).join("、")}`
      : "";
    errors.push(`章节数量不一致：定稿 ${expectedDiskCount} 章、数据库 ${rows.length} 行（已定稿 ${committedRows}）、磁盘 ${diskByNumber.size} 个正文文件${chapterList}`);
  }

  const words = verifiedWords;
  return {
    words,
    words_visible: words,
    words_cjk: verifiedCjkWords,
    ok: errors.length === 0,
    detail: errors.length ? errors.join("；") : `已核对 ${committedNumbers.size} 章定稿正文，数据库 ${rows.length} 行与磁盘正文一致`,
    fix: errors.length ? "恢复缺失正文，或同步并重新定稿后再次预检" : null,
  };
}

export function preflightSubmission(db, book) {
  const checks = [];
  const integrity = checkBodyIntegrity(db, book);
  const words = integrity.words;
  const wordsVisible = integrity.words_visible ?? words;
  const wordsCjk = integrity.words_cjk ?? 0;
  // 0.9：门槛跟随这本书自己的目标字数，不再写死 2 万
  const hasTarget = Number(book.target_words) > 0;
  const minWords = hasTarget ? Number(book.target_words) : DEFAULT_MIN_WORDS;
  const isProvisional = !hasTarget;

  checks.push({
    id: "body_integrity",
    label: "定稿正文与章节记录一致",
    ok: integrity.ok,
    detail: integrity.detail,
    fix: integrity.fix,
  });

  const divergence = wordsVisible > 0 ? (wordsVisible - wordsCjk) / wordsVisible : 0;
  const visibleOk = wordsVisible >= minWords;
  const cjkOk = wordsCjk >= minWords;
  // 分歧超过 10% 且汉字未达标时，不得只凭 visible 放行
  const wordsOk = visibleOk && (divergence <= 0.1 || cjkOk);

  checks.push({
    id: "words",
    label: `已定稿正文 ≥ ${minWords.toLocaleString("zh-CN")} 字${isProvisional ? "（暂定观测值）" : ""}`,
    ok: wordsOk,
    cjk_ok: cjkOk,
    detail: `当前 ${wordsVisible.toLocaleString("zh-CN")} 字（纯汉字 ${wordsCjk.toLocaleString("zh-CN")}）${isProvisional ? " · 暂定观测值" : ""}`,
    fix: wordsOk ? null : (visibleOk ? "纯汉字数未达到门槛（与可见字符分歧超过 10%），继续补写正文汉字至达标" : "继续写并定稿，或在开书时把目标字数改成实际计划"),
    provisional: isProvisional,
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
  return {
    ready: checks.every((check) => check.ok),
    checks,
    words,
    words_visible: wordsVisible,
    words_cjk: wordsCjk,
    dirty,
    blocking,
    min_words: minWords,
  };
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
  // 0.9：打包实际存在的全部大纲（多卷书带全所有卷纲/目录），不再按写死清单漏文件
  const { volumes, catalogs } = scanOutlines(book.path);
  const outlineFiles = [
    ...volumes.map((name) => `大纲/${name}`),
    ...catalogs.map((name) => `大纲/${name}`),
  ];
  const files = ["投稿包/全文.md", "投稿包/体检报告.md", "投稿包/投稿邮件.md", ...outlineFiles];
  const missingFiles = files.filter((rel) => {
    try {
      resolveInsideBook(book.path, rel);
      return !existsSync(join(book.path, rel));
    } catch {
      return true;
    }
  });
  if (missingFiles.length) {
    const error = new Error(`投稿包文件不完整：${missingFiles.join("、")}`);
    error.code = "manifest_incomplete";
    error.missing = missingFiles;
    throw error;
  }

  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const submissionsRoot = join(book.path, "_pipeline", "submissions");
  mkdirSync(submissionsRoot, { recursive: true });
  let relDir;
  let dir;
  do {
    relDir = `_pipeline/submissions/${stamp}_${safeSegment(channel)}_${randomUUID()}`;
    dir = join(book.path, relDir);
  } while (existsSync(dir));
  mkdirSync(dir);
  const manifest = { schema_version: 1, created_at: new Date().toISOString(), channel, words: preflight.words, files: [] };
  try {
    for (const rel of files) {
      const source = resolveInsideBook(book.path, rel);
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
    if (!manifest.files.some((file) => file.name === "全文.md")) {
      const error = new Error("投稿包文件不完整：全文.md");
      error.code = "manifest_incomplete";
      error.missing = ["投稿包/全文.md"];
      throw error;
    }
    writeFileSync(join(dir, "MANIFEST.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  } catch (error) {
    rmSync(dir, { recursive: true, force: true });
    throw error;
  }

  // 确认审计：谁、何时、对哪个包勾了哪 4 条（追加式，绝不覆盖历史）
  try {
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
  } catch (error) {
    rmSync(dir, { recursive: true, force: true });
    throw error;
  }

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
