// TDD-004（审计 DL-004）：字数有两个口径，工作台只报一个，还拿它当签约门槛。
//
// books.words_total / chapters.words 全部来自 visible_chars_v1（server/book-files.mjs:48
// countVisibleChars = 去掉空白后的所有字符），它把中文标点、英文字母、数字一律算进「字数」。
// 上游一直知道这口径偏大：wordcount_core.py:15 `METRIC = "visible_chars_v1"`，
// 而本仓库的 preflight / 体检报告只报这一个数，读的人以为它就是编辑口里的「字数」。
//
// 真书实测（《顶职》前 9 章）：visible 21,112 / 纯汉字 17,533 —— 差 17%，
// 而闸门线是 20,000。按 visible 已经「达标可投」，按汉字还差 2,467 字。
// 更麻烦的是这条 20,000 本身没有官方出处：F:\小说\platforms\qimao\config\thresholds.json
// 写着 current_min_words: null、status: "provisional"，20000 只是 historical_observations。
//
// 期望（绿）：preflight 双口径并列（visible 与汉字），分歧超过 10% 时不得只凭 visible 放行；
// 落在回退线（DEFAULT_MIN_WORDS=20000）上时那条检查必须标 provisional，说清「暂定观测值」。
// 本文件先把这些行为立成红测试，实现待补。
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { openDatabase } from "../db.mjs";
import { syncBook } from "../sync.mjs";
import { preflightSubmission } from "../submissions.mjs";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

// 与上游同一条正则（inline_runner.py:1552 的汉字口径：U+4E00–U+9FFF，标点/字母/数字都不算）
const CJK_ONLY = new RegExp("[\\u4e00-\\u9fff]", "g");
const countCjk = (text) => (text.match(CJK_ONLY) ?? []).length;
// 本仓库现在的口径：去掉所有空白后逐字符（标点、字母、数字都算）
const countVisible = (text) => text.replace(/\s+/g, "").length;

const CJK_LINE = "陈砚把工牌翻过来，名字那一栏还空着。\n";
const LATIN_LINE = "He waited by the door for 20 minutes.\n";

/**
 * 造一章正文：汉字 + 标点 + 拉丁/数字混排，让两种口径真的分叉。
 * 返回文本与两种口径的计数（断言用同一个来源，不写死数字）。
 */
function makeChapter(number, title, cjkLines, latinLines) {
  const text = `# 第${String(number).padStart(3, "0")}章 ${title}\n\n`
    + CJK_LINE.repeat(cjkLines) + LATIN_LINE.repeat(latinLines);
  return { number, title, text, visible: countVisible(text), cjk: countCjk(text) };
}

/**
 * 一本「按 visible 达标、按汉字不达标」的书；target_words 传 null 时走 20000 回退线。
 */
function makeBook(root, { targetWords, chapters }) {
  const book = join(root, "book");
  for (const dir of ["正文", "大纲", "设定", "追踪", "投稿包", "_pipeline"]) {
    mkdirSync(join(book, dir), { recursive: true });
  }
  const records = {};
  for (const chapter of chapters) {
    writeFileSync(
      join(book, "正文", `第${String(chapter.number).padStart(3, "0")}章_${chapter.title}.md`),
      chapter.text, "utf8",
    );
    // 上游记的就是 visible_chars_v1，工作台照抄 —— 这正是分歧的起点
    records[String(chapter.number)] = {
      actual: chapter.visible, target: 2200, metric: "visible_chars_v1", status: "ok",
    };
  }
  writeFileSync(join(book, "追踪", "_tracking-state.json"), JSON.stringify({
    last_committed_chapter: chapters.length, wordcount_records: records,
  }, null, 2), "utf8");
  writeFileSync(join(book, "大纲", "卷纲_第一卷.md"), "- 主线：顶职名额\n", "utf8");
  writeFileSync(join(book, "大纲", "目录_第1卷.md"), "1. 换名\n", "utf8");
  writeFileSync(join(book, "投稿包", "全文.md"), "# 全文\n", "utf8");
  writeFileSync(join(book, "_pipeline", "state.json"), JSON.stringify({
    schema_version: 2, book: "《测试》", genre: "niandai", platform: "qimao",
    target_words: targetWords, max_chapters: 12, cursor: "C3.N09", current_chapter: 2,
    chapters_committed: 2,
    words_total: chapters.reduce((sum, chapter) => sum + chapter.visible, 0),
    nodes: {}, fix_rounds: {}, n20_rounds: 0, llm_rounds: 0,
  }, null, 2), "utf8");
  return book;
}

function preflightFor(options) {
  const root = mkdtempSync(join(tmpdir(), "wb-tdd004-"));
  const chapters = options.chapters ?? [makeChapter(1, "换名", 500, 36), makeChapter(2, "茶缸", 500, 36)];
  const bookPath = makeBook(root, { ...options, chapters });
  const db = openDatabase({ dataDir: join(root, "data") });
  try {
    syncBook(db, bookPath);
    const book = db.prepare("SELECT * FROM books").get();
    assert.ok(book, "前提：夹具书必须同步进库");
    return {
      book,
      chapters,
      result: preflightSubmission(db, book),
      totals: {
        visible: chapters.reduce((sum, chapter) => sum + chapter.visible, 0),
        cjk: chapters.reduce((sum, chapter) => sum + chapter.cjk, 0),
      },
    };
  } finally {
    db.close();
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

// 上游 thresholds.json 的唯一入口是仓库 config.json（严禁硬编码绝对路径）
function readThresholds() {
  let config;
  try {
    config = JSON.parse(readFileSync(join(REPO_ROOT, "config.json"), "utf8"));
  } catch {
    return null;
  }
  const path = config.QIMAO_THRESHOLDS;
  if (!path || !existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

test("DL-004 preflight 必须同时给出 visible_chars_v1 与汉字两个口径", () => {
  const { result, totals } = preflightFor({ targetWords: 20000 });
  assert.ok(
    "words_cjk" in result,
    "preflight 只有 visible 一个口径（submissions.mjs:39 读 books.words_total），"
    + `磁盘上的汉字数 ${totals.cjk} 从未出现在投前检查里 —— 需要 words_cjk`,
  );
  assert.equal(result.words_visible, totals.visible, "words_visible 必须等于按正文算出的可见字符数");
  assert.equal(result.words_cjk, totals.cjk, "words_cjk 必须等于同一批正文的纯汉字数（[\\u4e00-\\u9fff]）");
  assert.ok(
    totals.visible > totals.cjk * 1.1,
    `前提：夹具得真的分叉（visible ${totals.visible} / cjk ${totals.cjk}）`,
  );
});

test("DL-004 字数那条检查的人话里要同时写两个数，不能只报 visible", () => {
  const { result, totals } = preflightFor({ targetWords: 20000 });
  const words = result.checks.find((check) => check.id === "words");
  assert.ok(words, "前提：preflight 有字数检查");
  assert.match(
    words.detail, /汉字|纯汉字/,
    `「当前 ${totals.visible.toLocaleString("zh-CN")} 字」这句话让作者以为够本了；`
    + `detail 必须把汉字口径 ${totals.cjk.toLocaleString("zh-CN")} 一起写出来：${JSON.stringify(words)}`,
  );
  assert.ok(
    words.detail.includes(String(totals.cjk.toLocaleString("zh-CN")))
    || words.detail.includes(String(totals.cjk)),
    `detail 里看不到汉字数 ${totals.cjk}：${words.detail}`,
  );
});

test("DL-004 回退到 20000 线时必须标 provisional（那条线没有官方出处）", () => {
  const { result } = preflightFor({ targetWords: null });
  const words = result.checks.find((check) => check.id === "words");
  const thresholds = readThresholds();

  const { result: withTarget } = preflightFor({ targetWords: 600000 });
  const explicit = withTarget.checks.find((check) => check.id === "words");

  if (thresholds) {
    assert.equal(thresholds.status, "provisional", "上游自述：全部统计仅作 advisory");
    assert.equal(
      thresholds.submission?.current_min_words ?? null, null,
      "上游没有确认的最低字数；20000 只出现在 historical_observations 里",
    );
    assert.equal(
      words.provisional, true,
      "target_words 缺失时 preflight 回退到 DEFAULT_MIN_WORDS=20000（submissions.mjs:5,41），"
      + "却把它和这本书自己填的目标字数一样呈现为硬门槛 → 必须在检查项上标 provisional，"
      + `并说明它是暂定观测值：${JSON.stringify(words)}`,
    );
    assert.equal(explicit.provisional ?? false, false, "作者自己填的 target_words 不是暂定线");
    assert.match(
      `${words.label}${words.detail}`, /暂定|provisional|观测/,
      `回退线的文案要让作者知道这不是平台条款：${JSON.stringify(words)}`,
    );
  } else {
    assert.equal(
      words.provisional, true,
      "回退到 20000 的字数闸门必须标 provisional（读不到 thresholds.json 也要标，那是代码里的常量属性）",
    );
  }
});

test("DL-004 visible 过线、汉字没过线时不得只凭 visible 放行", () => {
  // 复刻《顶职》的真实形状：visible 20,000+ 而汉字 1 万 6 —— 差 20%，闸门线 20,000
  const { result, totals } = preflightFor({
    targetWords: 20000,
    chapters: [makeChapter(1, "换名", 700, 90), makeChapter(2, "茶缸", 700, 90)],
  });
  const words = result.checks.find((check) => check.id === "words");
  const divergence = (totals.visible - totals.cjk) / totals.visible;

  assert.ok(
    totals.visible >= 20000 && totals.cjk < 20000 && divergence > 0.1,
    `前提没成立：visible ${totals.visible} / cjk ${totals.cjk} / 分歧 ${(divergence * 100).toFixed(1)}%`,
  );
  assert.ok(
    words.cjk_ok === false || words.ok === false || result.ready === false,
    "按 visible 已经「≥ 20,000 字，可投」，按汉字还差 "
    + `${20000 - totals.cjk} 字（分歧 ${(divergence * 100).toFixed(1)}%，真书 21,112/17,533 = 17%）。`
    + "分歧超过 10% 时这条检查不能是绿灯，否则作者按 visible 投稿、编辑按汉字打回。"
    + JSON.stringify(words),
  );
});
