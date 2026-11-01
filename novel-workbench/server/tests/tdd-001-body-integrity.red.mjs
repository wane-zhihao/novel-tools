// TDD-001（审计 DL-001，critical）：投稿前必须证明「库里的账」和「磁盘上的正文」是同一件事。
//
// 现状（红）：server/submissions.mjs:39 直接读 books.words_total —— 那只是 sync 从
// _pipeline/state.json 抄进库的缓存值（server/sync.mjs:129）；preflight 全程不碰磁盘，
// 也不比对 chapters 行。于是下面四种坏情况都会一路放行到投稿包：
//   1) 缓存字数虚高（漏同步、或 state.json 被手工改过）→ 字数闸门假绿灯；
//   2) 正文文件被删了，库里还留着行 → 章节数和磁盘对不上，仍然「可投」；
//   3) 正文被改过但没重新同步，chapters.sha256 还是旧值 → 交出去的是没人看过的稿子；
//   4) packageSubmission 用 existsSync + continue 逐文件复制（submissions.mjs:129-141），
//      投稿包/全文.md 不见了也不会报错，MANIFEST 里就是没有全文。
// 期望（绿）：preflight 补一条正文完整性检查——字数按 chapters 求和而不是读缓存、
// 章节行数与磁盘正文文件数一致、每章 sha256 与磁盘一致；packageSubmission 在
// MANIFEST 缺 全文.md 时抛 manifest_incomplete，而不是产出残缺包。
//
// 这一章真栽过一次：《顶职》投稿包里 全文.md 缺了两章，MANIFEST 照样「齐全」。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
  for (const dir of ["正文", "大纲", "设定", "追踪", "投稿包", join("_pipeline", "versions")]) {
    mkdirSync(join(book, dir), { recursive: true });
  }
  const titles = ["换名", "茶缸", "名额", "夜班", "名单", "厂办", "回执", "门口", "账本", "签名"];
  const records = {};
  for (let n = 1; n <= count; n += 1) {
    const title = titles[(n - 1) % titles.length];
    const body = `# 第${String(n).padStart(3, "0")}章 ${title}\n\n陈砚把工牌翻过来，名字那一栏还空着。\n`;
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
  if (fullText) writeFileSync(join(book, "投稿包", "全文.md"), "# 全文\n" + "正文".repeat(100), "utf8");
  writeFileSync(join(book, "投稿包", "体检报告.md"), "# 体检\n", "utf8");
  writeFileSync(join(book, "投稿包", "投稿邮件.md"), "# 邮件\n", "utf8");
  writeFileSync(join(book, "_pipeline", "state.json"), JSON.stringify({
    schema_version: 2,
    book: "《测试》",
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
    // 磁盘 3 章 × 700 字 = 2100，缓存却写着 21000（漏同步 / 手改 state.json）
    assert.equal(book.words_total, 21000, "前提：库里的缓存字数虚高");
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

    unlinkSync(join(bookPath, "正文", "第002章_茶缸.md"));   // 磁盘少一章，库里没动
    const result = preflightSubmission(db, book);
    const guard = result.checks.find(
      (check) => !check.ok && /章节|正文|完整/.test(`${check.id}${check.label}`),
    );
    assert.ok(
      guard,
      "库里 3 行章节、磁盘只剩 2 个正文文件，preflight 却没有任何一条检查发现它——"
      + "需要一条「章节数与磁盘一致」的检查：" + JSON.stringify(result.checks),
    );
    assert.match(guard.detail, /3/);
    assert.match(guard.detail, /2/);
    assert.ok(guard.fix, "拦下来必须给出路（重新同步 / 找回正文）");
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

    // 三条出口都要走到：抛错 → 必须是完整性错误码；打包成功 → MANIFEST 必须含全文。
    let error = null;
    let packaged = null;
    try {
      packaged = packageSubmission(db, book, { channel: "七猫", confirmations: [true, true, true, true] });
    } catch (caught) {
      error = caught;
    }
    if (error) {
      assert.equal(
        error.code, "manifest_incomplete",
        `打包被挡住了，但错误码是 ${error.code}：缺 全文.md 必须有专门的完整性错误码，`
        + "不能被 preflight_failed / confirmations_incomplete 顺带盖过去",
      );
    }
    const names = packaged?.manifest?.files?.map((file) => file.name) ?? [];
    assert.ok(
      !packaged || names.includes("全文.md"),
      "packageSubmission 产出的 MANIFEST.json 里根本没有 全文.md（实际："
      + `${JSON.stringify(names)}），却仍然返回成功 —— 投稿包可以没有正文。`
      + "submissions.mjs:129-141 的 existsSync + continue 必须改成缺件即失败。",
    );
  },
));
