import { execFile, execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { createProvider, ProviderError } from "./providers.mjs";
import { createToolExecutor } from "./tools.mjs";
import { defaultDataDir } from "../paths.mjs";
import { ensurePipelineConfig } from "../settings.mjs";
import { secretResolver } from "../secrets.mjs";

const DEFAULT_MAX_TOOL_STEPS = 48;

export class RuntimeError extends Error {
  constructor(message) {
    super(message);
    this.name = "RuntimeError";
  }
}

class BudgetExceededError extends RuntimeError {
  constructor(message) {
    super(message);
    this.name = "BudgetExceededError";
  }
}

function sanitize(text) {
  return String(text ?? "")
    .replace(/(--token\s+)\S+/gi, "$1***")
    .replace(/done_cmd[^\n]*/gi, "done_cmd=***");
}

function runCommand(command, args, { cwd, timeoutMs, signal, env = {} } = {}) {
  return new Promise((accept) => {
    const child = execFile(
      command,
      args,
      {
        cwd,
        timeout: timeoutMs,
        maxBuffer: 8 * 1024 * 1024,
        encoding: "utf8",
        windowsHide: true,
        signal,
        env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1", ...env },
      },
      (error, stdout, stderr) => {
        const timedOut = Boolean(error?.killed);
        const code = error
          ? typeof error.code === "number"
            ? error.code
            : timedOut
              ? 124
              : 1
          : 0;
        // 审查 B9：execFile 的 signal/timeout 只终止直接子进程；autopilot 再
        // spawn 的 storyctl/gate_runner 在 Windows 上会成为孤儿继续写书目录。
        // 这里补杀整个进程树。
        if ((timedOut || signal?.aborted) && process.platform === "win32" && child.pid) {
          try {
            execFileSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
          } catch {}
        }
        accept({ code, stdout: stdout ?? "", stderr: stderr ?? "", timedOut });
      },
    );
    return child;
  });
}

function parseLastJson(text) {
  const lines = String(text ?? "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (!lines[index].startsWith("{")) continue;
    try {
      return JSON.parse(lines[index]);
    } catch {}
  }
  return null;
}

export function resolveRuntimePaths({ env = process.env, dataDir = defaultDataDir() } = {}) {
  const settings = ensurePipelineConfig({ dataDir, env });
  const blocking = settings.errors.filter((error) => !error.startsWith("书库根目录"));
  if (blocking.length) throw new RuntimeError(blocking.join("；"));
  return {
    python: settings.python,
    autopilot: settings.autopilot,
    configPath: settings.pipeline_config_path,
    readRoots: settings.read_roots,
  };
}

function readStepToken(bookDir) {
  const path = join(bookDir, "_pipeline", ".step_token");
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function insideRoot(root, target) {
  const rel = relative(root, target);
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel));
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function expandEvidence(bookDir, pattern) {
  const normalized = String(pattern).replaceAll("\\", "/");
  const slash = normalized.lastIndexOf("/");
  const dirRel = slash >= 0 ? normalized.slice(0, slash) : ".";
  const namePattern = slash >= 0 ? normalized.slice(slash + 1) : normalized;
  const dir = resolve(bookDir, dirRel);
  if (!insideRoot(bookDir, dir) || !existsSync(dir)) return [];
  const regex = new RegExp(`^${namePattern.split("*").map(escapeRegex).join(".*")}$`);
  try {
    return readdirSync(dir)
      .filter((name) => regex.test(name))
      .map((name) => (dirRel === "." ? name : `${dirRel}/${name}`));
  } catch {
    return [];
  }
}

function systemPrompt(role) {
  const lines = [
    "你是「签约工作台」的执行智能体，只能通过工具读写当前书目录内的文件。",
    "规则：",
    "1. 严格按用户消息里的节点指令执行，不改变范围、不跳过要求。",
    "2. 参考文件用 read_file 读取；所有产出必须用 write_file 写到指令指定路径，写完整内容而不是补丁。",
    "3. 禁止改写 _pipeline/state.json、_pipeline/.step_token、_pipeline/workbench.json、_pipeline/run.json、_pipeline/agents.json、_pipeline/submissions.json、_pipeline/snapshots/，也不可读取它们。",
    "4. 完成后只回一句 ≤100 字回执，不要复述正文。",
  ];
  if (role === "judge") {
    lines.push("5. 裁决写入 JSON 文件；items[].quote 必须是正文连续 12–30 字原文，严禁省略号跳跃。");
  }
  return lines.join("\n");
}

export class AgentRuntime {
  constructor({
    bookDir,
    config,
    env = process.env,
    signal = null,
    onEvent = () => {},
    maxSteps = Infinity,
    dataDir = defaultDataDir(),
    paths = resolveRuntimePaths({ env, dataDir }),
    readRoots = paths.readRoots ?? [],
    onFileWrite = null,
    providerFactory = createProvider,
  } = {}) {
    if (!bookDir) throw new RuntimeError("缺少 bookDir");
    if (!config?.roles?.writer || !config?.roles?.judge) throw new RuntimeError("Agent 配置缺少 writer/judge 角色");
    this.bookDir = resolve(bookDir);
    this.config = config;
    this.env = env;
    this.signal = signal;
    this.onEvent = onEvent;
    this.maxSteps = maxSteps;
    this.paths = paths;
    this.providerFactory = providerFactory;
    this.onFileWrite = onFileWrite;
    this.currentNode = null;
    this.tools = createToolExecutor({
      bookDir: this.bookDir,
      readRoots,
      onFileWrite: (change) => this.onFileWrite?.({ ...change, node: this.currentNode }),
    });
    this.secrets = secretResolver({ dataDir, env });
    this.budgets = { llm_rounds: Infinity, node_timeout_s: 900, ...(config.budgets ?? {}) };
    this.steps = 0;
    this.llmRounds = 0;
    this.usage = { prompt: 0, completion: 0 };
    this.results = [];
  }

  emit(event) {
    try {
      this.onEvent(event);
    } catch {}
  }

  aborted() {
    return Boolean(this.signal?.aborted);
  }

  assertRunning() {
    if (this.aborted()) throw new RuntimeError("已停止");
  }

  nodeSignal(roleConfig) {
    const seconds = Math.min(roleConfig.timeout_s, this.budgets.node_timeout_s ?? roleConfig.timeout_s);
    const timeout = AbortSignal.timeout(seconds * 1000);
    return this.signal ? AbortSignal.any([this.signal, timeout]) : timeout;
  }

  async resume() {
    const result = await runCommand(
      this.paths.python,
      [this.paths.autopilot, "resume", "--book", this.bookDir, "--force"],
      {
        cwd: this.bookDir,
        timeoutMs: 60_000,
        signal: this.signal ?? undefined,
        env: { WORKBENCH_CONFIG: this.paths.configPath },
      },
    );
    return { ...result, payload: parseLastJson(result.stdout) };
  }

  async callNext() {
    const result = await runCommand(this.paths.python, [this.paths.autopilot, "next", "--book", this.bookDir], {
      cwd: this.bookDir,
      timeoutMs: 600_000,
      signal: this.signal ?? undefined,
      env: { WORKBENCH_CONFIG: this.paths.configPath },
    });
    return { ...result, payload: parseLastJson(result.stdout) };
  }

  async callDone(node, evidence) {
    const held = readStepToken(this.bookDir);
    if (!held?.token || held.node !== node) {
      return { code: 2, payload: { error: "token_missing", message: "找不到与当前节点匹配的 step_token" }, stderr: "" };
    }
    // 审查 C1：令牌经 WB_STEP_TOKEN 环境变量传递，不再走 argv——本机进程
    // 管理器（任务管理器/Process Explorer）能读到命令行，env 至少不进列表。
    const args = [
      this.paths.autopilot, "done", "--book", this.bookDir,
      "--node", node,
    ];
    for (const path of evidence) args.push("--evidence", path);
    const result = await runCommand(this.paths.python, args, {
      cwd: this.bookDir,
      timeoutMs: 300_000,
      signal: this.signal ?? undefined,
      env: { WORKBENCH_CONFIG: this.paths.configPath, WB_STEP_TOKEN: held.token },
    });
    return { ...result, payload: parseLastJson(result.stdout) };
  }

  missingEvidence(evidence) {
    const missing = [];
    for (const rel of evidence ?? []) {
      if (String(rel).includes("*")) {
        const hits = expandEvidence(this.bookDir, rel);
        if (hits.length !== 1) {
          missing.push(hits.length ? `${rel}（匹配 ${hits.length} 个，应唯一）` : rel);
        }
        continue;
      }
      const target = resolve(this.bookDir, rel);
      if (!insideRoot(this.bookDir, target) || !existsSync(target) || !statSync(target).isFile()) {
        missing.push(rel);
      }
    }
    return missing;
  }

  async runAgent(instruction, roleConfig, feedback, signal) {
    const provider = this.providerFactory(
      roleConfig.provider,
      this.config.providers?.[roleConfig.provider],
      { env: this.env, secrets: this.secrets },
    );
    const messages = [
      { role: "system", content: systemPrompt(instruction.subagent_role) },
      {
        role: "user",
        content: feedback
          ? `${instruction.subagent_prompt}\n\n【上一轮失败反馈】\n${feedback}`
          : instruction.subagent_prompt,
      },
    ];
    const usage = { prompt: 0, completion: 0 };
    const context = {
      node: instruction.node,
      role: instruction.subagent_role,
      chapter: instruction.chapter,
      bookDir: this.bookDir,
      evidence: instruction.evidence ?? [],
    };
    const maxToolSteps = this.budgets.max_tool_steps ?? DEFAULT_MAX_TOOL_STEPS;
    let toolSteps = 0;
    for (let step = 0; step < maxToolSteps; step += 1) {
      this.assertRunning();
      // 审查 B8：llm_rounds 按 chat 次数计——一次 attempt 内工具循环最多 48 次
      // chat，按 attempt 计会让预算形同虚设。
      if (this.llmRounds >= this.budgets.llm_rounds) {
        throw new BudgetExceededError(`LLM 轮次超过预算 ${this.budgets.llm_rounds}`);
      }
      this.llmRounds += 1;
      const reply = await provider.chat({
        model: roleConfig.model,
        messages,
        tools: this.tools.definitions,
        temperature: roleConfig.temperature,
        maxTokens: roleConfig.max_tokens,
        signal,
        context,
      });
      usage.prompt += reply.usage.prompt;
      usage.completion += reply.usage.completion;
      const budget = this.budgets.max_completion_tokens;
      if (budget && this.usage.completion + usage.completion > budget) {
        throw new BudgetExceededError(`completion token 超过预算 ${budget}`);
      }
      if (!reply.toolCalls.length) {
        // 空回复不停摆：模型（实测 deepseek-flash）会在长任务中途返回无工具、
        // 无文字的轮次，直接结束会白烧一次 attempt。推一把让它继续干活。
        if (!String(reply.content ?? "").trim()) {
          this.emit({ type: "node_nudge", node: instruction.node, step });
          // 注意：DeepSeek 严格校验 assistant 消息——content 为 null/空且无
          // tool_calls 会 400（content or tool_calls must be set）。给个非空
          // 占位内容，nudge 本体放 user 轮。
          messages.push({ role: "assistant", content: "（继续）" });
          messages.push({
            role: "user",
            content: "继续执行：按指令用 write_file 把产物写到指定路径；写完用 ≤100 字回执。不要再浏览目录。",
          });
          continue;
        }
        return { text: reply.content, usage, toolSteps };
      }
      toolSteps += reply.toolCalls.length;
      messages.push({
        role: "assistant",
        content: reply.content || null,
        tool_calls: reply.toolCalls.map((call) => ({
          id: call.id,
          type: "function",
          function: { name: call.name, arguments: call.arguments },
        })),
      });
      for (const call of reply.toolCalls) {
        let args = {};
        try {
          args = JSON.parse(call.arguments || "{}");
        } catch {}
        const result = this.tools.execute(call.name, args);
        this.emit({
          type: "tool_call",
          node: instruction.node,
          tool: call.name,
          path: typeof args.path === "string" ? args.path : null,
          ok: result.ok,
        });
        messages.push({ role: "tool", tool_call_id: call.id, content: result.content });
      }
    }
    throw new RuntimeError(`工具循环超过 ${maxToolSteps} 步`);
  }

  async executeNode(instruction) {
    const role = instruction.subagent_role ?? "writer";
    const roleConfig = this.config.roles?.[role];
    if (!roleConfig) return { status: "failed", error: `配置里没有角色 ${role}` };
    const evidence = Array.isArray(instruction.resolved_evidence) && instruction.resolved_evidence.length
      ? instruction.resolved_evidence
      : (Array.isArray(instruction.evidence) ? instruction.evidence : []);
    const maxAttempts = roleConfig.max_attempts ?? 2;
    this.currentNode = instruction.node;
    // 审查 A6：工具面按角色收放——只有 judge 可写 judgments_*，writer/scout 不可。
    this.tools.setRole(role);
    let feedback = "";

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      this.assertRunning();
      if (this.llmRounds >= this.budgets.llm_rounds) return { status: "budget" };
      this.emit({
        type: "node_started",
        node: instruction.node,
        role,
        provider: roleConfig.provider,
        model: roleConfig.model,
        attempt,
      });

      const nodeSignal = this.nodeSignal(roleConfig);
      let agent;
      try {
        agent = await this.runAgent(instruction, roleConfig, feedback, nodeSignal);
      } catch (error) {
        if (error instanceof BudgetExceededError) throw error;
        if (error instanceof RuntimeError) throw error;
        if (this.aborted()) throw new RuntimeError("已停止");
        if (nodeSignal.aborted) {
          return { status: "failed", error: `节点超时（${roleConfig.timeout_s}s）`, attempt };
        }
        if (error instanceof ProviderError && !error.retryable && error.message.includes("超时")) {
          return { status: "failed", error: error.message, attempt };
        }
        feedback = `模型调用失败：${error.message}`;
        this.emit({ type: "node_retry", node: instruction.node, reason: feedback });
        continue;
      }
      this.usage.prompt += agent.usage.prompt;
      this.usage.completion += agent.usage.completion;

      const missing = this.missingEvidence(evidence);
      if (missing.length) {
        feedback = `以下证据文件不存在或为空：${missing.join("、")}。请按指令写出完整文件后再回执。`;
        this.emit({ type: "node_retry", node: instruction.node, reason: feedback });
        continue;
      }

      const done = await this.callDone(instruction.node, evidence);
      this.assertRunning();
      const payload = done.payload ?? {};
      if (done.code === 0) {
        this.emit({
          type: "node_done",
          node: instruction.node,
          attempt,
          tool_steps: agent.toolSteps,
        });
        return { status: "done", attempt, usage: agent.usage, toolSteps: agent.toolSteps };
      }
      if (done.code === 1 && payload.blocked && payload.routed_to) {
        this.emit({ type: "routed", node: instruction.node, routed_to: payload.routed_to, reason: payload.reason ?? "" });
        return { status: "routed", routedTo: payload.routed_to, attempt, usage: agent.usage };
      }
      if (done.code === 1 && payload.error === "verify_blocked") {
        return { status: "blocked", error: payload.reason ?? "终审未通过", attempt, usage: agent.usage };
      }
      if (done.code === 2 && payload.error === "quote_check_failed") {
        feedback = `引用校验失败，以下条目的 quote 不是正文连续原文：${JSON.stringify(payload.items ?? []).slice(0, 800)}`;
        this.emit({ type: "node_retry", node: instruction.node, reason: "引用校验失败，回喂重写" });
        continue;
      }
      return {
        status: "failed",
        error: payload.error
          ? `${payload.error}: ${payload.message ?? payload.reason ?? ""}`.trim()
          : `done 退出码 ${done.code}：${(done.stderr || done.stdout || "").slice(-400)}`,
        attempt,
        usage: agent.usage,
      };
    }
    return { status: "failed", error: `${instruction.node} 连续 ${maxAttempts} 次未通过` };
  }

  async run() {
    this.emit({ type: "run_started", book: this.bookDir });
    try {
      while (true) {
        this.assertRunning();
        if (this.steps >= this.maxSteps) return this.finish("max_steps");
        if (this.llmRounds >= this.budgets.llm_rounds) return this.finish("budget_exhausted");

        const next = await this.callNext();
        this.assertRunning();
        const payload = next.payload;
        if (!payload) {
          return this.finish("failed", `autopilot 输出无法解析：${sanitize(next.stderr || "无 stderr 输出").slice(-400)}`);
        }
        if (payload.done) return this.finish("done");
        if (payload.error) {
          if (payload.error === "blocked") return this.finish("blocked", payload.reason ?? "流水线受阻");
          if (payload.error === "node_running") {
            return this.finish("needs_resume", payload.hint ?? "上次执行被中断，需先恢复");
          }
          return this.finish("failed", `${payload.error}: ${payload.hint ?? payload.output ?? ""}`.trim());
        }
        if (payload.kind !== "llm" && payload.kind !== "verify") {
          return this.finish("failed", `非预期节点类型：${payload.kind}`);
        }

        const outcome = await this.executeNode(payload);
        if (outcome.status === "failed") return this.finish("failed", outcome.error);
        if (outcome.status === "blocked") return this.finish("blocked", outcome.error);
        if (outcome.status === "budget") return this.finish("budget_exhausted");
        this.steps += 1;
        this.results.push({
          node: payload.node,
          role: payload.subagent_role,
          model: payload.subagent_model,
          attempts: outcome.attempt,
          usage: outcome.usage,
        });
        if (outcome.status === "routed") continue;
      }
    } catch (error) {
      if (error instanceof BudgetExceededError) return this.finish("budget_exhausted", error.message);
      if (error instanceof RuntimeError && error.message === "已停止") return this.finish("stopped");
      return this.finish("failed", error.message);
    }
  }

  finish(status, error = null) {
    let book = null;
    try {
      const state = JSON.parse(readFileSync(join(this.bookDir, "_pipeline", "state.json"), "utf8"));
      book = {
        title: state.book ?? null,
        cursor: state.cursor ?? null,
        chapters_committed: state.chapters_committed ?? 0,
        words_total: state.words_total ?? 0,
        blocked_reason: state.blocked_reason ?? null,
      };
    } catch {}
    const result = {
      status,
      error: error ? sanitize(error) : null,
      book,
      steps: this.results,
      llm_rounds: this.llmRounds,
      usage: this.usage,
    };
    this.emit({ type: "run_finished", ...result });
    return result;
  }
}
