#!/usr/bin/env node
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { openDatabase } from "./db.mjs";
import { rebuild, syncAll, syncBook } from "./sync.mjs";
import { loadAgentConfig } from "./agent-config.mjs";
import { defaultDataDir } from "./paths.mjs";
import { effectiveSettings } from "./settings.mjs";
import { AgentRuntime } from "./agent/runtime.mjs";

function parseArgs(argv) {
  const options = {
    command: argv[0] ?? "help",
    roots: [],
    book: null,
    json: false,
    force: false,
    mock: false,
    resume: false,
    maxSteps: Infinity,
  };
  for (let index = 1; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--root") options.roots.push(argv[++index]);
    else if (value === "--book") options.book = argv[++index];
    else if (value === "--json") options.json = true;
    else if (value === "--force") options.force = true;
    else if (value === "--mock") options.mock = true;
    else if (value === "--resume") options.resume = true;
    else if (value === "--max-steps") {
      const parsed = Number(argv[++index]);
      if (!Number.isInteger(parsed) || parsed < 1) throw new Error("--max-steps 必须是正整数");
      options.maxSteps = parsed;
    } else if (value === "--help" || value === "-h") options.command = "help";
    else throw new Error(`未知参数：${value}`);
  }
  return options;
}

function bookRows(db) {
  return db.prepare(`
    SELECT b.*,
      (SELECT COUNT(*) FROM chapters c WHERE c.book_id = b.id) AS chapter_count,
      (SELECT COUNT(*) FROM findings f WHERE f.book_id = b.id AND f.active = 1) AS active_findings,
      (SELECT COUNT(*) FROM findings f WHERE f.book_id = b.id AND f.active = 1 AND f.blocking = 1) AS blocking_findings
    FROM books b
    ORDER BY b.path
  `).all();
}

function printBooks(db, dataDir) {
  const rows = bookRows(db);
  console.log(`数据目录：${dataDir}`);
  console.log(`书：${rows.length}`);
  for (const row of rows) {
    console.log(
      `- ${row.title ?? "(无书名)"}  ${row.path}\n`
      + `  游标 ${row.cursor ?? "-"} ｜ 封板 ${row.chapters_committed} 章 / ${row.words_total} 字`
      + ` ｜ 章节 ${row.chapter_count} ｜ 现行问题 ${row.active_findings}（阻断 ${row.blocking_findings}）`
      + (row.blocked_reason ? `\n  受阻：${row.blocked_reason}` : ""),
    );
  }
}

function printConfig(result) {
  console.log(`默认配置：${result.basePath}`);
  console.log(`本书覆盖：${result.overridePath ?? "无"}`);
  console.log("Provider：");
  for (const provider of result.providers) {
    const state = provider.optional
      ? "密钥可选（本地）"
      : provider.configured
        ? "已配置"
        : `未配置：${provider.env ?? "缺少密钥"}`;
    console.log(`- ${provider.name} (${provider.type}) ${state}`);
  }
  console.log("角色：");
  for (const [role, settings] of Object.entries(result.config.roles ?? {})) {
    console.log(`- ${role} → ${settings.provider}/${settings.model}  temp=${settings.temperature}  timeout=${settings.timeout_s}s`);
  }
  for (const warning of result.warnings) console.log(`警告：${warning}`);
  for (const error of result.errors) console.log(`错误：${error}`);
}

function printEvent(event) {
  if (event.type === "node_started") {
    console.log(`\n[${event.node}] ${event.role} · ${event.provider}/${event.model}（第 ${event.attempt} 次）`);
  } else if (event.type === "tool_call") {
    console.log(`  · ${event.tool} ${event.path ?? ""}${event.ok ? "" : "  [失败]"}`);
  } else if (event.type === "node_done") {
    console.log(`✓ ${event.node}（${event.attempt} 次，工具调用 ${event.tool_steps} 步）`);
  } else if (event.type === "node_retry") {
    console.log(`… ${event.node} 未通过：${event.reason.slice(0, 160)}`);
  } else if (event.type === "routed") {
    console.log(`↻ ${event.node} 判定 block，已路由到 ${event.routed_to}`);
  }
}

const STATUS_LABELS = {
  done: "已完成",
  blocked: "受阻断",
  failed: "失败",
  stopped: "已停止",
  max_steps: "达到步数上限",
  budget_exhausted: "预算耗尽",
};

function printRunResult(result) {
  console.log(`\n状态：${STATUS_LABELS[result.status] ?? result.status}`);
  if (result.error) console.log(`原因：${result.error}`);
  if (result.book) {
    console.log(`书：${result.book.title ?? ""} ｜ 游标 ${result.book.cursor ?? "-"} ｜ 封板 ${result.book.chapters_committed} 章 / ${result.book.words_total} 字`);
    if (result.book.blocked_reason) console.log(`阻断：${result.book.blocked_reason}`);
  }
  console.log(`本轮节点 ${result.steps.length} 个，LLM 轮次 ${result.llm_rounds}，tokens ${result.usage.prompt}/${result.usage.completion}`);
}

async function handleRun(options) {
  if (!options.book) {
    console.error("run 需要 --book <书目录>");
    return 2;
  }
  const bookDir = resolve(options.book);
  if (!existsSync(resolve(bookDir, "_pipeline", "state.json"))) {
    console.error(`不是工作台书目录（缺少 _pipeline/state.json）：${bookDir}`);
    return 2;
  }
  const loaded = loadAgentConfig({ bookDir, dataDir: defaultDataDir() });
  for (const warning of loaded.warnings) console.error(`警告：${warning}`);
  if (loaded.errors.length) {
    for (const error of loaded.errors) console.error(`错误：${error}`);
    return 2;
  }
  const config = structuredClone(loaded.config);
  if (options.mock) {
    for (const role of Object.keys(config.roles ?? {})) {
      config.roles[role].provider = "mock";
      config.roles[role].model = `mock-${role}`;
    }
    console.error("警告：--mock 使用占位 provider，不会调用真实模型，产物不可用于投稿。");
  }

  const controller = new AbortController();
  const onSigint = () => {
    console.error("\n收到停止信号，等待当前步骤收尾…");
    controller.abort();
  };
  process.once("SIGINT", onSigint);

  let result;
  try {
    const runtime = new AgentRuntime({
      bookDir,
      config,
      dataDir: defaultDataDir(),
      signal: controller.signal,
      maxSteps: options.maxSteps,
      onEvent: options.json ? () => {} : printEvent,
    });
    if (options.resume) {
      const resumed = await runtime.resume();
      if (resumed.code !== 0) {
        console.error(`恢复失败：${(resumed.stderr || resumed.stdout).slice(-500)}`);
        return 2;
      }
    }
    result = await runtime.run();
  } catch (error) {
    console.error(`运行时启动失败：${error.message}`);
    return 2;
  } finally {
    process.removeListener("SIGINT", onSigint);
  }

  try {
    const db = openDatabase({ dataDir: defaultDataDir() });
    try {
      syncBook(db, bookDir, { force: true });
    } finally {
      db.close();
    }
  } catch (error) {
    console.error(`数据库同步失败（不影响写作结果）：${error.message}`);
  }

  if (options.json) console.log(JSON.stringify(result, null, 2));
  else printRunResult(result);

  if (result.status === "done" || result.status === "max_steps") return 0;
  if (result.status === "stopped") return 130;
  return 1;
}

function help() {
  console.log(`novel-workbench CLI

用法：
  node novel-workbench/server/cli.mjs sync    [--root <dir> ...] [--force] [--json]
  node novel-workbench/server/cli.mjs rebuild [--root <dir> ...] [--json]
  node novel-workbench/server/cli.mjs status  [--json]
  node novel-workbench/server/cli.mjs config  [--book <dir>] [--json]
  node novel-workbench/server/cli.mjs run     --book <dir> [--max-steps N] [--mock] [--resume] [--json]

环境变量：
  WORKBENCH_DATA_DIR     数据目录（默认 %LOCALAPPDATA%\\NovelWorkbench）
  WORKBENCH_BOOKS_ROOTS  书库根目录，多个用 ; 或 , 分隔；缺省读 config.json 的 BOOKS_ROOT
  WORKBENCH_PYTHON       覆盖 config.json 里的 Python 路径

run 使用 agents.default.json + 书内 _pipeline/agents.json 的配置；
密钥只从环境变量读取，默认 mock provider 不会产生调用费用。
`);
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    return 2;
  }
  if (options.command === "help") {
    help();
    return 0;
  }
  if (options.command === "run") return handleRun(options);

  const dataDir = defaultDataDir();
  const roots = options.roots.length ? options.roots : effectiveSettings({ dataDir }).books_roots;

  if (options.command === "config") {
    const result = loadAgentConfig({ bookDir: options.book, dataDir });
    if (options.json) console.log(JSON.stringify(result, null, 2));
    else printConfig(result);
    return result.errors.length ? 1 : 0;
  }

  if (!roots.length) {
    console.error("没有可用的书库根目录：设置 WORKBENCH_BOOKS_ROOTS 或用 --root 指定");
    return 2;
  }
  const db = openDatabase({ dataDir });
  try {
    if (options.command === "sync" || options.command === "rebuild") {
      const summary = options.command === "rebuild"
        ? rebuild(db, roots)
        : syncAll(db, roots, { force: options.force });
      if (options.json) console.log(JSON.stringify(summary, null, 2));
      else {
        console.log(`根目录：${summary.roots.join(" ｜ ")}`);
        console.log(`发现 ${summary.discovered} 本，入库 ${summary.synced} 本，跳过 ${summary.skipped} 本`);
        for (const error of summary.errors) console.log(`失败：${error.path} — ${error.message}`);
      }
      return summary.errors.length ? 1 : 0;
    }
    if (options.command === "status") {
      if (options.json) {
        console.log(JSON.stringify({ dataDir, books: bookRows(db) }, null, 2));
      } else {
        printBooks(db, dataDir);
      }
      return 0;
    }
    console.error(`未知命令：${options.command}`);
    help();
    return 2;
  } finally {
    db.close();
  }
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error) => {
    console.error(error.stack ?? error.message);
    process.exitCode = 1;
  });
