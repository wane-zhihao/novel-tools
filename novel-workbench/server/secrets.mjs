import { spawnSync } from "node:child_process";
import {
  chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { defaultDataDir } from "./paths.mjs";

const SECRETS_FILE = "secrets.json";
const IS_WINDOWS = process.platform === "win32";
const cache = new Map();

export class SecretError extends Error {
  constructor(message) {
    super(message);
    this.name = "SecretError";
  }
}

const ENCRYPT_SCRIPT = `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$text = [Console]::In.ReadToEnd()
$bytes = [Text.Encoding]::UTF8.GetBytes($text)
$protected = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
[Console]::Out.Write([Convert]::ToBase64String($protected))
`;

const DECRYPT_SCRIPT = `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$b64 = [Console]::In.ReadToEnd()
$bytes = [Convert]::FromBase64String($b64.Trim())
$plain = [Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
[Console]::Out.Write([Text.Encoding]::UTF8.GetString($plain))
`;

function powershell(script, input) {
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    input,
    encoding: "utf8",
    timeout: 15_000,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    throw new SecretError(`DPAPI 调用失败：${(result.stderr || result.error?.message || "").slice(0, 300)}`);
  }
  return result.stdout;
}

function storePath(dataDir) {
  return join(dataDir, SECRETS_FILE);
}

function readStore(dataDir) {
  const path = storePath(dataDir);
  if (!existsSync(path)) return { schema_version: 1, secrets: {} };
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    return { schema_version: 1, secrets: {}, ...parsed };
  } catch {
    throw new SecretError(`密钥文件无法解析：${path}`);
  }
}

function writeStore(dataDir, store) {
  mkdirSync(dataDir, { recursive: true });
  const target = storePath(dataDir);
  const temp = `${target}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(temp, `${JSON.stringify(store, null, 2)}\n`, "utf8");
  renameSync(temp, target);
  if (!IS_WINDOWS) {
    try {
      chmodSync(target, 0o600);
    } catch {}
  }
}

function encrypt(value) {
  return IS_WINDOWS ? powershell(ENCRYPT_SCRIPT, value) : value;
}

function decrypt(stored) {
  return IS_WINDOWS ? powershell(DECRYPT_SCRIPT, stored) : stored;
}

/** 只返回密钥值供 provider 使用；绝不写日志或 API 响应。 */
export function getSecret(name, { dataDir = defaultDataDir(), env = process.env } = {}) {
  if (!name) return { value: null, source: null };
  if (env[name]) return { value: env[name], source: "env" };
  const key = `${dataDir}\u0000${name}`;
  if (cache.has(key)) return { value: cache.get(key), source: "store", encrypted: IS_WINDOWS };
  const entry = readStore(dataDir).secrets?.[name];
  if (!entry?.value) return { value: null, source: null };
  try {
    const value = decrypt(entry.value);
    cache.set(key, value);
    return { value, source: "store", encrypted: IS_WINDOWS };
  } catch (error) {
    return { value: null, source: "store", encrypted: IS_WINDOWS, error: error.message };
  }
}

export function setSecret(name, value, { dataDir = defaultDataDir() } = {}) {
  if (!name || typeof name !== "string") throw new SecretError("缺少密钥变量名");
  if (typeof value !== "string" || !value.trim()) throw new SecretError("密钥不能为空");
  const store = readStore(dataDir);
  store.secrets = store.secrets || {};
  store.platform = process.platform;
  store.encrypted = IS_WINDOWS;
  store.secrets[name] = {
    value: encrypt(value.trim()),
    updated_at: new Date().toISOString(),
  };
  writeStore(dataDir, store);
  cache.delete(`${dataDir}\u0000${name}`);
  return { name, configured: true, source: "store", encrypted: IS_WINDOWS };
}

export function deleteSecret(name, { dataDir = defaultDataDir() } = {}) {
  const store = readStore(dataDir);
  if (!store.secrets?.[name]) return { name, configured: false };
  delete store.secrets[name];
  writeStore(dataDir, store);
  cache.delete(`${dataDir}\u0000${name}`);
  return { name, configured: false };
}

export function secretInfo({ dataDir = defaultDataDir(), env = process.env } = {}) {
  const store = readStore(dataDir);
  const names = Object.keys(store.secrets ?? {}).map((name) => ({
    name,
    source: env[name] ? "env" : "store",
    updated_at: store.secrets[name]?.updated_at ?? null,
  }));
  return {
    encrypted: IS_WINDOWS,
    platform: process.platform,
    names,
  };
}

export function secretResolver({ dataDir = defaultDataDir(), env = process.env } = {}) {
  return (name) => getSecret(name, { dataDir, env });
}
