import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { defaultDataDir, loadWorkbenchConfig, SKILL_DIR } from "./paths.mjs";

const SETTINGS_FILE = "settings.json";
const PIPELINE_FILE = "pipeline.json";
const SETTINGS_SCHEMA = 1;

export class SettingsError extends Error {
  constructor(message, errors = []) {
    super(message);
    this.name = "SettingsError";
    this.errors = errors;
  }
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function which(command) {
  const tool = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(tool, [command], { encoding: "utf8", timeout: 3000, windowsHide: true });
  if (result.status !== 0) return null;
  return result.stdout.split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? null;
}

const pythonChecks = new Map();

function workingPython(candidate) {
  if (!candidate) return false;
  const cached = pythonChecks.get(candidate);
  if (cached && Date.now() - cached.at < 60_000) return cached.ok;
  let ok = false;
  try {
    if (existsSync(candidate)) {
      const result = spawnSync(candidate, ["--version"], { encoding: "utf8", timeout: 3000, windowsHide: true });
      ok = result.status === 0;
    }
  } catch {
    ok = false;
  }
  pythonChecks.set(candidate, { ok, at: Date.now() });
  return ok;
}

export function detectPython() {
  for (const name of ["python", "python3", "py"]) {
    const found = which(name);
    if (found && workingPython(found)) return found;
  }
  return null;
}

function isDirectory(path) {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

function isFile(path) {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function normalizeRoots(value) {
  if (!value) return [];
  const list = Array.isArray(value) ? value : [value];
  return [...new Set(
    list
      .map((item) => String(item).trim())
      .filter(Boolean)
      .map((item) => resolve(item)),
  )];
}

function splitList(value) {
  return normalizeRoots(String(value).split(/[;,]/));
}

function choosePath({ env, envName, userValue, fallback, resolvePath = true }) {
  if (env[envName]) return { value: resolvePath ? resolve(env[envName]) : env[envName], source: "env" };
  if (userValue) return { value: resolvePath ? resolve(userValue) : userValue, source: "user" };
  if (fallback) return { value: fallback, source: "default" };
  return { value: null, source: "none" };
}

function derive(baseDir, ...parts) {
  return baseDir ? join(baseDir, ...parts) : null;
}

function validatePaths({
  booksRoots,
  python,
  autopilot,
  node,
  storyctl,
  trackingCommit,
  checkOutline,
  gateRunner,
  platformDir,
  strictOptional = false,
}) {
  const errors = [];
  const warnings = [];
  const optionalIssue = (message) => {
    if (strictOptional) errors.push(message);
    else warnings.push(message);
  };
  if (!booksRoots.length) errors.push("缺少书库根目录");
  for (const root of booksRoots) {
    if (!isAbsolute(root)) errors.push(`书库根目录必须是绝对路径：${root}`);
    else if (!isDirectory(root)) errors.push(`书库根目录不存在或不是目录：${root}`);
  }
  if (!python) errors.push("缺少 Python 路径");
  else if (!workingPython(python)) errors.push(`Python 不可执行：${python}`);
  if (!autopilot) errors.push("缺少 autopilot.py 路径");
  else if (basename(autopilot) !== "autopilot.py" || !existsSync(autopilot)) {
    errors.push(`autopilot.py 不存在：${autopilot}`);
  }
  if (!node) errors.push("缺少 Node 路径");
  else if (!isFile(node)) errors.push(`Node 不可执行：${node}`);
  for (const [label, path] of [
    ["storyctl.py", storyctl],
    ["tracking_commit.py", trackingCommit],
    ["check-outline-contract.js", checkOutline],
  ]) {
    if (!path) errors.push(`缺少 ${label}`);
    else if (!isFile(path)) errors.push(`${label} 不存在：${path}`);
  }
  if (gateRunner) {
    if (basename(gateRunner) !== "gate_runner.py" || !isFile(gateRunner)) {
      optionalIssue(`gate_runner.py 不存在：${gateRunner}`);
    }
  } else {
    warnings.push("未配置 gate_runner：闸门报告只能显示已存 findings");
  }
  if (platformDir && !isDirectory(platformDir)) {
    optionalIssue(`平台目录不存在：${platformDir}`);
  }
  return { errors, warnings };
}

export function effectiveSettings({ dataDir = defaultDataDir(), env = process.env, rootsOverride = null } = {}) {
  const userPath = join(dataDir, SETTINGS_FILE);
  const user = readJson(userPath) ?? {};
  const defaults = (() => {
    try {
      return loadWorkbenchConfig();
    } catch {
      return {};
    }
  })();
  const sources = {};

  let booksRoots;
  if (rootsOverride?.length) {
    booksRoots = normalizeRoots(rootsOverride);
    sources.books_roots = "cli";
  } else if (env.WORKBENCH_BOOKS_ROOTS) {
    booksRoots = splitList(env.WORKBENCH_BOOKS_ROOTS);
    sources.books_roots = "env";
  } else if (user.books_roots || user.books_root) {
    booksRoots = normalizeRoots(user.books_roots ?? user.books_root);
    sources.books_roots = "user";
  } else if (defaults.BOOKS_ROOT) {
    booksRoots = normalizeRoots(defaults.BOOKS_ROOT);
    sources.books_roots = "default";
  } else {
    booksRoots = [];
    sources.books_roots = "none";
  }

  const pythonPick = choosePath({ env, envName: "WORKBENCH_PYTHON", userValue: user.python });
  let python = pythonPick.value;
  if (pythonPick.source === "none") {
    if (defaults.PYTHON && workingPython(defaults.PYTHON)) {
      python = defaults.PYTHON;
      sources.python = "default";
    } else {
      python = detectPython();
      sources.python = python ? "auto" : "none";
    }
  } else {
    sources.python = pythonPick.source;
  }

  const nodePick = choosePath({ env, envName: "WORKBENCH_NODE", userValue: user.node });
  let node = nodePick.value;
  if (!node) {
    if (defaults.NODE && isFile(defaults.NODE)) {
      node = defaults.NODE;
      sources.node = "default";
    } else {
      node = process.execPath;
      sources.node = "auto";
    }
  } else {
    sources.node = nodePick.source;
  }

  const autopilot = resolve(
    env.WORKBENCH_AUTOPILOT || user.autopilot || join(SKILL_DIR, "scripts", "autopilot.py"),
  );
  sources.autopilot = env.WORKBENCH_AUTOPILOT ? "env" : user.autopilot ? "user" : "default";

  const storyPick = choosePath({
    env, envName: "WORKBENCH_STORY_LONG_WRITE", userValue: user.story_long_write,
  });
  const storyLongWrite = storyPick.value || (defaults.STORY_LONG_WRITE ? resolve(defaults.STORY_LONG_WRITE) : null);
  sources.story_long_write = storyPick.source === "none" && storyLongWrite ? "default" : storyPick.source;

  function scriptField({ envName, userValue, fallback, defaultValue, sourceKey }) {
    const pick = choosePath({ env, envName, userValue });
    if (pick.value) {
      sources[sourceKey] = pick.source;
      return pick.value;
    }
    if (fallback) {
      const path = resolve(fallback);
      if (existsSync(path)) {
        sources[sourceKey] = "derived";
        return path;
      }
    }
    sources[sourceKey] = defaultValue ? "default" : "none";
    return defaultValue ?? null;
  }

  const storyctl = scriptField({
    envName: "WORKBENCH_STORYCTL",
    userValue: user.storyctl,
    fallback: derive(storyLongWrite, "scripts", "storyctl.py"),
    defaultValue: defaults.STORYCTL,
    sourceKey: "storyctl",
  });
  const trackingCommit = scriptField({
    envName: "WORKBENCH_TRACKING_COMMIT",
    userValue: user.tracking_commit,
    fallback: derive(storyLongWrite, "scripts", "tracking_commit.py"),
    defaultValue: defaults.TRACKING_COMMIT,
    sourceKey: "tracking_commit",
  });
  const buildWriterPrompt = scriptField({
    envName: "WORKBENCH_BUILD_WRITER_PROMPT",
    userValue: user.build_writer_prompt,
    fallback: derive(storyLongWrite, "scripts", "build_writer_prompt.py"),
    defaultValue: defaults.BUILD_WRITER_PROMPT,
    sourceKey: "build_writer_prompt",
  });
  const checkOutline = scriptField({
    envName: "WORKBENCH_CHECK_OUTLINE",
    userValue: user.check_outline,
    fallback: derive(storyLongWrite, "scripts", "check-outline-contract.js"),
    defaultValue: defaults.CHECK_OUTLINE,
    sourceKey: "check_outline",
  });
  const aiPatterns = scriptField({
    envName: "WORKBENCH_AI_PATTERNS",
    userValue: user.ai_patterns_check,
    fallback: derive(storyLongWrite, "scripts", "check-ai-patterns.js"),
    defaultValue: defaults.AI_PATTERNS_CHECK,
    sourceKey: "ai_patterns_check",
  });
  const degeneration = scriptField({
    envName: "WORKBENCH_DEGENERATION",
    userValue: user.degeneration_check,
    fallback: derive(storyLongWrite, "scripts", "check-degeneration.js"),
    defaultValue: defaults.DEGENERATION_CHECK,
    sourceKey: "degeneration_check",
  });

  const platformPick = choosePath({
    env, envName: "WORKBENCH_PLATFORM_DIR", userValue: user.platform_dir,
  });
  const platformDir = platformPick.value || (defaults.QIMAO_PLATFORM_DIR ? resolve(defaults.QIMAO_PLATFORM_DIR) : null);
  sources.platform_dir = platformPick.source === "none" && platformDir ? "default" : platformPick.source;

  const corePick = choosePath({
    env, envName: "WORKBENCH_CORE_TOOLS", userValue: user.core_tools_dir,
  });
  const coreTools = corePick.value || (defaults.CORE_TOOLS_DIR ? resolve(defaults.CORE_TOOLS_DIR) : null);
  sources.core_tools_dir = corePick.source === "none" && coreTools ? "default" : corePick.source;

  function platformField({ envName, userValue, fallback, defaultValue, sourceKey }) {
    return scriptField({ envName, userValue, fallback, defaultValue, sourceKey });
  }

  const gateRunner = platformField({
    envName: "WORKBENCH_GATE_RUNNER",
    userValue: user.gate_runner,
    fallback: derive(coreTools, "gate_runner.py"),
    defaultValue: defaults.GATE_RUNNER,
    sourceKey: "gate_runner",
  });
  const styleProbe = platformField({
    envName: "WORKBENCH_STYLE_PROBE",
    userValue: user.style_probe,
    fallback: derive(coreTools, "style_probe.py"),
    defaultValue: defaults.STYLE_PROBE,
    sourceKey: "style_probe",
  });
  const clarityOrgProbe = platformField({
    envName: "WORKBENCH_CLARITY_ORG_PROBE",
    userValue: user.clarity_org_probe,
    fallback: derive(coreTools, "clarity_org_probe.py"),
    defaultValue: defaults.CLARITY_ORG_PROBE,
    sourceKey: "clarity_org_probe",
  });
  const qimaoHarness = platformField({
    envName: "WORKBENCH_QIMAO_HARNESS",
    userValue: user.qimao_harness,
    fallback: derive(platformDir, "harness"),
    defaultValue: defaults.QIMAO_HARNESS,
    sourceKey: "qimao_harness",
  });
  const qimaoGates = platformField({
    envName: "WORKBENCH_QIMAO_GATES",
    userValue: user.qimao_gates,
    fallback: derive(qimaoHarness, "gates.json"),
    defaultValue: defaults.QIMAO_GATES,
    sourceKey: "qimao_gates",
  });
  const qimaoThresholds = platformField({
    envName: "WORKBENCH_QIMAO_THRESHOLDS",
    userValue: user.qimao_thresholds,
    fallback: derive(platformDir, "config", "thresholds.json"),
    defaultValue: defaults.QIMAO_THRESHOLDS,
    sourceKey: "qimao_thresholds",
  });
  const qimaoGenreBase = platformField({
    envName: "WORKBENCH_QIMAO_GENRE_BASE",
    userValue: user.qimao_genre_base,
    fallback: derive(qimaoHarness, "genre-baselines.json"),
    defaultValue: defaults.QIMAO_GENRE_BASE,
    sourceKey: "qimao_genre_base",
  });
  const qimaoStyleAnchor = platformField({
    envName: "WORKBENCH_QIMAO_STYLE_ANCHOR",
    userValue: user.qimao_style_anchor,
    fallback: derive(qimaoHarness, "style-anchor.json"),
    defaultValue: defaults.QIMAO_STYLE_ANCHOR,
    sourceKey: "qimao_style_anchor",
  });
  const qimaoChapterCheck = platformField({
    envName: "WORKBENCH_QIMAO_CHAPTER_CHECK",
    userValue: user.qimao_chapter_check,
    fallback: derive(platformDir, "tools", "qimao-chapter-check.py"),
    defaultValue: defaults.QIMAO_CHAPTER_CHECK,
    sourceKey: "qimao_chapter_check",
  });

  let readRoots;
  if (env.WORKBENCH_READ_ROOTS) {
    readRoots = splitList(env.WORKBENCH_READ_ROOTS);
    sources.read_roots = "env";
  } else if (Array.isArray(user.read_roots) && user.read_roots.length) {
    readRoots = normalizeRoots(user.read_roots);
    sources.read_roots = "user";
  } else {
    readRoots = normalizeRoots([platformDir, storyLongWrite, SKILL_DIR].filter(Boolean));
    sources.read_roots = "default";
  }
  readRoots = readRoots.filter((root) => isDirectory(root));

  const { errors, warnings } = validatePaths({
    booksRoots,
    python,
    autopilot,
    node,
    storyctl,
    trackingCommit,
    checkOutline,
    gateRunner,
    platformDir,
  });
  if (user.schema_version && user.schema_version > SETTINGS_SCHEMA) {
    errors.push(`settings.json 版本 ${user.schema_version} 高于当前支持的 ${SETTINGS_SCHEMA}`);
  }
  if (!readRoots.length) warnings.push("没有可用的只读书外参考目录，模型将读不到平台/手法手册");

  const userPresent = Boolean(user && Object.keys(user).length);
  return {
    schema_version: SETTINGS_SCHEMA,
    data_dir: resolve(dataDir),
    user_settings_path: userPath,
    pipeline_config_path: join(resolve(dataDir), PIPELINE_FILE),
    user_settings_present: userPresent,
    updated_at: user.updated_at ?? null,
    books_roots: booksRoots,
    books_root: booksRoots[0] ?? null,
    python,
    node,
    autopilot,
    story_long_write: storyLongWrite,
    storyctl,
    tracking_commit: trackingCommit,
    build_writer_prompt: buildWriterPrompt,
    check_outline: checkOutline,
    ai_patterns_check: aiPatterns,
    degeneration_check: degeneration,
    gate_runner: gateRunner,
    platform_dir: platformDir,
    core_tools_dir: coreTools,
    style_probe: styleProbe,
    clarity_org_probe: clarityOrgProbe,
    qimao_harness: qimaoHarness,
    qimao_gates: qimaoGates,
    qimao_thresholds: qimaoThresholds,
    qimao_genre_base: qimaoGenreBase,
    qimao_style_anchor: qimaoStyleAnchor,
    qimao_chapter_check: qimaoChapterCheck,
    read_roots: readRoots,
    sources,
    exists: {
      books_roots: booksRoots.map((root) => isDirectory(root)),
      python: Boolean(python && workingPython(python)),
      node: Boolean(node && isFile(node)),
      autopilot: Boolean(autopilot && existsSync(autopilot)),
      storyctl: Boolean(storyctl && isFile(storyctl)),
      tracking_commit: Boolean(trackingCommit && isFile(trackingCommit)),
      check_outline: Boolean(checkOutline && isFile(checkOutline)),
      gate_runner: Boolean(gateRunner && isFile(gateRunner)),
      platform_dir: Boolean(platformDir && isDirectory(platformDir)),
      read_roots: readRoots.map((root) => isDirectory(root)),
    },
    errors,
    warnings,
    needs_setup: errors.length > 0 || (!userPresent && !booksRoots.some((root) => isDirectory(root))),
  };
}

export function pipelineConfig(settings) {
  // PLATFORMS 合成（并行会话加 PLATFORMS 契约后的补丁）：仓库 config.json 是
  // 默认源（含 zongheng 等全部平台），settings 里的 qimao 路径覆盖 qimao 项；
  // 仓库配置没有 PLATFORMS 时（老配置）从 settings 合成 qimao。没有这一步，
  // env/设置驱动的 pipeline.json 缺 PLATFORMS，platform_cfg 直接硬报错。
  const base = (() => {
    try {
      return loadWorkbenchConfig();
    } catch {
      return {};
    }
  })();
  const platforms = structuredClone(base.PLATFORMS ?? {});
  const qimao = { ...(platforms.qimao ?? {}) };
  if (settings.platform_dir) qimao.DIR = settings.platform_dir;
  if (settings.qimao_genre_base) qimao.GENRE_BASE = settings.qimao_genre_base;
  if (settings.qimao_style_anchor) qimao.STYLE_ANCHOR = settings.qimao_style_anchor;
  qimao.GENRES = qimao.GENRES ?? [];
  platforms.qimao = qimao;
  return {
    PYTHON: settings.python,
    NODE: settings.node,
    STORY_LONG_WRITE: settings.story_long_write,
    STORYCTL: settings.storyctl,
    TRACKING_COMMIT: settings.tracking_commit,
    BUILD_WRITER_PROMPT: settings.build_writer_prompt,
    CHECK_OUTLINE: settings.check_outline,
    AI_PATTERNS_CHECK: settings.ai_patterns_check,
    DEGENERATION_CHECK: settings.degeneration_check,
    QIMAO_PLATFORM_DIR: settings.platform_dir,
    QIMAO_HARNESS: settings.qimao_harness,
    QIMAO_GATES: settings.qimao_gates,
    QIMAO_THRESHOLDS: settings.qimao_thresholds,
    QIMAO_GENRE_BASE: settings.qimao_genre_base,
    QIMAO_STYLE_ANCHOR: settings.qimao_style_anchor,
    QIMAO_CHAPTER_CHECK: settings.qimao_chapter_check,
    CORE_TOOLS_DIR: settings.core_tools_dir,
    GATE_RUNNER: settings.gate_runner,
    STYLE_PROBE: settings.style_probe,
    CLARITY_ORG_PROBE: settings.clarity_org_probe,
    PLATFORMS: platforms,
    WORKBENCH_READ_ROOTS: settings.read_roots,
  };
}

export function writePipelineConfig(dataDir, settings) {
  const target = join(resolve(dataDir), PIPELINE_FILE);
  const body = `${JSON.stringify(pipelineConfig(settings), null, 2)}\n`;
  mkdirSync(dirname(target), { recursive: true });
  try {
    if (readFileSync(target, "utf8") === body) return target;
  } catch {}
  const temp = `${target}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(temp, body, "utf8");
  renameSync(temp, target);
  return target;
}

export function ensurePipelineConfig({ dataDir = defaultDataDir(), env = process.env, rootsOverride = null } = {}) {
  const settings = effectiveSettings({ dataDir, env, rootsOverride });
  writePipelineConfig(dataDir, settings);
  return settings;
}

function assignIfPresent(next, patch, key) {
  if (patch[key] !== undefined) next[key] = patch[key];
}

export function saveSettings({ dataDir = defaultDataDir(), patch = {} } = {}) {
  if (!patch || typeof patch !== "object") throw new SettingsError("设置必须是对象");
  const current = readJson(join(dataDir, SETTINGS_FILE)) ?? {};
  const next = {
    ...current,
    schema_version: SETTINGS_SCHEMA,
    updated_at: new Date().toISOString(),
  };
  if (patch.books_root !== undefined) {
    next.books_root = patch.books_root;
    delete next.books_roots;
  }
  if (patch.books_roots !== undefined) {
    next.books_roots = patch.books_roots;
    delete next.books_root;
  }
  for (const key of [
    "python", "node", "autopilot", "story_long_write", "gate_runner", "platform_dir",
    "core_tools_dir", "storyctl", "tracking_commit", "build_writer_prompt", "check_outline",
    "ai_patterns_check", "degeneration_check", "style_probe", "clarity_org_probe",
    "qimao_harness", "qimao_gates", "qimao_thresholds", "qimao_genre_base",
    "qimao_style_anchor", "qimao_chapter_check",
  ]) {
    assignIfPresent(next, patch, key);
  }
  if (patch.read_roots !== undefined) next.read_roots = normalizeRoots(patch.read_roots);

  const roots = normalizeRoots(next.books_roots ?? next.books_root);
  if (patch.create_books_root && roots.length) {
    let first = true;
    for (const root of roots) {
      if (first && !existsSync(root)) mkdirSync(root, { recursive: true });
      first = false;
    }
  }
  if (roots.length) {
    next.books_roots = roots;
    next.books_root = roots[0];
  }
  for (const key of [
    "python", "node", "autopilot", "story_long_write", "gate_runner", "platform_dir",
    "core_tools_dir", "storyctl", "tracking_commit", "build_writer_prompt", "check_outline",
    "ai_patterns_check", "degeneration_check", "style_probe", "clarity_org_probe",
    "qimao_harness", "qimao_gates", "qimao_thresholds", "qimao_genre_base",
    "qimao_style_anchor", "qimao_chapter_check",
  ]) {
    if (next[key]) next[key] = resolve(next[key]);
  }

  const explicitGate = patch.gate_runner;
  if (explicitGate && (!isFile(explicitGate) || basename(explicitGate) !== "gate_runner.py")) {
    throw new SettingsError("设置未通过校验", [`gate_runner.py 不存在：${explicitGate}`]);
  }
  const explicitPlatform = patch.platform_dir;
  if (explicitPlatform && !isDirectory(explicitPlatform)) {
    throw new SettingsError("设置未通过校验", [`平台目录不存在：${explicitPlatform}`]);
  }

  mkdirSync(dataDir, { recursive: true });
  const target = join(dataDir, SETTINGS_FILE);
  mkdirSync(dirname(target), { recursive: true });
  const previous = existsSync(target) ? readFileSync(target, "utf8") : null;
  const temp = `${target}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(temp, `${JSON.stringify(next, null, 2)}\n`, "utf8");
  renameSync(temp, target);

  // 让 effectiveSettings 统一处理“默认值/推导值”，校验失败时回滚原文件
  const settings = effectiveSettings({ dataDir });
  if (settings.errors.length) {
    if (previous === null) {
      try {
        unlinkSync(target);
      } catch {}
    } else {
      writeFileSync(target, previous, "utf8");
    }
    throw new SettingsError("设置未通过校验", settings.errors);
  }
  writePipelineConfig(dataDir, settings);
  return settings;
}
