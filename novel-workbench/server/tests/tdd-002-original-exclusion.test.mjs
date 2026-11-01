// TDD-002（审计 DL-002，critical）：`_原稿_` 改稿备份不是正文，本仓库到处把它当正文。
//
// 改稿流程会把旧稿另存为 正文/第NNN章_章名_原稿_{日期}.md。上游一直排除它：
//   wordcount_core.py:279  →  if not outline and "_原稿_" in name: return None
//   build_writer_prompt.py:83 →  and "_原稿_" not in entry.name
// 本仓库一处都没排除（全仓 grep "原稿" 只在 server/tests/tdd-002-* 里出现）：
//   · server/sync.mjs:283-305  readdirSync(正文) + /^第(\d{3})章_(.*)\.md$/ —— `(.*)` 贪婪，
//     原稿文件照样匹配，同一章号 upsert 两次，后写的备份把真正文的行覆盖掉（sha/标题/dirty 全错）；
//   · server/book-files.mjs:67-75  chapterFiles 前缀匹配 → 返回 2 个文件 → findChapterFile 返回 null，
//     稿纸和定稿入口直接打不开这一章；
//   · server/gate-report.mjs:52    自己那份 findChapterFile 同病 → 章节体检「找不到唯一的正文文件」；
//   · scripts/inline_runner.py + commit_chapter.py 共 11 处 正文 glob 未过滤（见最后一个用例）：
//     N17 全文.md 会把旧稿和新稿一起合进投稿包，字数、脏稿判定、AI 味扫描全都被污染。
//
// 期望（绿）：一个统一的「本章正文文件」判定（排除 _原稿_ / .bak / 快照），
// JS 与 Python 两侧都走它。本文件先立规矩，实现待补。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chapterFiles, findChapterFile } from "../book-files.mjs";
import { openDatabase } from "../db.mjs";
import { detectPython } from "../settings.mjs";
import { syncBook } from "../sync.mjs";
import { findChapterFile as reportChapterFile } from "../gate-report.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SCRIPTS_DIR = resolve(TEST_DIR, "..", "..", "scripts");
const ORIGINAL_MARK = "_原稿_";

function sha256Of(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

/** 正文/ 目录：real = 每章的现役稿，backups = 章号 → 原稿备份内容 */
function makeBody(book, specs) {
  const body = join(book, "正文");
  mkdirSync(body, { recursive: true });
  for (const spec of specs) {
    writeFileSync(join(body, spec.name), spec.text, "utf8");
  }
  return body;
}

function inTemp(prefix, fn) {
  const root = mkdtempSync(join(tmpdir(), prefix));
  try {
    return fn(root, join(root, "book"));
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

// 现役稿 + 同名前缀的原稿备份（上游改稿流程的真实命名）
const REAL_002 = "# 第002章 茶缸\n\n陈砚把茶缸推到桌角，怕碰翻。\n";
const BACKUP_002 = "# 第002章 茶缸\n\n（旧稿）陈砚握着茶缸，手心出汗。\n";
const BACKUP_003 = "# 第003章 名额\n\n（旧稿）名单还没贴出来。\n";

function bookWithBackups(book) {
  makeBody(book, [
    { name: "第001章_换名.md", text: "# 第001章 换名\n\n人事干事念了两遍名字。\n" },
    { name: "第002章_茶缸.md", text: REAL_002 },
    { name: "第002章_茶缸_原稿_20261028.md", text: BACKUP_002 },
    { name: "第003章_名额.md", text: "# 第003章 名额\n\n公告栏前挤了三个人。\n" },
    { name: "第003章_名额_原稿_1761609600000.md", text: BACKUP_003 },
  ]);
  return book;
}

test("DL-002 chapterFiles / findChapterFile 不得把 _原稿_ 当本章正文", () => inTemp("wb-tdd002-bf-", (root, book) => {
  bookWithBackups(book);

  const files = chapterFiles(book, 2);
  assert.equal(
    files.length, 1,
    `chapterFiles(第2章) 把原稿备份也算进本章，返回 ${files.length} 个文件：`
    + `${JSON.stringify(files.map((file) => basename(file)))} —— `
    + "server/book-files.mjs:67-75 只做前缀匹配，缺 _原稿_ 排除",
  );
  assert.equal(basename(files[0]), "第002章_茶缸.md");
  assert.equal(
    chapterFiles(book, 3).length, 1,
    "第 3 章同理（备份用毫秒时间戳命名：第003章_名额_原稿_1761609600000.md）",
  );
  assert.equal(chapterFiles(book, 1).length, 1, "没有备份的章不受影响");
  assert.ok(
    typeof findChapterFile(book, 2) === "string",
    "findChapterFile 只在「恰好 1 个文件」时返回路径：多出的原稿让它返回 null，"
    + "稿纸 / 定稿 / 版本对比全部打不开第 2 章",
  );
  assert.equal(basename(findChapterFile(book, 2)), "第002章_茶缸.md");
}));

test("DL-002 gate-report 的 findChapterFile 同样必须排除 _原稿_", () => inTemp("wb-tdd002-gr-", (root, book) => {
  bookWithBackups(book);
  const hit = reportChapterFile(book, 2);
  assert.ok(
    typeof hit === "string",
    "server/gate-report.mjs:52 自带一份 findChapterFile，同样被原稿备份打成「不唯一」，"
    + "章节体检只会报「找不到唯一的正文文件」",
  );
  assert.equal(basename(hit), "第002章_茶缸.md");
}));

// ── sync：库里那一行到底是谁 ────────────────────────────────────────────────
function makeSyncableBook(book, cachedWords) {
  for (const dir of ["大纲", "设定", "追踪", "投稿包", "_pipeline"]) {
    mkdirSync(join(book, dir), { recursive: true });
  }
  bookWithBackups(book);
  const records = {
    1: { actual: 7000, target: 2200, metric: "visible_chars_v1", status: "ok" },
    2: { actual: 7000, target: 2200, metric: "visible_chars_v1", status: "ok" },
    3: { actual: 7000, target: 2200, metric: "visible_chars_v1", status: "ok" },
  };
  // 封板指纹按「现役稿」记，这才是事实：备份本来就没人定稿过
  records[1].body_sha256 = sha256Of("# 第001章 换名\n\n人事干事念了两遍名字。\n");
  records[2].body_sha256 = sha256Of(REAL_002);
  records[3].body_sha256 = sha256Of("# 第003章 名额\n\n公告栏前挤了三个人。\n");
  writeFileSync(join(book, "追踪", "_tracking-state.json"), JSON.stringify({
    last_committed_chapter: 3, wordcount_records: records,
  }, null, 2), "utf8");
  writeFileSync(join(book, "大纲", "卷纲_第一卷.md"), "- 主线：顶职名额\n", "utf8");
  writeFileSync(join(book, "大纲", "目录_第1卷.md"), "1. 换名\n", "utf8");
  writeFileSync(join(book, "_pipeline", "state.json"), JSON.stringify({
    schema_version: 2, book: "《测试》", genre: "niandai", platform: "qimao",
    target_words: 20000, max_chapters: 12, cursor: "C4.N09", current_chapter: 3,
    chapters_committed: 3, words_total: cachedWords, nodes: {}, fix_rounds: {},
    n20_rounds: 0, llm_rounds: 0,
  }, null, 2), "utf8");
  return book;
}

test("DL-002 sync 不能被 _原稿_ 备份顶掉本章正文行（sha / 标题 / dirty 全错）", () => inTemp("wb-tdd002-sync-", (root, book) => {
  makeSyncableBook(book, 21000);
  const db = openDatabase({ dataDir: join(root, "data") });
  try {
    syncBook(db, book);
    const rows = db.prepare("SELECT * FROM chapters WHERE book_id = 1 ORDER BY number").all();
    assert.equal(rows.length, 3, `磁盘上是 3 章（另有两个 _原稿_ 备份），库里却有 ${rows.length} 行`);

    const second = rows.find((row) => row.number === 2);
    assert.equal(
      second.sha256, sha256Of(REAL_002),
      "第 2 章的 sha256 来自原稿备份而不是现役正文：sync.mjs:283-305 的 /^第(\\d{3})章_(.*)\\.md$/ "
      + "把 第002章_茶缸_原稿_20261028.md 也认成本章，同一章号写两次、后写覆盖前写",
    );
    assert.equal(second.title, "茶缸", `章节标题被写成了备份名：${JSON.stringify(second.title)}`);
    assert.equal(
      second.dirty, 0,
      `备份覆盖正文后 sha ≠ 封板指纹，本章被误标 dirty=${second.dirty}，`
      + "preflight 的「没有未重新定稿的改动」会因此假报警",
    );
    const third = rows.find((row) => row.number === 3);
    assert.equal(third.sha256, sha256Of("# 第003章 名额\n\n公告栏前挤了三个人。\n"));
    assert.equal(third.dirty, 0, `第 3 章同理（dirty=${third.dirty}）`);
  } finally {
    db.close();
  }
}));

// ── Python：N17 合成全文 ────────────────────────────────────────────────────
const MERGE_HARNESS = [
  "import json, sys",
  "from pathlib import Path",
  "scripts, book = sys.argv[1], Path(sys.argv[2])",
  "sys.path.insert(0, scripts)",
  "import inline_runner as ir",
  "rc, msg = ir._merge_full(book, {'book': '测试书'})",
  "full = book / '投稿包' / '全文.md'",
  "print(json.dumps({'rc': rc, 'msg': msg,",
  "                  'full': full.read_text(encoding='utf-8') if full.is_file() else None},",
  "                 ensure_ascii=False))",
].join("\n");

test("DL-002 N17 合成 全文.md 时不得把 _原稿_ 旧稿一起合进投稿包", (t) => {
  const python = detectPython();
  if (!python) return t.skip("本机没有可执行的 Python");
  inTemp("wb-tdd002-merge-", (root, book) => {
    makeSyncableBook(book, 21000);
    const out = execFileSync(python, ["-c", MERGE_HARNESS, SCRIPTS_DIR, book], {
      encoding: "utf8",
      timeout: 120_000,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    });
    const result = JSON.parse(out.trim().split(/\r?\n/).at(-1));
    assert.equal(result.rc, 0, `N17 合成失败：${JSON.stringify(result)}`);
    assert.ok(result.full, "投稿包/全文.md 应已生成");

    const stale = result.full.split("（旧稿）").length - 1;
    assert.equal(
      stale, 0,
      "全文.md 里混进了原稿备份（inline_runner.py:941 `sorted((book / \"正文\").glob(\"*.md\"))` "
      + `未过滤 _原稿_，出现 ${stale} 处「（旧稿）」）：投稿包会把同一章的新稿和旧稿一起发给编辑`,
    );
    assert.ok(result.full.includes("陈砚把茶缸推到桌角"), "现役正文必须仍在全文里（别把过滤写成漏正文）");
    assert.match(
      result.msg, /合并 3 章/,
      `N17 自报「${result.msg}」：按 5 个文件合了全书，章数与字数双双虚高`,
    );
  });
});

// ── 覆盖面：所有正文收集点都要过滤 ─────────────────────────────────────────
// 行为用例只能钉住最要命的几处；这一条守住「不留死角」：凡是从 正文/ 列举章节文件的
// Python 站点，都必须显式排除 _原稿_（或收敛到统一助手后不再在本行 glob）。
// snapshot_body 例外：快照要连备份一起存，那不是「取正文」。
const SNAPSHOT_FUNCTIONS = new Set(["snapshot_body"]);
const LISTING = /正文["']?\)\s*\.(?:glob|iterdir)\(/;

test("DL-002 scripts/*.py 的每个正文收集点都必须排除 _原稿_", () => {
  const offenders = [];
  for (const name of ["inline_runner.py", "commit_chapter.py", "autopilot.py", "commands.py", "pipeline_spec.py"]) {
    const lines = readFileSync(join(SCRIPTS_DIR, name), "utf8").split(/\r?\n/);
    let current = "";
    for (let i = 0; i < lines.length; i += 1) {
      const defMatch = /^def (\w+)/.exec(lines[i]);
      if (defMatch) current = defMatch[1];
      if (!LISTING.test(lines[i])) continue;
      if (SNAPSHOT_FUNCTIONS.has(current)) continue;
      if (lines[i].includes(ORIGINAL_MARK)) continue;               // 行内已过滤
      if (/chapter_body_files|body_files\(/.test(lines[i])) continue; // 已收敛到统一助手
      offenders.push(`${name}:${i + 1}  ${lines[i].trim()}`);
    }
  }
  assert.deepEqual(
    offenders, [],
    `${offenders.length} 处正文收集点仍把 _原稿_ 当本章正文（上游 wordcount_core.py:279 一直在排除）：\n`
    + offenders.map((line) => "  " + line).join("\n"),
  );
});
