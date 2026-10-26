// errors.js 是纯映射层，用 node --test 直接测（不需要浏览器）。
// 这层存在的意义：错误码 → 人话 → 出口 只能有一份定义。测试守住三件事：
// 1) 已知码都有人话和（能做的）出口；2) 未知码不被吞掉；3) 出口闭包真能跳转。
import assert from "node:assert/strict";
import test from "node:test";
import { errorState, knownErrorCodes } from "../../web/lib/errors.js";

function makeError(code, { message = "", details = null } = {}) {
  const error = new Error(message || code);
  error.code = code;
  if (details) error.details = details;
  return error;
}

test("0.6 每个已知错误码都产出人话，且不把 code 当文案", () => {
  const codes = knownErrorCodes();
  assert.ok(codes.length >= 10, `已知码太少：${codes.join(",")}`);
  for (const code of codes) {
    const state = errorState(makeError(code));
    assert.equal(state.code, code);
    assert.ok(state.text && state.text.length > 4, `${code} 没有可读文案：${state.text}`);
    // 文案不该是裸 code 或后端堆栈
    assert.ok(!state.text.includes("Error:"), `${code} 把异常类名漏进了文案`);
    assert.ok(!/^\s*[a-z_]+\s*$/.test(state.text), `${code} 文案看起来就是 code 本身`);
    assert.ok(["danger", "warn", "ok"].includes(state.tone), `${code} tone 非法：${state.tone}`);
    assert.ok(Array.isArray(state.actions));
  }
});

test("0.6 出口按钮真能触发跳转（不是死按钮）", () => {
  const visited = [];
  const ctx = {
    go: (hash) => visited.push(hash),
    bookId: 7,
    chapter: 5,
    retryWithLength: () => visited.push("RETRY_LENGTH"),
  };

  // 闸门阻断 → 去审第 5 章
  const gate = errorState(makeError("gate_blocked", { details: { gate: { blocking_count: 2 } } }), ctx);
  assert.match(gate.text, /2 条阻断/);
  assert.equal(gate.actions.length, 1);
  gate.actions[0].onClick();
  assert.deepEqual(visited, ["#/books/7/revise/5"]);

  // 字数越界 → 就地放行重定稿（而不是跳页）
  visited.length = 0;
  const length = errorState(makeError("length_out_of_band"), ctx);
  length.actions[0].onClick();
  assert.deepEqual(visited, ["RETRY_LENGTH"]);

  // 跳章 → 去前一章（chapter-1，且不小于 1）
  visited.length = 0;
  errorState(makeError("chapter_gap"), ctx).actions[0].onClick();
  assert.deepEqual(visited, ["#/books/7/write/4"]);
  visited.length = 0;
  errorState(makeError("chapter_gap"), { ...ctx, chapter: 1 }).actions[0].onClick();
  assert.deepEqual(visited, ["#/books/7/write/1"], "第 1 章不该退到第 0 章");
});

test("0.6 未知错误码不吞掉，仍给后端人话但不下结论", () => {
  const state = errorState(makeError("weird_new_code", { message: "磁盘满了" }));
  assert.equal(state.text, "磁盘满了", "未知码应保留后端文案（已是人话）");
  assert.equal(state.code, "weird_new_code");
  assert.deepEqual(state.actions, [], "未知码不该凭空造出口");
});

test("0.6 完全没有 code 的异常也给可用文案", () => {
  const state = errorState(new Error("网络断了"));
  assert.equal(state.text, "网络断了");
  assert.equal(state.code, null);
  assert.deepEqual(state.actions, []);
});

test("0.6 已登记的关键码齐全（防止悄悄删掉出口）", () => {
  const codes = knownErrorCodes();
  // 定稿线的码一个都不能少——每个都对应一条用户会撞到的真实失败
  for (const must of [
    "gate_blocked", "length_out_of_band", "tracking_missing", "tracking_invalid",
    "chapter_gap", "chapter_busy", "run_running", "revision_conflict", "commit_timeout",
    "quality_blocked", "quality_tool_unavailable",
    "content_conflict", "committed_edit_required", "story_conflict",
  ]) {
    assert.ok(codes.includes(must), `缺少错误码映射：${must}`);
  }
});
