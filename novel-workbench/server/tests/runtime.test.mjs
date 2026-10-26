import assert from "node:assert/strict";
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { loadAgentConfig } from "../agent-config.mjs";
import { AgentRuntime } from "../agent/runtime.mjs";
import { applyFakeEnv } from "./helpers.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");
const FIXTURES = resolve(SKILL_DIR, "..", "_fixtures");

test("mock 运行时用真实 autopilot 推进一步并封住令牌", async () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-run-"));
  cpSync(resolve(FIXTURES, "chapter_e2e"), bookDir, { recursive: true });
  try {
    applyFakeEnv();
    const loaded = loadAgentConfig({ bookDir, env: {} });
    assert.deepEqual(loaded.errors, []);
    const config = structuredClone(loaded.config);

    const runtime = new AgentRuntime({
      bookDir,
      config,
      env: {},
      maxSteps: 1,
    });
    const result = await runtime.run();

    assert.equal(result.status, "max_steps");
    // 审查 B8：llm_rounds 按 chat 次数计（一次 attempt 内工具循环可多次 chat），
    // mock writer 一个节点 = 工具调用轮 + 收尾轮 = 2。
    assert.equal(result.llm_rounds, 2);
    assert.equal(result.steps.length, 1);
    assert.equal(result.steps[0].node, "C3.N09");

    const state = JSON.parse(readFileSync(join(bookDir, "_pipeline", "state.json"), "utf8"));
    assert.equal(state.cursor, "C3.N10");
    assert.equal(state.nodes["C3.N09"].status, "done");
    assert.ok(!existsSync(join(bookDir, "_pipeline", ".step_token")));

    const outline = join(bookDir, "大纲", "细纲_第003章.md");
    assert.ok(existsSync(outline));
    assert.ok(statSync(outline).size >= 500);

    const serialized = JSON.stringify(result);
    assert.ok(!/token/i.test(serialized), "结果中不应出现 token");
    assert.ok(!serialized.includes("done_cmd"), "结果中不应出现 done_cmd");
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
});

test("输出触到长度上限：不当空回复结束，改为要求分段写入", async () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-trunc-"));
  try {
    // 真书实证：write_file 一次写整章 ≈ 4-6K tokens 被 max_tokens 截断，
    // DeepSeek 丢弃截断中的 tool_calls → finish=length + 空 content + 零工具调用。
    const calls = [];
    const scripted = [
      { content: "", toolCalls: [], finishReason: "length", usage: { prompt: 100, completion: 4096 } },
      { content: "回执：已分两段写入", toolCalls: [], finishReason: "stop", usage: { prompt: 120, completion: 20 } },
    ];
    const runtime = new AgentRuntime({
      bookDir,
      config: {
        roles: {
          writer: { provider: "fake", model: "fake-model", max_attempts: 1, max_tokens: 8192 },
          judge: { provider: "fake", model: "fake-model" },
        },
        providers: {},
      },
      env: {},
      providerFactory: () => ({
        chat: async (request) => {
          calls.push(request);
          return scripted[Math.min(calls.length - 1, scripted.length - 1)];
        },
      }),
    });
    const events = [];
    runtime.onEvent = (event) => events.push(event);
    const result = await runtime.runAgent(
      { node: "C1.N12", subagent_role: "writer", subagent_prompt: "写第 1 章正文", evidence: [] },
      runtime.config.roles.writer,
      "",
      null,
    );
    assert.equal(result.text, "回执：已分两段写入");
    assert.equal(calls.length, 2);
    const lastUser = calls[1].messages.filter((m) => m.role === "user").at(-1).content;
    assert.match(lastUser, /分段写入/);
    assert.match(lastUser, /append=true/);
    assert.ok(events.some((e) => e.type === "node_truncated"), "应发出 node_truncated 事件");
    assert.ok(!events.some((e) => e.type === "node_nudge"), "截断不应走通用推一把分支");
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
});

test("章节节点采用覆盖的有限重试预算，记录完整拒绝原因并在耗尽时停止", async () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-preflight-retry-"));
  try {
    const feedbacks = [];
    const events = [];
    const runtime = new AgentRuntime({
      bookDir,
      config: {
        roles: {
          writer: { provider: "fake", model: "fake-model", timeout_s: 30, max_attempts: 2, max_tokens: 8192 },
          judge: { provider: "fake", model: "fake-model", timeout_s: 30 },
        },
        providers: {},
      },
      env: {},
      providerFactory: () => ({ chat: async () => ({
        content: "回执：完成", toolCalls: [], finishReason: "stop",
        usage: { prompt: 1, completion: 1 },
      }) }),
    });
    runtime.onEvent = (event) => events.push(event);
    runtime.runAgent = async (_instruction, _roleConfig, feedback) => {
      feedbacks.push(feedback);
      return { text: "回执：完成", usage: { prompt: 1, completion: 1 }, toolSteps: 0 };
    };
    const doneReplies = [
      { code: 2, payload: { error: "tracking_preflight_failed", detail: "delta 字节超限" } },
      { code: 2, payload: { error: "text_preflight_failed", detail: "not-is-comparison" } },
      { code: 0, payload: { ok: true } },
    ];
    runtime.callDone = async () => doneReplies.shift();

    const result = await runtime.executeNode({
      node: "C1.N12",
      subagent_role: "writer",
      subagent_prompt: "写第 1 章正文并填写追踪事务",
      evidence: [],
      max_attempts: 4,
    });

    assert.equal(result.status, "done");
    assert.equal(result.attempt, 3);
    assert.match(feedbacks[1], /delta 字节超限/);
    assert.match(feedbacks[2], /not-is-comparison/);
    assert.deepEqual(events.filter((event) => event.type === "node_started").map((event) => event.attempt), [1, 2, 3]);
    assert.match(events.find((event) => event.type === "node_retry")?.reason ?? "", /delta 字节超限/);
    assert.equal(doneReplies.length, 0);
    assert.equal(events.find((event) => event.type === "node_retry")?.error, "tracking_preflight_failed");
    assert.match(events.find((event) => event.type === "node_retry")?.detail ?? "", /delta 字节超限/);

    const exhaustedReplies = [
      { code: 2, payload: { error: "tracking_preflight_failed", detail: "delta 字节超限" } },
      { code: 2, payload: { error: "text_preflight_failed", detail: "not-is-comparison" } },
      { code: 0, payload: { ok: true } },
    ];
    runtime.callDone = async () => exhaustedReplies.shift();
    const exhausted = await runtime.executeNode({
      node: "C1.N12",
      subagent_role: "writer",
      subagent_prompt: "写第 1 章正文并填写追踪事务",
      evidence: [],
      max_attempts: 2,
    });
    assert.equal(exhausted.status, "failed");
    assert.equal(exhausted.attempt, 2);
    assert.equal(exhaustedReplies.length, 1, "耗尽预算后不得再调用 done");
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
});

test("工具参数 JSON 非法（截断）时回喂明确提示，不拿空对象硬执行", async () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-badargs-"));
  try {
    const calls = [];
    const scripted = [
      {
        content: "",
        toolCalls: [{ id: "c1", name: "write_file", arguments: '{"path": "正文/第001章_x.md", "content": "# 第 1 章' }],
        finishReason: "tool_calls",
        usage: { prompt: 100, completion: 500 },
      },
      { content: "回执：改成分段写", toolCalls: [], finishReason: "stop", usage: { prompt: 120, completion: 20 } },
    ];
    const runtime = new AgentRuntime({
      bookDir,
      config: {
        roles: {
          writer: { provider: "fake", model: "fake-model", max_attempts: 1, max_tokens: 8192 },
          judge: { provider: "fake", model: "fake-model" },
        },
        providers: {},
      },
      env: {},
      providerFactory: () => ({
        chat: async (request) => {
          calls.push(request);
          return scripted[Math.min(calls.length - 1, scripted.length - 1)];
        },
      }),
    });
    await runtime.runAgent(
      { node: "C1.N12", subagent_role: "writer", subagent_prompt: "写第 1 章正文", evidence: [] },
      runtime.config.roles.writer,
      "",
      null,
    );
    assert.equal(calls.length, 2);
    const toolMessage = calls[1].messages.filter((m) => m.role === "tool").at(-1).content;
    assert.match(toolMessage, /不是合法 JSON/);
    assert.match(toolMessage, /append=true/);
    assert.ok(!toolMessage.includes("(无扩展名)"), "不应再撞出误导性的扩展名错误");
    assert.ok(!existsSync(join(bookDir, "正文")), "非法参数不应落任何文件");
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
});
