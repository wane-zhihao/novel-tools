import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { AGENTS_DEFAULT_PATH, defaultDataDir } from "./paths.mjs";

const REQUIRED_ROLES = ["writer", "judge"];
const PROVIDER_TYPES = new Set(["mock", "openai"]);
const TOP_LEVEL_KEYS = new Set(["schema_version", "providers", "roles", "budgets"]);
const PROVIDER_KEYS = new Set(["type", "base_url", "api_key_env", "api_key_optional"]);
const ROLE_KEYS = new Set([
  "provider", "model", "temperature", "timeout_s", "max_attempts", "max_tokens",
]);
const BUDGET_KEYS = new Set([
  "llm_rounds", "fix_rounds", "n20_rounds", "node_timeout_s",
  "max_completion_tokens", "max_tool_steps",
]);
const SECRET_VALUE = /^(sk-|bearer\s|ghp_|xoxb-)/i;
const CREDENTIAL_URL = /^[a-z]+:\/\/[^/\s]+@/i;

function scanSecretValues(value, path, errors) {
  if (typeof value === "string") {
    if (SECRET_VALUE.test(value) || CREDENTIAL_URL.test(value)) {
      errors.push(`${path} 疑似包含密钥；密钥请用环境变量或 UI 密钥存储`);
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => scanSecretValues(item, `${path}[${index}]`, errors));
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      scanSecretValues(child, `${path}.${key}`, errors);
    }
  }
}

export function redactSecrets(value) {
  if (typeof value === "string") {
    return SECRET_VALUE.test(value) || CREDENTIAL_URL.test(value) ? "[已隐藏]" : value;
  }
  if (Array.isArray(value)) return value.map(redactSecrets);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, redactSecrets(child)]));
  }
  return value;
}

function readJsonIfPresent(path) {
  if (!path || !existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf8"));
}

function mergeConfig(base, override) {
  if (!override) return structuredClone(base);
  const merged = { ...base, ...override };
  const providerNames = new Set([
    ...Object.keys(base.providers ?? {}),
    ...Object.keys(override.providers ?? {}),
  ]);
  merged.providers = {};
  for (const name of providerNames) {
    merged.providers[name] = { ...(base.providers?.[name] ?? {}), ...(override.providers?.[name] ?? {}) };
  }
  const roleNames = new Set([...Object.keys(base.roles ?? {}), ...Object.keys(override.roles ?? {})]);
  merged.roles = {};
  for (const role of roleNames) {
    merged.roles[role] = { ...(base.roles?.[role] ?? {}), ...(override.roles?.[role] ?? {}) };
  }
  merged.budgets = { ...(base.budgets ?? {}), ...(override.budgets ?? {}) };
  return merged;
}

function isInteger(value) {
  return Number.isInteger(value);
}

export function validateAgentConfig(config) {
  const errors = [];
  const warnings = [];
  if (!config || typeof config !== "object") {
    return { errors: ["配置不是对象"], warnings };
  }
  if (config.schema_version !== 1) {
    errors.push(`schema_version 必须是 1（当前 ${config.schema_version ?? "缺失"}）`);
  }
  for (const key of Object.keys(config)) {
    if (!TOP_LEVEL_KEYS.has(key)) errors.push(`未知配置字段：${key}（为避免密钥落盘，不接受自定义字段）`);
  }

  const providers = config.providers ?? {};
  if (!Object.keys(providers).length) errors.push("providers 不能为空");
  for (const [name, provider] of Object.entries(providers)) {
    for (const key of Object.keys(provider ?? {})) {
      if (!PROVIDER_KEYS.has(key)) errors.push(`provider ${name} 含未知字段：${key}`);
    }
    if (!PROVIDER_TYPES.has(provider?.type)) {
      errors.push(`provider ${name} 的 type 必须是 mock 或 openai`);
      continue;
    }
    if (provider.type === "openai") {
      if (typeof provider.base_url !== "string" || !/^https?:\/\//.test(provider.base_url)) {
        errors.push(`provider ${name} 缺少合法的 base_url`);
      }
      const optional = provider.api_key_optional === true;
      if (!optional && (typeof provider.api_key_env !== "string" || !provider.api_key_env)) {
        errors.push(`provider ${name} 必须声明 api_key_env（只写环境变量名，不写密钥）`);
      }
    }
  }

  const roles = config.roles ?? {};
  for (const role of REQUIRED_ROLES) {
    if (!roles[role]) errors.push(`缺少必需角色 ${role}`);
  }
  for (const [role, settings] of Object.entries(roles)) {
    for (const key of Object.keys(settings ?? {})) {
      if (!ROLE_KEYS.has(key)) errors.push(`角色 ${role} 含未知字段：${key}`);
    }
    if (!providers[settings?.provider]) {
      errors.push(`角色 ${role} 指向不存在的 provider ${settings?.provider ?? "(缺失)"}`);
    }
    if (typeof settings?.model !== "string" || !settings.model) {
      errors.push(`角色 ${role} 缺少 model`);
    }
    if (typeof settings?.temperature !== "number" || settings.temperature < 0 || settings.temperature > 2) {
      errors.push(`角色 ${role} 的 temperature 必须在 0–2`);
    }
    if (!isInteger(settings?.timeout_s) || settings.timeout_s < 30 || settings.timeout_s > 3600) {
      errors.push(`角色 ${role} 的 timeout_s 必须是 30–3600 的整数`);
    }
    if (!isInteger(settings?.max_attempts) || settings.max_attempts < 1 || settings.max_attempts > 5) {
      errors.push(`角色 ${role} 的 max_attempts 必须是 1–5 的整数`);
    }
  }

  const budgets = config.budgets ?? {};
  for (const key of Object.keys(budgets)) {
    if (!BUDGET_KEYS.has(key)) errors.push(`budgets 含未知字段：${key}`);
  }
  if (!isInteger(budgets.llm_rounds) || budgets.llm_rounds < 1) errors.push("budgets.llm_rounds 必须是正整数");
  for (const key of ["fix_rounds", "n20_rounds"]) {
    if (budgets[key] !== undefined && (!isInteger(budgets[key]) || budgets[key] < 0)) {
      errors.push(`budgets.${key} 必须是非负整数`);
    }
  }
  if (budgets.node_timeout_s !== undefined
    && (!isInteger(budgets.node_timeout_s) || budgets.node_timeout_s < 60 || budgets.node_timeout_s > 3600)) {
    errors.push("budgets.node_timeout_s 必须是 60–3600 的整数");
  }

  const writer = roles.writer;
  const judge = roles.judge;
  if (writer && judge && writer.provider === judge.provider && writer.model === judge.model) {
    warnings.push("writer 与 judge 使用同一个 provider/model：审稿会系统性高估自己的稿子，建议分离");
  }
  const activeProviders = Object.values(roles)
    .map((settings) => providers[settings?.provider])
    .filter(Boolean);
  if (!activeProviders.some((provider) => provider.type !== "mock")) {
    warnings.push("writer/judge 仍指向 mock provider：不会真正调用模型，只适合回归测试");
  }
  scanSecretValues(config, "config", errors);
  return { errors, warnings };
}

/** 只暴露「是否已配置」，绝不返回密钥本身。 */
export function providerStatus(config, env = process.env, secretNames = new Set()) {
  return Object.entries(config.providers ?? {}).map(([name, provider]) => {
    if (provider?.type === "mock") {
      return { name, type: "mock", configured: true, env: null, source: "mock" };
    }
    const envName = provider?.api_key_env ?? null;
    const optional = provider?.api_key_optional === true;
    const fromEnv = Boolean(envName && env[envName]);
    const fromStore = Boolean(envName && secretNames.has(envName));
    return {
      name,
      type: provider?.type ?? null,
      configured: optional || fromEnv || fromStore,
      env: envName,
      optional,
      source: fromEnv ? "env" : fromStore ? "store" : null,
    };
  });
}

export function loadAgentConfig({
  bookDir,
  dataDir = defaultDataDir(),
  basePath = AGENTS_DEFAULT_PATH,
  env = process.env,
  secretNames = new Set(),
} = {}) {
  const base = readJsonIfPresent(basePath);
  if (!base) throw new Error(`找不到默认 Agent 配置：${basePath}`);
  const globalPath = join(dataDir, "agents.json");
  const globalOverride = readJsonIfPresent(globalPath);
  const overridePath = bookDir ? join(bookDir, "_pipeline", "agents.json") : null;
  const bookOverride = readJsonIfPresent(overridePath);
  const config = mergeConfig(mergeConfig(base, globalOverride), bookOverride);
  const { errors, warnings } = validateAgentConfig(config);
  return {
    config,
    errors,
    warnings,
    providers: providerStatus(config, env, secretNames),
    basePath,
    globalPath: globalOverride ? globalPath : null,
    overridePath: bookOverride ? overridePath : null,
  };
}
