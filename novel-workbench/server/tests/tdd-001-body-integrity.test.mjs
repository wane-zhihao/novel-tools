// TDD-001（审计 DL-001，critical）：投稿前必须证明「库里的账」和「磁盘上的正文」是同一件事。
//
// 回归范围：preflight 字数按章节行求和，并将 DB/磁盘/定稿账本做完整性核对；
// sync 修正 books.words_total 后仍保留 state 原值供 drift API 披露；缺少投稿必需文件时拒绝打包：
// 回归覆盖：缓存字数虚高、正文缺失或修改、额外草稿、定稿摘要缺失，及投稿文件缺失。
// 这一章真栽过一次：《顶职》投稿包里 全文.md 缺了两章，MANIFEST 照样「齐全」。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import test from "node:test";
import { openDatabase } from "../db.mjs";
import { syncBook } from "../sync.mjs";
import { packageSubmission, preflightSubmission } from "../submissions.mjs";

// sync 侧算 sha 的方式（sync.mjs:291）：utf8 读全文再哈希。测试必须用同一口径，
// 否则「sha 一致」这个前提本身就是假的。
function sha256Of(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

/**
 * 造一本「其余检查全过」的书：正文按 chapters 求和 = chapters × wordsPerChapter，
 * state.json 缓存字数单独给（用来制造缓存与磁盘的分歧）。
 */
function makeBook(root, { count, wordsPerChapter, cachedWords, targetWords = 20000, fullText = true }) {
  const book = join(root, "book");
  const bookTitle = "《测试》";
  for (const dir of ["正文", "大纲", "设定", "追踪", "投稿包", join("_pipeline", "versions")]) {
    mkdirSync(join(book, dir), { recursive: true });
  }
  const titles = ["换名", "茶缸", "名额", "夜班", "名单", "厂办", "回执", "门口", "账本", "签名"];
  const records = {};
  const chapterBodies = [];
  for (let n = 1; n <= count; n += 1) {
    const title = titles[(n - 1) % titles.length];
    const heading = `# 第${String(n).padStart(3, "0")}章 ${title}\n\n`;
    const body = `${heading}${"文".repeat(wordsPerChapter - heading.replace(/\s+/g, "").length)}\n`;
    chapterBodies.push(body);
    writeFileSync(join(book, "正文", `第${String(n).padStart(3, "0")}章_${title}.md`), body, "utf8");
    records[String(n)] = {
      actual: wordsPerChapter,
      target: 2200,
      metric: "visible_chars_v1",
      status: "ok",
      body_sha256: sha256Of(body),   // 封板时的正文指纹：与磁盘一致 → 不 dirty
    };
  }
  writeFileSync(join(book, "追踪", "_tracking-state.json"), JSON.stringify({
    last_committed_chapter: count,
    wordcount_records: records,
  }, null, 2), "utf8");
  writeFileSync(join(book, "大纲", "卷纲_第一卷.md"), "- 主线：顶职名额\n", "utf8");
  writeFileSync(join(book, "大纲", "目录_第1卷.md"), "1. 换名\n", "utf8");
  if (fullText) {
    writeFileSync(join(book, "投稿包", "全文.md"), `${chapterBodies.map((body) => body.trim()).join("\n\n")}\n`, "utf8");
  }
  writeFileSync(join(book, "投稿包", "体检报告.md"), "# 体检\n", "utf8");
  writeFileSync(join(book, "投稿包", "投稿邮件.md"), "# 邮件\n", "utf8");
  writeFileSync(join(book, "_pipeline", "state.json"), JSON.stringify({
    schema_version: 2,
    book: bookTitle,
    genre: "niandai",
    platform: "qimao",
    target_words: targetWords,
    max_chapters: 12,
    cursor: `C${count + 1}.N09`,
    current_chapter: count,
    chapters_committed: count,
    words_total: cachedWords,        // ← 缓存值，DL-001 的病根
    nodes: {},
    fix_rounds: {},
    n20_rounds: 0,
    llm_rounds: 0,
  }, null, 2), "utf8");
  return book;
}

function withBook(options, fn) {
  const root = mkdtempSync(join(tmpdir(), "wb-tdd001-"));
  const dataDir = join(root, "data");
  const db = openDatabase({ dataDir });
  const bookPath = makeBook(root, options);
  syncBook(db, bookPath);
  const book = db.prepare("SELECT * FROM books").get();
  assert.ok(book, "夹具书必须能同步进库（前提条件）");
  try {
    return fn({ db, book, bookPath });
  } finally {
    db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

const chapterSum = (db, bookId) =>
  db.prepare("SELECT COALESCE(SUM(words), 0) AS n FROM chapters WHERE book_id = ?").get(bookId).n;

test("DL-001 preflight 字数必须按章节求和，不能读 state.json 抄来的缓存值", () => withBook(
  { count: 3, wordsPerChapter: 700, cachedWords: 21000 },
  ({ db, book }) => {
    // 模拟旧缓存或外部写入让 DB 值虚高；同步路径自身会先把它归正。
    db.prepare("UPDATE books SET words_total = 21000 WHERE id = ?").run(book.id);
    assert.equal(db.prepare("SELECT words_total FROM books WHERE id = ?").get(book.id).words_total, 21000,
      "前提：库里的缓存字数虚高");
    assert.equal(chapterSum(db, book.id), 2100, "前提：按章节求和只有 2100");

    const result = preflightSubmission(db, book);
    assert.equal(
      result.words, 2100,
      "preflight.words 必须是 chapters 字数求和（2100），不是 books.words_total 缓存（21000）—— "
      + "server/submissions.mjs:39 至今只读缓存",
    );
    const words = result.checks.find((check) => check.id === "words");
    assert.equal(words.ok, false, "2100 字不该过 20000 字闸门：" + JSON.stringify(words));
    assert.equal(result.ready, false, "缓存虚高时不得判定为可投：" + JSON.stringify(result.checks));
  },
));

test("DL-001 正文文件被删、库里还剩章节行时 preflight 必须拦住", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    // 先确认「一切正常」时是 ready 的，否则下面的红说明不了任何问题
    assert.equal(preflightSubmission(db, book).ready, true, "前提：夹具本身必须全绿");

    unlinkSync(join(bookPath, "正文", "第002章_茶缸.md"));   // 磁盘少一章
    syncBook(db, bookPath);                                  // API refresh 同步时会删除 DB 行
    assert.equal(db.prepare("SELECT COUNT(*) AS n FROM chapters WHERE book_id = ? AND number = 2").get(book.id).n, 0,
      "前提：同步已删除缺失章节行");
    const result = preflightSubmission(db, db.prepare("SELECT * FROM books WHERE id = ?").get(book.id));
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(
      guard && !guard.ok,
      "库里 3 行章节、磁盘只剩 2 个正文文件，preflight 却没有正文完整性失败项："
      + JSON.stringify(result.checks),
    );
    assert.match(guard.detail, /第?002章|第?2章|2/);
    assert.match(guard.detail, /3/);
    assert.match(guard.detail, /2/);
    assert.ok(guard.fix, "拦下来必须给出路（重新同步 / 找回正文）");
    assert.equal(result.ready, false);
  },
));

test("DL-001 多出未定稿正文导致三方章数不一致时 preflight 必须列出章号", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const draft = join(bookPath, "正文", "第004章_草稿.md");
    writeFileSync(draft, "# 第004章 草稿\\n\\n尚未定稿的正文。\\n", "utf8");
    db.prepare(`
      INSERT INTO chapters (book_id, number, title, file_path, words, committed, sha256, dirty)
      VALUES (?, 4, '草稿', '正文/第004章_草稿.md', 10, 0, ?, 0)
    `).run(book.id, sha256Of(readFileSync(draft, "utf8")));

    const result = preflightSubmission(db, book);
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(guard && !guard.ok, "额外草稿使 DB/磁盘为 4 章、state 仅定稿 3 章，必须阻断");
    assert.match(guard.detail, /004章|第?4章|4/);
    assert.equal(result.ready, false);
  },
));

test("DL-001 同章号存在不同正文文件时 preflight 必须阻断歧义", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const duplicate = join(bookPath, "正文", "第001章_另一个版本.md");
    writeFileSync(duplicate, "# 第001章 另一个版本\\n\\n与已定稿正文不同。\\n", "utf8");
    const result = preflightSubmission(db, book);
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(guard && !guard.ok, "同一章号对应多份现役正文时不能选择其一并放行");
    assert.match(guard.detail, /001章|第?1章|重复|多个/);
    assert.equal(result.ready, false);
  },
));

test("DL-001 同章的 _原稿_ 备份稿不得被当成重复现役正文而误拦", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    // PRD M2：现役稿与备份稿并存时只认现役稿；备份不进重复判定、不进全文、不进字数。
    const backup = join(bookPath, "正文", "第002章_茶缸_原稿_20261028.md");
    writeFileSync(backup, "# 第002章 茶缸\n\n（旧稿）陈砚握着茶缸，手心出汗。\n", "utf8");
    const result = preflightSubmission(db, book);
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(guard && guard.ok, "有同章 _原稿_ 备份时不应误报多份磁盘正文：" + JSON.stringify(guard));
    assert.equal(result.ready, true, JSON.stringify(result.checks));
  },
));

test("DL-001 章节行指向书内旧稿时 preflight 必须校验正文目录中的现役文件", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const row = db.prepare("SELECT file_path FROM chapters WHERE book_id = ? AND number = 1").get(book.id);
    const canonical = join(bookPath, "正文", "第001章_换名.md");
    const oldDraft = join(bookPath, "旧稿_第001章.md");
    writeFileSync(oldDraft, readFileSync(canonical));
    db.prepare("UPDATE chapters SET file_path = ? WHERE book_id = ? AND number = 1")
      .run("旧稿_第001章.md", book.id);
    writeFileSync(canonical, "# 第001章 换名\n\n目录中的现役正文已被替换。\n", "utf8");

    assert.notEqual(row.file_path, "旧稿_第001章.md");
    const result = preflightSubmission(db, book);
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(guard && !guard.ok, "预检必须校验正文目录中的唯一现役正文，而非任意书内路径");
    assert.match(guard.detail, /001章|第?1章|路径|现役/);
    assert.equal(result.ready, false);
  },
));

test("DL-001 已定稿章节没有 body_sha256 时不能宣称正文已验证", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const tracking = JSON.parse(readFileSync(join(bookPath, "追踪", "_tracking-state.json"), "utf8"));
    delete tracking.wordcount_records["2"].body_sha256;
    writeFileSync(join(bookPath, "追踪", "_tracking-state.json"), JSON.stringify(tracking), "utf8");

    const result = preflightSubmission(db, book);
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(guard && !guard.ok, "缺少定稿时正文摘要必须视为无法验证并阻断");
    assert.match(guard.detail, /002章|第?2章|body_sha256|摘要/);
    assert.equal(result.ready, false);
  },
));

test("DL-001 磁盘正文与库里 chapters.sha256 不一致时 preflight 必须拦住", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const file = join(bookPath, "正文", "第003章_名额.md");
    const stored = db.prepare("SELECT sha256 FROM chapters WHERE book_id = ? AND number = 3").get(book.id);
    assert.equal(stored.sha256, sha256Of(readFileSync(file, "utf8")), "前提：同步后库里的 sha 与磁盘一致");

    writeFileSync(file, "# 第003章 名额\n\n（改稿后的新正文，没有重新定稿，也没有重新同步）\n", "utf8");
    assert.notEqual(stored.sha256, sha256Of(readFileSync(file, "utf8")), "前提：磁盘已经变了，库里还是旧 sha");

    const result = preflightSubmission(db, book);
    const guard = result.checks.find(
      (check) => !check.ok && /sha|完整|一致|正文/.test(`${check.id}${check.label}${check.detail}`),
    );
    assert.ok(
      guard,
      "第 3 章正文已被改写、库里 sha256 还是封板时的旧值，preflight 仍判定可投——"
      + "需要按磁盘内容复核 sha256（dirty 位只在同步后才更新，不能作为唯一防线）："
      + JSON.stringify(result.checks),
    );
    assert.equal(result.ready, false);
  },
));

test("DL-001 非有限或超范围的定稿章数必须快速失败", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const statePath = join(bookPath, "_pipeline", "state.json");
    const originalState = JSON.parse(readFileSync(statePath, "utf8"));
    writeFileSync(statePath, JSON.stringify({ ...originalState, chapters_committed: 100000000 }), "utf8");
    const result = preflightSubmission(db, book);
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(guard && !guard.ok, "超出书籍支持上限的章数应作为账本错误阻断");
    assert.match(guard.detail, /章数|范围|无效/);
    assert.equal(result.ready, false);

    writeFileSync(statePath, JSON.stringify({ ...originalState, chapters_committed: "Infinity" }), "utf8");
    const nonFinite = preflightSubmission(db, book);
    assert.equal(nonFinite.ready, false, "非有限章数应立即失败，不能进入按章展开循环");

    writeFileSync(statePath, "{}", "utf8");
    const missingLedger = preflightSubmission(db, book);
    assert.equal(missingLedger.ready, false, "缺少定稿章数账本时不能默认成零章并放行");
  },
));

test("DL-001 符合命名规则的追踪草稿不能混入投稿全文", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const draftName = "第004章_不在账本中的草稿.md";
    const draftText = "# 第004章 草稿\n\n不能被拼入投稿全文。\n";
    writeFileSync(join(bookPath, "正文", draftName), draftText, "utf8");
    const fullTextPath = join(bookPath, "投稿包", "全文.md");
    const originalFullText = readFileSync(fullTextPath, "utf8");
    writeFileSync(fullTextPath, `${originalFullText.trimEnd()}\n\n${draftText.trim()}\n`, "utf8");

    const result = preflightSubmission(db, book);
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(guard && !guard.ok, "全文只能包含账本中的已定稿章节正文");
    assert.equal(result.ready, false);
  },
));

test("DL-001 正文目录指向书目录外时必须阻断预检", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const bodyDir = join(bookPath, "正文");
    const backupDir = join(bookPath, "正文-原目录");
    const outsideDir = join(bookPath, "..", "outside-body");
    cpSync(bodyDir, outsideDir, { recursive: true });
    renameSync(bodyDir, backupDir);
    symlinkSync(outsideDir, bodyDir, process.platform === "win32" ? "junction" : "dir");
    try {
      const result = preflightSubmission(db, book);
      const guard = result.checks.find((check) => check.id === "body_integrity");
      assert.ok(guard && !guard.ok, "正文目录通过符号链接指向书外时不能作为现役正文");
      assert.equal(result.ready, false);
    } finally {
      rmSync(bodyDir, { recursive: true, force: true });
      renameSync(backupDir, bodyDir);
      rmSync(outsideDir, { recursive: true, force: true });
    }
  },
));

test("DL-001 投稿大纲目录指向书外时 packageSubmission 必须拒绝", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const outlineDir = join(bookPath, "大纲");
    const backupDir = join(bookPath, "大纲-原目录");
    const outsideDir = join(bookPath, "..", "outside-outline");
    cpSync(outlineDir, outsideDir, { recursive: true });
    renameSync(outlineDir, backupDir);
    symlinkSync(outsideDir, outlineDir, process.platform === "win32" ? "junction" : "dir");
    try {
      assert.throws(
        () => packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true, true] }),
        (error) => error.code === "manifest_incomplete" && /路径无效|文件不完整/.test(error.message),
      );
    } finally {
      rmSync(outlineDir, { recursive: true, force: true });
      renameSync(backupDir, outlineDir);
      rmSync(outsideDir, { recursive: true, force: true });
    }
  },
));

test("DL-001 tracking actual 高于正文可见字符数时不能抬高投稿字数", () => withBook(
  { count: 3, wordsPerChapter: 2100, cachedWords: 6300, targetWords: 10000 },
  ({ db, book, bookPath }) => {
    const trackingPath = join(bookPath, "追踪", "_tracking-state.json");
    const tracking = JSON.parse(readFileSync(trackingPath, "utf8"));
    tracking.wordcount_records["1"].actual = 20000;
    writeFileSync(trackingPath, JSON.stringify(tracking), "utf8");
    syncBook(db, bookPath);
    const fresh = db.prepare("SELECT * FROM books WHERE id = ?").get(book.id);
    const result = preflightSubmission(db, fresh);
    const words = result.checks.find((check) => check.id === "words");
    assert.ok(!words.ok, "定稿记录中的 actual 不能超过按正文内容复算的字数");
    assert.ok(result.words < 20000);
    assert.equal(result.ready, false);
  },
));

test("DL-001 首章带 UTF-8 BOM 时按流水线口径生成的全文不得被误判为不一致", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    // Python _merge_full 用 encoding="utf-8" 读文件：BOM 原样保留，strip() 不删 BOM，
    // 于是首章不以 # 开头 → 合成时额外冠一行书名。校验器必须容忍这个字节级差异。
    const chapterPath = join(bookPath, "正文", "第001章_换名.md");
    const original = readFileSync(chapterPath, "utf8");
    const bom = `﻿${original}`;
    writeFileSync(chapterPath, bom, "utf8");
    const trackingPath = join(bookPath, "追踪", "_tracking-state.json");
    const tracking = JSON.parse(readFileSync(trackingPath, "utf8"));
    tracking.wordcount_records["1"].body_sha256 = sha256Of(bom);
    writeFileSync(trackingPath, JSON.stringify(tracking), "utf8");

    const names = readdirSync(join(bookPath, "正文")).sort();
    const parts = names.map((name) =>
      readFileSync(join(bookPath, "正文", name), "utf8").replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, ""));
    const joined = parts.join("\n\n");
    const full = (joined.startsWith("#") ? "" : "# 《测试》\n\n") + joined + "\n";
    writeFileSync(join(bookPath, "投稿包", "全文.md"), full, "utf8");

    syncBook(db, bookPath);
    const result = preflightSubmission(db, db.prepare("SELECT * FROM books WHERE id = ?").get(book.id));
    assert.equal(result.ready, true, "带 BOM 的现役正文与流水线生成的全文应判一致：" + JSON.stringify(result.checks));

    // Windows 上 Path.write_text 会把 \n 落成 \r\n：CRLF 版全文同样必须判一致。
    const fullPath = join(bookPath, "投稿包", "全文.md");
    writeFileSync(fullPath, full.replace(/\n/g, "\r\n"), "utf8");
    const crlf = preflightSubmission(db, db.prepare("SELECT * FROM books WHERE id = ?").get(book.id));
    assert.equal(crlf.ready, true, "CRLF 打包产物不得被误判为全文不一致：" + JSON.stringify(crlf.checks));
  },
));

test("DL-001 全文.md 内容未覆盖当前章节正文时 preflight 必须阻断", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    writeFileSync(join(bookPath, "投稿包", "全文.md"), "# 旧稿\n这不是正文目录中的当前章节。\n", "utf8");
    const result = preflightSubmission(db, book);
    const guard = result.checks.find((check) => check.id === "body_integrity");
    assert.ok(guard && !guard.ok, "全文必须由当前章节正文完整生成，不能只因文件存在而放行");
    assert.match(guard.detail, /全文|字数|章节正文/);
    assert.equal(result.ready, false);
  },
));

test("DL-001 缺 全文.md 时 packageSubmission 必须失败，而不是产出缺正文的 MANIFEST", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    // 故意让 preflight 在新旧两种字数口径下都通过（求和 21000 ≥ 2 万），
    // 这样下面挡住打包的只可能是「MANIFEST 完整性」，不会和字数检查混在一起。
    unlinkSync(join(bookPath, "投稿包", "全文.md"));
    assert.equal(
      preflightSubmission(db, book).ready, true,
      "前提：其余检查仍应全绿（缺的是投稿包产物，不是正文本身）",
    );

    assert.throws(
      () => packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true, true] }),
      (error) => error.code === "manifest_incomplete" && /全文\.md/.test(error.message),
      "缺少全文.md 应返回可识别的完整性错误",
    );
  },
));

test("DL-001 已有同名投稿包目录不能被失败重试清理", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const submissionsRoot = join(bookPath, "_pipeline", "submissions");
    const collisionDir = join(submissionsRoot, "2026-11-01T00-00-00-000Z_七猫_old");
    mkdirSync(join(collisionDir, "全文.md"), { recursive: true });
    const sentinel = join(collisionDir, "keep.txt");
    writeFileSync(sentinel, "previous package", "utf8");

    assert.doesNotThrow(() => packageSubmission(db, book, {
      channel: "七猫", confirmations: [true, true, true, true],
    }));
    assert.equal(readFileSync(sentinel, "utf8"), "previous package");
  },
));

test("DL-001 确认审计写入失败时不得留下已生成的投稿包", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    const auditPath = join(bookPath, "_pipeline", "confirmations.json");
    mkdirSync(auditPath, { recursive: true });
    const submissionsDir = join(bookPath, "_pipeline", "submissions");
    assert.throws(
      () => packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true, true] }),
      /EISDIR|directory/i,
    );
    assert.ok(!existsSync(submissionsDir) || readdirSync(submissionsDir).length === 0,
      "确认审计失败时应清理本次生成的投稿包");
  },
));

test("DL-001 缺少其他必需投稿文件时失败且不留下包目录或确认审计", () => withBook(
  { count: 3, wordsPerChapter: 7000, cachedWords: 21000 },
  ({ db, book, bookPath }) => {
    unlinkSync(join(bookPath, "投稿包", "体检报告.md"));
    const submissionsDir = join(bookPath, "_pipeline", "submissions");
    assert.throws(
      () => packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true, true] }),
      (error) => error.code === "manifest_incomplete" && /体检报告\.md/.test(error.message),
    );
    assert.ok(!existsSync(submissionsDir) || readdirSync(submissionsDir).length === 0,
      "打包失败不能留下半成品目录");
    assert.equal(existsSync(join(bookPath, "_pipeline", "confirmations.json")), false,
      "打包失败不能追加确认审计");
  },
));
