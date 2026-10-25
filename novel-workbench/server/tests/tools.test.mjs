import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createToolExecutor } from "../agent/tools.mjs";

function withBook(fn) {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-tools-"));
  mkdirSync(join(bookDir, "正文"), { recursive: true });
  writeFileSync(join(bookDir, "正文", "第001章_测试.md"), "顶职名单今天换名字，换掉的正是他。", "utf8");
  try {
    return fn(bookDir, createToolExecutor({ bookDir }));
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
}

test("读写书内文本文件", () => withBook((bookDir, tools) => {
  const read = tools.execute("read_file", { path: "正文/第001章_测试.md" });
  assert.equal(read.ok, true);
  assert.match(read.content, /顶职名单/);

  const write = tools.execute("write_file", { path: "大纲/细纲_第003章.md", content: "# 细纲\n内容" });
  assert.equal(write.ok, true);
  assert.equal(readFileSync(join(bookDir, "大纲", "细纲_第003章.md"), "utf8"), "# 细纲\n内容");

  const listing = tools.execute("list_dir", { path: "." });
  assert.equal(listing.ok, true);
  assert.match(listing.content, /正文\//);
}));

test("append 追加与 .json 合法性守卫", () => withBook((bookDir, tools) => {
  const first = tools.execute("write_file", { path: "大纲/长文.md", content: "第一段。" });
  assert.equal(first.ok, true);
  const appended = tools.execute("write_file", { path: "大纲/长文.md", content: "第二段。", append: true });
  assert.equal(appended.ok, true);
  assert.equal(readFileSync(join(bookDir, "大纲", "长文.md"), "utf8"), "第一段。第二段。");
  assert.match(appended.content, /已追加/);

  // 合法 JSON 放行
  const goodJson = tools.execute("write_file", { path: "大纲/x.json", content: '{"a":1}' });
  assert.equal(goodJson.ok, true);

  // 截断的半截 JSON 被拦下（真书实证：模型把 tracking.json 写成 3636 字符中断，
  // 直到 N16 封板才被 storyctl 拒，白烧一整轮）
  const badJson = tools.execute("write_file", { path: "大纲/x.json", content: '{"a":1,"b":"半截' });
  assert.equal(badJson.ok, false);
  assert.match(badJson.content, /不是合法 JSON/);
  assert.equal(readFileSync(join(bookDir, "大纲", "x.json"), "utf8"), '{"a":1}', "非法写入不应覆盖原文件");

  // 对已存在 JSON 追加 → 拼出非法文档，同样拦下并给出改法
  const appendJson = tools.execute("write_file", { path: "大纲/x.json", content: '{"c":2}', append: true });
  assert.equal(appendJson.ok, false);
  assert.match(appendJson.content, /append/);
}));

test("路径越界被拒绝", () => withBook((bookDir, tools) => {
  const outsideRelative = tools.execute("write_file", { path: "../outside.md", content: "x" });
  assert.equal(outsideRelative.ok, false);
  assert.match(outsideRelative.content, /路径越界/);

  const absolute = tools.execute("read_file", { path: "C:/Windows/win.ini" });
  assert.equal(absolute.ok, false);
  assert.match(absolute.content, /路径越界|不在书目录/);
}));

test("控制文件禁止改写", () => withBook((_bookDir, tools) => {
  for (const path of ["_pipeline/state.json", "_pipeline/.step_token", "_pipeline/run.json"]) {
    const result = tools.execute("write_file", { path, content: "{}" });
    assert.equal(result.ok, false, `${path} 应被拒绝`);
    assert.match(result.content, /禁止改写控制/);
  }
}));

test("非文本扩展名与未知工具被拒绝", () => withBook((_bookDir, tools) => {
  const binary = tools.execute("write_file", { path: "正文/a.exe", content: "x" });
  assert.equal(binary.ok, false);
  assert.match(binary.content, /只允许写入文本文件/);

  const unknown = tools.execute("delete_file", { path: "正文/第001章_测试.md" });
  assert.equal(unknown.ok, false);
  assert.match(unknown.content, /未知工具/);
}));

test("关键产物保护覆盖大小写与目录，正文覆盖会备份", () => withBook((bookDir, tools) => {
  for (const path of [
    "_PIPELINE/STATE.JSON",
    "追踪/_tracking-state.json",
    "投稿包/全文.md",
    "_pipeline/findings_C1.json",
  ]) {
    const result = tools.execute("write_file", { path, content: "x" });
    assert.equal(result.ok, false, `${path} 应被拒绝`);
  }

  const original = readFileSync(join(bookDir, "正文", "第001章_测试.md"), "utf8");
  const write = tools.execute("write_file", { path: "正文/第001章_测试.md", content: "新内容" });
  assert.equal(write.ok, true);
  const backupDir = join(bookDir, "_pipeline", "backups", "正文");
  const backups = readdirSync(backupDir);
  assert.equal(backups.length, 1);
  assert.equal(readFileSync(join(backupDir, backups[0]), "utf8"), original);
}));

test("读取支持 offset/limit 与截断提示", () => withBook((_bookDir, tools) => {
  const result = tools.execute("read_file", { path: "正文/第001章_测试.md", offset: 2, limit: 4 });
  assert.equal(result.ok, true);
  assert.match(result.content, /已截断/);
}));
