import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { ensurePipelineConfig } from "../settings.mjs";
import { applyFakeEnv } from "./helpers.mjs";

function withBook(fn) {
  const root = mkdtempSync(join(tmpdir(), "wb-judgment-"));
  const book = join(root, "book");
  const dataDir = join(root, "data");
  mkdirSync(join(book, "正文"), { recursive: true });
  mkdirSync(join(book, "大纲"), { recursive: true });
  mkdirSync(join(book, "设定"), { recursive: true });
  writeFileSync(join(book, "正文", "第001章_测试.md"), "顶职名单今天换名字，换掉的正是他。他站在宣传栏前，手指发凉。", "utf8");
  writeFileSync(join(book, "大纲", "选题报告.md"), "年代文选题：厂办顶职，亲情背叛与清誉危机。", "utf8");
  writeFileSync(join(book, "设定", "题材定位.md"), "年代文，重人情与工厂秩序，主角靠时序记忆翻盘。", "utf8");
  writeFileSync(join(book, "大纲", "卷纲_第一卷.md"), "第一卷围绕顶职名额与三天期限展开。", "utf8");
  try {
    return fn({ root, book, dataDir });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function validate({ book, dataDir }, node, judgment) {
  applyFakeEnv();
  const settings = ensurePipelineConfig({ dataDir });
  const script = [
    "import json, sys",
    "from pathlib import Path",
    "sys.path.insert(0, sys.argv[1])",
    "from commands import _validate_judgment",
    "book = Path(sys.argv[2])",
    "node = sys.argv[3]",
    "judgment = json.loads(Path(sys.argv[4]).read_text(encoding='utf-8'))",
    "print(json.dumps(_validate_judgment(book, node, judgment), ensure_ascii=False))",
  ].join("\n");
  const file = join(dataDir, "judgment.json");
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(file, JSON.stringify(judgment), "utf8");
  const output = execFileSync(settings.python, [
    "-c", script, resolve(settings.autopilot, ".."), book, node, file,
  ], { encoding: "utf8", env: { ...process.env, WORKBENCH_CONFIG: settings.pipeline_config_path } });
  return JSON.parse(output);
}

test("合法 N04 判词通过", () => withBook((ctx) => {
  const bad = validate(ctx, "N04", {
    verdict: "pass",
    items: [{
      criterion: "选题可投",
      verdict: "pass",
      source: "大纲/选题报告.md",
      // 连续 ≥12 字（审查 C 级：代码下限对齐 prompt 的 12–30，标点归一化后 13 字）
      quote: "厂办顶职，亲情背叛与清誉危机",
    }],
  });
  assert.deepEqual(bad, []);
}));

test("空 items、空 quote、缺 verdict 都被拒绝", () => withBook((ctx) => {
  assert.ok(validate(ctx, "N04", { verdict: "pass", items: [] }).length > 0);
  assert.ok(validate(ctx, "N04", {
    verdict: "pass",
    items: [{ criterion: "x", verdict: "pass", source: "大纲/选题报告.md", quote: "" }],
  }).length > 0);
  assert.ok(validate(ctx, "N15", {
    items: [{ criterion: "x", verdict: "pass", source: "正文/第001章_测试.md", quote: "顶职名单今天换名字" }],
  }).some((item) => item.reason.includes("verdict")));
}));

test("N15 引用必须来自正文", () => withBook((ctx) => {
  const bad = validate(ctx, "C1.N15", {
    verdict: "pass",
    items: [{
      criterion: "前200字有冲突",
      verdict: "pass",
      source: "设定/题材定位.md",
      quote: "年代文",
    }],
  });
  assert.ok(bad.some((item) => item.reason.includes("不在允许范围")));
  const good = validate(ctx, "C1.N15", {
    verdict: "pass",
    items: [{
      criterion: "前200字有冲突",
      verdict: "pass",
      source: "正文/第001章_测试.md",
      quote: "顶职名单今天换名字，换掉的正是他",
    }],
  });
  assert.deepEqual(good, []);
}));

test("N20 机械项允许 quote 为空但必须带 metric", () => withBook((ctx) => {
  const missingMetric = validate(ctx, "N20", {
    verdict: "pass",
    items: [{ id: "5", level: "B", verdict: "pass", source: "正文/第001章_测试.md", quote: "" }],
  });
  assert.ok(missingMetric.some((item) => item.reason.includes("metric")));
  const ok = validate(ctx, "N20", {
    verdict: "pass",
    items: [{
      id: "5", level: "B", verdict: "pass", source: "正文/第001章_测试.md", quote: "",
      metric: { actual: 1, expected: 0 },
    }],
  });
  assert.deepEqual(ok, []);
}));
