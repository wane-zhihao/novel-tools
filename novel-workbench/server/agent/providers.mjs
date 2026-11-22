import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export class ProviderError extends Error {
  constructor(message, { status = null, retryable = false } = {}) {
    super(message);
    this.name = "ProviderError";
    this.status = status;
    this.retryable = retryable;
  }
}

function sleep(ms, signal) {
  return new Promise((accept, reject) => {
    if (signal?.aborted) {
      reject(new ProviderError("已取消", { retryable: false }));
      return;
    }
    const timer = setTimeout(accept, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new ProviderError("已取消", { retryable: false }));
    }, { once: true });
  });
}

const SECRET_PATTERNS = [
  [/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer ***"],
  [/\b(sk|pk|rk|ak)-[A-Za-z0-9_-]{6,}/g, "***"],
  [/(api[_-]?key|access[_-]?key|secret|token|password|authorization)["'\s]*[:=]["'\s]*[^"'\s,}]+/gi, "$1=***"],
  [/(https?:\/\/[^\s"']+?)\?[^\s"']*/g, "$1?<query-redacted>"],
];

/** 脱敏摘要：只保留状态码与安全摘要，进错误消息/事件流；原始错误体只进 console.error 本地日志 */
function sanitizeErrorText(text, limit = 200) {
  let out = String(text ?? "").replace(/\s+/g, " ").trim();
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    out = out.replace(pattern, replacement);
  }
  if (out.length > limit) out = `${out.slice(0, limit)}…`;
  return out;
}

function logProviderError(context, detail) {
  try {
    console.error(`[provider] ${context}`, detail);
  } catch {}
}

export class OpenAIProvider {
  constructor({ baseUrl, apiKey, timeoutMs = 300_000, fetchImpl = globalThis.fetch, maxRetries = 2 } = {}) {
    if (!baseUrl) throw new ProviderError("OpenAI provider 缺少 base_url");
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.apiKey = apiKey || "";
    this.timeoutMs = timeoutMs;
    this.fetchImpl = fetchImpl;
    this.maxRetries = maxRetries;
  }

  async chat({ model, messages, tools, temperature = 0.7, maxTokens = null, signal, timeoutMs } = {}) {
    const body = { model, messages, temperature, stream: false };
    if (Number.isInteger(maxTokens) && maxTokens > 0) body.max_tokens = maxTokens;
    if (Array.isArray(tools) && tools.length) {
      body.tools = tools;
      body.tool_choice = "auto";
    }
    const headers = { "Content-Type": "application/json" };
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;

    for (let attempt = 0; ; attempt += 1) {
      if (signal?.aborted) throw new ProviderError("已取消");
      const local = new AbortController();
      const timeout = setTimeout(() => local.abort(), timeoutMs ?? this.timeoutMs);
      const onAbort = () => local.abort();
      signal?.addEventListener("abort", onAbort, { once: true });
      let response;
      try {
        response = await this.fetchImpl(`${this.baseUrl}/chat/completions`, {
          method: "POST",
          headers,
          body: JSON.stringify(body),
          signal: local.signal,
        });
      } catch (error) {
        if (signal?.aborted) throw new ProviderError("已取消");
        if (local.signal.aborted) throw new ProviderError("模型请求超时", { retryable: false });
        if (attempt < this.maxRetries) {
          await sleep(500 * 2 ** attempt, signal);
          continue;
        }
        logProviderError("请求异常", {
          url: sanitizeErrorText(`${this.baseUrl}/chat/completions`, 120),
          message: error.message,
          cause: String(error.cause ?? ""),
        });
        throw new ProviderError(`模型请求失败：${sanitizeErrorText(error.message, 160)}`, { retryable: true });
      } finally {
        clearTimeout(timeout);
        signal?.removeEventListener("abort", onAbort);
      }

      if (!response.ok) {
        const text = await response.text().catch(() => "");
        const retryable = response.status === 429 || response.status >= 500;
        if (retryable && attempt < this.maxRetries) {
          await sleep(500 * 2 ** attempt, signal);
          continue;
        }
        // 原始响应体只进本地日志；抛给上层的消息脱敏，避免端点/账号信息
        // 经 node_retry 事件持久化并回喂模型
        logProviderError(`HTTP ${response.status}`, {
          url: sanitizeErrorText(`${this.baseUrl}/chat/completions`, 120),
          body: text.slice(0, 2000),
        });
        const summary = sanitizeErrorText(text);
        throw new ProviderError(
          summary ? `模型接口返回 HTTP ${response.status}：${summary}` : `模型接口返回 HTTP ${response.status}`,
          { status: response.status, retryable },
        );
      }

      const data = await response.json();
      const message = data?.choices?.[0]?.message;
      if (!message) throw new ProviderError("模型响应缺少 choices[0].message");
      const toolCalls = (message.tool_calls ?? []).map((call) => ({
        id: call.id ?? `call-${Math.random().toString(36).slice(2)}`,
        name: call.function?.name ?? "",
        arguments: call.function?.arguments ?? "{}",
      }));
      return {
        content: typeof message.content === "string" ? message.content : "",
        toolCalls,
        finishReason: typeof data?.choices?.[0]?.finish_reason === "string" ? data.choices[0].finish_reason : "",
        usage: {
          prompt: Number(data.usage?.prompt_tokens) || 0,
          completion: Number(data.usage?.completion_tokens) || 0,
        },
      };
    }
  }
}

const MOCK_FILLER = "这是一段用于验证运行时链路的占位文本，不代表真实写作质量，也不参与闸门校准。";

function mockOutline(chapter) {
  return [
    `# 第${String(chapter).padStart(3, "0")}章 细纲（mock）`,
    "",
    "- 核心事件：主角在厂办当众被要求交出账本，冲突升级。",
    "- 字数目标：2200字",
    "- 字数口径：visible_chars_v1",
    "- 单元ID/位置：U1-3 / 全章",
    "- 目标情绪：压抑后反击，读者获得一次明确的小爽点。",
    "- 主角目标/关键选择：保住父亲清誉，选择当众核对第一笔账。",
    "- 章首钩子：名单被换，主角当场被点名交代。",
    "- 爽点：用账本日期反将一军，逼对方改口。",
    "- 本章禁止提前释放：不得提前揭露孙有田作证。",
    "",
    "### 内容概括",
    "- 起因：主角被叫到厂办。",
    "- 发展：对方要求交账本。",
    "- 转折：主角发现日期矛盾。",
    "- 高潮：当众核对。",
    "- 结尾：对方暂退，期限仍在。",
    "",
    "### 情节安排",
    "一、厂办点名；二、账本交锋；三、日期反证；四、限期未解。",
    "",
    "### 人物关系和出场顺序",
    "陈砚、赵德海、陈磊、王秀兰。",
    "",
    "### 情节细化",
    "| # | 情节点 | 功能标签 | 执行边界（禁＋放） |",
    "|---|---|---|---|",
    "| 1 | 点名交账 | 冲突 | 禁：解释背景；放：当场对话 |",
    "| 2 | 日期反证 | 爽点 | 禁：提前放人证；放：账本细节 |",
    "",
    MOCK_FILLER.repeat(6),
  ].join("\n");
}

function mockChapter(chapter) {
  const no = String(chapter ?? 1).padStart(3, "0");
  const paragraph = "陈砚站在厂办门口，手指发凉。他数着墙上的名单，一笔一划都像刀。";
  return [
    `# 第${no}章 mock 样章`,
    "",
    paragraph.repeat(18),
    "",
    paragraph.repeat(18),
    "",
    paragraph.repeat(18),
    "",
    paragraph.repeat(12),
  ].join("\n");
}

function mockMarkdown(path, context) {
  const normalized = String(path).replaceAll("\\", "/");
  if (normalized.includes("大纲/")) return mockOutline(context.chapter ?? 1);
  if (normalized.includes("正文/")) return mockChapter(context.chapter ?? 1);
  return [`# ${normalized.split("/").at(-1)}`, "", MOCK_FILLER.repeat(10)].join("\n");
}

function normalize(text) {
  return String(text).replace(/[^\u4e00-\u9fa5a-zA-Z0-9]/g, "");
}

function firstFile(dir, predicate = () => true) {
  if (!existsSync(dir)) return null;
  const name = readdirSync(dir).filter(predicate).sort()[0];
  return name ? join(dir, name) : null;
}

function judgeSourceFile(bookDir, node, chapter) {
  if (node === "N04") {
    const file = join(bookDir, "大纲", "选题报告.md");
    return existsSync(file) ? file : null;
  }
  if (node === "N06") return firstFile(join(bookDir, "设定"), (name) => name.endsWith(".md"));
  if (node === "N08") {
    const file = join(bookDir, "大纲", "卷纲_第一卷.md");
    return existsSync(file) ? file : null;
  }
  const bodyDir = join(bookDir, "正文");
  const prefix = `第${String(chapter ?? 1).padStart(3, "0")}章`;
  return firstFile(bodyDir, (name) => name.startsWith(prefix) && name.endsWith(".md"))
    ?? firstFile(bodyDir, (name) => name.endsWith(".md"));
}

function mockQuote(bookDir, node, chapter) {
  const file = judgeSourceFile(bookDir, node, chapter);
  if (!file) return { quote: "", source: null };
  const source = file.slice(bookDir.length + 1).split("\\").join("/");
  const normalized = normalize(readFileSync(file, "utf8"));
  if (normalized.length < 12) return { quote: "", source };
  const start = Math.min(Math.floor(normalized.length * 0.3), normalized.length - 16);
  return { quote: normalized.slice(start, start + 16), source };
}

/** 无密钥回归用的固定行为 provider：writer 写证据文件，judge 生成可校验引用。 */
export class MockProvider {
  constructor() {
    this.state = new Map();
  }

  async chat({ context = {} } = {}) {
    const { node = "mock", role = "writer", evidence = [] } = context;
    const key = `${node}:${role}`;
    const state = this.state.get(key) ?? { written: new Set(), judgeDone: false, counter: 0 };
    this.state.set(key, state);

    if (role === "judge") {
      if (state.judgeDone) return { content: "mock judge 完成", toolCalls: [], usage: { prompt: 0, completion: 0 } };
      state.judgeDone = true;
      const { quote, source } = mockQuote(context.bookDir, node, context.chapter);
      const target = evidence[0] ?? `_pipeline/judgments_${node}.json`;
      const judgment = {
        verdict: "pass",
        summary: "mock judge：仅验证运行时链路，不代表真实评审结论。",
        items: [{
          criterion: "mock criterion",
          level: "A",
          quote,
          source: source ?? "",
          verdict: "pass",
          fix: "",
        }],
      };
      return {
        content: "",
        toolCalls: [{
          id: `mock-${key}-${(state.counter += 1)}`,
          name: "write_file",
          arguments: JSON.stringify({ path: target, content: JSON.stringify(judgment, null, 2) }),
        }],
        usage: { prompt: 0, completion: 0 },
      };
    }

    const pending = evidence.filter((path) => !state.written.has(path));
    if (!pending.length) return { content: "mock writer 完成", toolCalls: [], usage: { prompt: 0, completion: 0 } };
    const pattern = pending[0];
    const target = String(pattern).includes("*") ? String(pattern).replace("*", "mock") : pattern;
    state.written.add(pattern);
    const toolCalls = [{
      id: `mock-${key}-${(state.counter += 1)}`,
      name: "write_file",
      arguments: JSON.stringify({ path: target, content: mockMarkdown(target, context) }),
    }];
    if (String(node).endsWith(".N12") && context.bookDir && !context.roleConfig?.omit_tracking) {
      const match = /^C(\d+)\.N12$/.exec(String(node));
      const ch = context.chapter ?? (match ? Number(match[1]) : 1);
      const chNo = String(ch).padStart(3, "0");
      const txnRel = `.story/work/第${chNo}章/tracking.json`;
      const txnAbs = join(context.bookDir, txnRel);
      if (existsSync(txnAbs)) {
        try {
          const raw = JSON.parse(readFileSync(txnAbs, "utf8"));
          raw.delta = {
            ...(raw.delta ?? {}),
            result: "主角在厂办完成交接并发现账本矛盾。",
            character_changes: [{ name: "陈砚", change: "完成交接" }],
            timeline_events: [{
              id: `E${chNo}`,
              story_time: "1987年某日",
              objective_fact: "陈砚在厂办完成交接",
              reader_knowledge: "知晓交接",
              reveal_status: "已揭示",
              reveal_chapter: ch,
              characters: ["陈砚"],
            }],
          };
          raw.character_snapshots = { "陈砚": "主角" };
          raw.context = {
            ...(raw.context ?? {}),
            position: { story_time: "1987年某日", scene: "厂办" },
          };
          toolCalls.push({
            id: `mock-${key}-${(state.counter += 1)}`,
            name: "write_file",
            arguments: JSON.stringify({ path: txnRel, content: JSON.stringify(raw, null, 2) }),
          });
        } catch {}
      }
    }
    return {
      content: "",
      toolCalls,
      usage: { prompt: 0, completion: 0 },
    };
  }
}

export function createProvider(name, config, { env = process.env, fetchImpl = globalThis.fetch, secrets = null } = {}) {
  if (!config) throw new ProviderError(`provider ${name} 未定义`);
  if (config.type === "mock") return new MockProvider();
  if (config.type === "openai") {
    const stored = config.api_key_env && secrets ? secrets(config.api_key_env) : null;
    if (stored?.error) throw new ProviderError(`provider ${name} 密钥读取失败：${stored.error}`);
    const apiKey = stored?.value ?? (config.api_key_env ? env[config.api_key_env] ?? "" : "");
    if (!apiKey && config.api_key_optional !== true) {
      throw new ProviderError(`provider ${name} 未配置密钥（环境变量 ${config.api_key_env} 或 UI 密钥存储）`);
    }
    return new OpenAIProvider({ baseUrl: config.base_url, apiKey, fetchImpl });
  }
  throw new ProviderError(`provider ${name} 类型不支持：${config.type}`);
}
