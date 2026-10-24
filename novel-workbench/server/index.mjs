#!/usr/bin/env node
import { spawn } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { openDatabase } from "./db.mjs";
import { syncAll, syncBook } from "./sync.mjs";
import { createApi, createHandler } from "./api.mjs";
import { RunManager, markInterruptedRuns } from "./run-manager.mjs";
import { defaultDataDir } from "./paths.mjs";
import { ensurePipelineConfig } from "./settings.mjs";
import { isLoopbackHost } from "./http.mjs";
import { workbenchVersion } from "./version.mjs";

const VERSION = workbenchVersion();

export async function startWorkbench({
  port = 43120,
  host = "127.0.0.1",
  roots = null,
  dataDir = defaultDataDir(),
  allowNetwork = false,
  networkToken = process.env.WORKBENCH_NETWORK_TOKEN || null,
} = {}) {
  if (!allowNetwork && !isLoopbackHost(host)) {
    throw new Error("绑定非回环地址需要显式 --allow-network");
  }
  if (allowNetwork && !networkToken) {
    throw new Error("绑定非回环地址必须设置 WORKBENCH_NETWORK_TOKEN");
  }
  const rootOverride = roots?.length ? roots.map((root) => resolve(root)) : null;
  const settings = ensurePipelineConfig({ dataDir, rootsOverride: rootOverride });
  const bookRoots = rootOverride ?? settings.books_roots;
  const db = openDatabase({ dataDir });
  markInterruptedRuns(db);
  const initialSync = syncAll(db, bookRoots);

  const runManager = new RunManager({
    db,
    dataDir,
    onFinished: (run) => {
      try {
        syncBook(db, run.bookPath, { force: true });
      } catch (error) {
        console.error(`[workbench] 执行结束后同步失败：${error.message}`);
      }
    },
    onError: (error) => console.error("[workbench] 运行记录写入失败：", error),
  });

  const api = createApi({
    db,
    roots: rootOverride,
    runManager,
    dataDir,
    allowNetwork,
    networkToken,
  });
  const server = createHttpServer(createHandler(api));
  await new Promise((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolvePromise);
  });
  const address = server.address();
  const actualPort = typeof address === "object" && address ? address.port : port;
  const displayHost = host === "::1" ? "[::1]" : host;
  const url = `http://${displayHost}:${actualPort}`;

  return {
    server,
    db,
    runManager,
    url,
    port: actualPort,
    roots: bookRoots,
    initialSync,
    settings,
    async close() {
      await new Promise((resolvePromise) => server.close(resolvePromise));
      db.close();
    },
  };
}

function openBrowser(url) {
  const command = process.platform === "win32"
    ? { file: "cmd", args: ["/c", "start", "", url] }
    : process.platform === "darwin"
      ? { file: "open", args: [url] }
      : { file: "xdg-open", args: [url] };
  const child = spawn(command.file, command.args, { detached: true, stdio: "ignore" });
  child.on("error", () => {});
  child.unref();
}

function parseArgs(argv) {
  const options = {
    port: Number(process.env.WORKBENCH_PORT || 43120),
    host: process.env.WORKBENCH_HOST || "127.0.0.1",
    roots: [],
    dataDir: null,
    allowNetwork: false,
    open: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--port") options.port = Number(argv[++index]);
    else if (value === "--host") options.host = argv[++index];
    else if (value === "--root") options.roots.push(argv[++index]);
    else if (value === "--data-dir") options.dataDir = argv[++index];
    else if (value === "--allow-network") options.allowNetwork = true;
    else if (value === "--open") options.open = true;
    else if (value === "--help" || value === "-h") options.help = true;
    else throw new Error(`未知参数：${value}`);
  }
  if (!Number.isInteger(options.port) || options.port < 0 || options.port > 65535) {
    throw new Error("--port 必须是 0–65535 的整数");
  }
  return options;
}

function printHelp() {
  console.log(`签约工作台后端 ${VERSION}

用法：
  node novel-workbench/server/index.mjs [--port 43120] [--host 127.0.0.1]
       [--root <书库根目录> ...] [--data-dir <数据目录>] [--allow-network] [--open]

默认：
  数据目录  %LOCALAPPDATA%\\NovelWorkbench（WORKBENCH_DATA_DIR 可覆盖）
  书库根    config.json 的 BOOKS_ROOT（WORKBENCH_BOOKS_ROOTS 可覆盖）
  只监听 127.0.0.1；非回环地址必须显式 --allow-network
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
  if (options.help) {
    printHelp();
    return 0;
  }
  let instance;
  try {
    instance = await startWorkbench({
      port: options.port,
      host: options.host,
      roots: options.roots.length ? options.roots : null,
      dataDir: options.dataDir ? resolve(options.dataDir) : defaultDataDir(),
      allowNetwork: options.allowNetwork,
    });
  } catch (error) {
    console.error(`启动失败：${error.message}`);
    return 2;
  }

  console.log(`签约工作台后端已启动 ${VERSION}`);
  console.log(`数据目录：${options.dataDir ? resolve(options.dataDir) : defaultDataDir()}`);
  console.log(`书库根目录：${instance.roots.join(" ｜ ") || "(未配置)"}`);
  console.log(`接口地址：${instance.url}`);
  console.log(`初始同步：发现 ${instance.initialSync.discovered} 本，入库 ${instance.initialSync.synced} 本，跳过 ${instance.initialSync.skipped} 本`);
  for (const error of instance.initialSync.errors) {
    console.error(`同步失败：${error.path} — ${error.message}`);
  }
  if (instance.settings.needs_setup) {
    console.log("首次运行：打开页面后按提示设置书库根目录与 Python 路径。");
    for (const error of instance.settings.errors) console.log(`  待修正：${error}`);
  }
  if (options.open) openBrowser(instance.url);

  const shutdown = async (signal) => {
    console.log(`\n收到 ${signal}，正在关闭…`);
    try {
      await instance.close();
    } finally {
      process.exit(0);
    }
  };
  process.once("SIGINT", () => shutdown("SIGINT"));
  process.once("SIGTERM", () => shutdown("SIGTERM"));
  return null;
}

const invokedDirectly = Boolean(process.argv[1])
  && pathToFileURL(resolve(process.argv[1])).href === import.meta.url;
if (invokedDirectly) {
  main()
    .then((code) => {
      if (code !== null) process.exitCode = code;
    })
    .catch((error) => {
      console.error(error.stack ?? error.message);
      process.exitCode = 1;
    });
}
