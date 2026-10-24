import { readFileSync } from "node:fs";
import { homedir, platform } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const SERVER_DIR = dirname(fileURLToPath(import.meta.url));
export const SKILL_DIR = resolve(SERVER_DIR, "..");
export const REPO_DIR = resolve(SKILL_DIR, "..");
export const CONFIG_PATH = join(SKILL_DIR, "config.json");
export const AGENTS_DEFAULT_PATH = join(SKILL_DIR, "agents.default.json");

export function loadWorkbenchConfig(path = CONFIG_PATH) {
  return JSON.parse(readFileSync(path, "utf8"));
}

export function defaultDataDir(env = process.env) {
  if (env.WORKBENCH_DATA_DIR) return resolve(env.WORKBENCH_DATA_DIR);
  if (platform() === "win32") {
    const base = env.LOCALAPPDATA || join(homedir(), "AppData", "Local");
    return join(base, "NovelWorkbench");
  }
  const base = env.XDG_DATA_HOME || join(homedir(), ".local", "share");
  return join(base, "novel-workbench");
}

/**
 * 书库根目录：显式环境变量优先，其次 config.json 的 BOOKS_ROOT。
 * 发版后用户换机器只需改 config.json 或 WORKBENCH_BOOKS_ROOTS，不用改代码。
 */
export function resolveBookRoots(env = process.env, configPath = CONFIG_PATH) {
  const roots = [];
  if (env.WORKBENCH_BOOKS_ROOTS) {
    for (const part of env.WORKBENCH_BOOKS_ROOTS.split(/[;,]/)) {
      const trimmed = part.trim();
      if (trimmed) roots.push(resolve(trimmed));
    }
  }
  try {
    const config = loadWorkbenchConfig(configPath);
    if (config.BOOKS_ROOT) roots.push(resolve(config.BOOKS_ROOT));
  } catch {
    // 配置缺失时只用环境变量；调用方会报告根目录为空
  }
  return [...new Set(roots)];
}
