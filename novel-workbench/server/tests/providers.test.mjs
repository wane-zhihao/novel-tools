import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { inspect } from "node:util";
import test from "node:test";
import { OpenAIProvider, MockProvider, createProvider } from "../agent/providers.mjs";
import { createToolExecutor } from "../agent/tools.mjs";

function normalize(text) {
  return text.replace(/[^\u4e00-\u9fa5a-zA-Z0-9]/g, "");
}

test("OpenAI 客户端发送 tools 与鉴权头并解析 tool_calls", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      json: async () => ({
        choices: [{
          message: {
            content: "",
            tool_calls: [{
              id: "call-1",
              type: "function",
              function: { name: "read_file", arguments: "{\"path\":\"大纲/a.md\"}" },
            }],
          },
        }],
        usage: { prompt_tokens: 12, completion_tokens: 7 },
      }),
      text: async () => "",
    };
  };
  const provider = new OpenAIProvider({
    baseUrl: "https://example.test/v1/",
    apiKey: "sk-test",
    fetchImpl,
    maxRetries: 0,
  });
  const reply = await provider.chat({
    model: "m-1",
    messages: [{ role: "user", content: "hi" }],
    tools: [{ type: "function", function: { name: "read_file" } }],
    temperature: 0.3,
    maxTokens: 1234,
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://example.test/v1/chat/completions");
  assert.equal(calls[0].init.headers.Authorization, "Bearer sk-test");
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.model, "m-1");
  assert.equal(body.tool_choice, "auto");
  assert.equal(body.tools.length, 1);
  assert.equal(body.max_tokens, 1234);
  assert.equal(reply.toolCalls[0].name, "read_file");
  assert.equal(reply.usage.prompt, 12);
});

test("OpenAI 客户端把 4xx 变成可读错误", async () => {
  const provider = new OpenAIProvider({
    baseUrl: "https://example.test/v1",
    apiKey: "sk-test",
    maxRetries: 0,
    fetchImpl: async () => ({
      ok: false,
      status: 400,
      text: async () => "bad request",
      json: async () => ({}),
    }),
  });
  await assert.rejects(
    () => provider.chat({ model: "m", messages: [{ role: "user", content: "hi" }] }),
    /HTTP 400/,
  );
});

test("createProvider 缺密钥时拒绝启动", () => {
  assert.throws(
    () => createProvider("x", { type: "openai", base_url: "https://x", api_key_env: "MISSING_KEY" }, { env: {} }),
    /未配置密钥.*MISSING_KEY/,
  );
  const optional = createProvider(
    "local",
    { type: "openai", base_url: "http://127.0.0.1:1/v1", api_key_optional: true },
    { env: {} },
  );
  assert.ok(optional instanceof OpenAIProvider);
  assert.ok(createProvider("mock", { type: "mock" }) instanceof MockProvider);
});

test("mock judge 写出的引用可在正文中原样找到", async () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-judge-"));
  try {
    mkdirSync(join(bookDir, "正文"), { recursive: true });
    const chapter = "顶职名单今天换名字，换掉的正是他。他站在宣传栏前，手指发凉。";
    writeFileSync(join(bookDir, "正文", "第001章_榜上换了名.md"), chapter, "utf8");

    const provider = new MockProvider();
    const reply = await provider.chat({
      context: {
        node: "C1.N15",
        role: "judge",
        chapter: 1,
        bookDir,
        evidence: ["_pipeline/judgments_C1.N15.json"],
      },
    });
    assert.equal(reply.toolCalls.length, 1);
    const args = JSON.parse(reply.toolCalls[0].arguments);
    // 审查 A6：judgments_* 只有 judge 角色可写——与 runtime 按节点注入角色一致
    const tools = createToolExecutor({ bookDir, role: "judge" });
    assert.equal(tools.execute("write_file", args).ok, true);

    const doc = JSON.parse(readFileSync(join(bookDir, "_pipeline", "judgments_C1.N15.json"), "utf8"));
    const quote = doc.items[0].quote;
    assert.ok(quote.length >= 12);
    assert.ok(normalize(chapter).includes(quote));
    assert.equal(doc.verdict, "pass");
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
});

test("mock writer 按证据路径逐次写文件后收尾", async () => {
  const bookDir = mkdtempSync(join(tmpdir(), "wb-writer-"));
  try {
    const provider = new MockProvider();
    const context = {
      node: "C3.N09",
      role: "writer",
      chapter: 3,
      bookDir,
      evidence: ["大纲/细纲_第003章.md"],
    };
    const first = await provider.chat({ context });
    assert.equal(first.toolCalls[0].name, "write_file");
    const args = JSON.parse(first.toolCalls[0].arguments);
    assert.equal(createToolExecutor({ bookDir }).execute("write_file", args).ok, true);
    assert.ok(Buffer.byteLength(args.content, "utf8") >= 500);

    const second = await provider.chat({ context });
    assert.equal(second.toolCalls.length, 0);
    assert.match(second.content, /完成/);
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
});

test("provider 错误消息脱敏，原始响应体只进 console.error 本地日志", async () => {
  const logs = [];
  const originalError = console.error;
  console.error = (...args) => logs.push(args.map((arg) => (typeof arg === "string" ? arg : inspect(arg))).join(" "));
  try {
    const provider = new OpenAIProvider({
      baseUrl: "https://example.test/v1",
      apiKey: "sk-test",
      maxRetries: 0,
      fetchImpl: async () => ({
        ok: false,
        status: 401,
        text: async () => JSON.stringify({
          error: {
            message: "Incorrect API key provided: sk-abcdef1234567890. See https://api.example.com/docs?key=xyz987654321 for details.",
          },
        }),
        json: async () => ({}),
      }),
    });
    await assert.rejects(
      () => provider.chat({ model: "m", messages: [{ role: "user", content: "hi" }] }),
      (error) => {
        assert.match(error.message, /HTTP 401/);
        assert.ok(!error.message.includes("sk-abcdef1234567890"), "密钥不得进入错误消息");
        assert.ok(!error.message.includes("xyz987654321"), "URL query 令牌不得进入错误消息");
        assert.ok(error.message.length < 400, "错误消息必须有界");
        return true;
      },
    );
  } finally {
    console.error = originalError;
  }
  // 详细原始错误只进本地日志，供排障
  assert.ok(logs.join("\n").includes("sk-abcdef1234567890"), "原始响应体应进本地日志");
  assert.ok(logs.join("\n").includes("xyz987654321"), "原始 URL query 应进本地日志");
});
