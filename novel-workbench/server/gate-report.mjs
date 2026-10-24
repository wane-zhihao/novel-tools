import { execFile } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { defaultDataDir } from "./paths.mjs";
import { ensurePipelineConfig } from "./settings.mjs";
import { HttpError } from "./http.mjs";

function run(command, args, { cwd, timeoutMs = 60_000, env = {} } = {}) {
  return new Promise((accept) => {
    execFile(
      command,
      args,
      {
        cwd,
        timeout: timeoutMs,
        maxBuffer: 8 * 1024 * 1024,
        encoding: "utf8",
        windowsHide: true,
        env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1", ...env },
      },
      (error, stdout, stderr) => {
        accept({
          code: error ? (typeof error.code === "number" ? error.code : 1) : 0,
          stdout: stdout ?? "",
          stderr: stderr ?? "",
        });
      },
    );
  });
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function pipelineTools(bookPath, dataDir) {
  // settings 是唯一路径来源；旧书里的 workbench.json 只做缺字段回退
  const settings = ensurePipelineConfig({ dataDir });
  const manifest = readJson(join(bookPath, "_pipeline", "workbench.json")) ?? {};
  return {
    python: settings.python || manifest.python,
    gate_runner: settings.gate_runner || manifest.gate_runner,
    platform: settings.platform_dir || manifest.platform,
    configPath: settings.pipeline_config_path,
  };
}

export function findChapterFile(bookPath, chapter) {
  const bodyDir = join(bookPath, "正文");
  if (!existsSync(bodyDir)) return null;
  const prefix = `第${String(chapter).padStart(3, "0")}章`;
  const hits = readdirSync(bodyDir).filter((name) => name.startsWith(prefix) && name.endsWith(".md"));
  return hits.length === 1 ? resolve(bodyDir, hits[0]) : null;
}

export async function buildChapterReport(bookPath, chapter, { genre = null, dataDir = defaultDataDir() } = {}) {
  if (!Number.isInteger(chapter) || chapter < 1 || chapter > 9999) {
    throw new HttpError(400, "invalid_chapter", "chapter 必须是 1–9999 的整数");
  }
  const findingsPath = join(bookPath, "_pipeline", `findings_C${chapter}.json`);
  const report = {
    chapter,
    source: existsSync(findingsPath) ? "findings" : "none",
    warning: null,
    gates: null,
    findings: existsSync(findingsPath) ? readJson(findingsPath) : null,
  };

  const body = findChapterFile(bookPath, chapter);
  if (!body) {
    report.warning = `找不到唯一的正文文件：正文/第${String(chapter).padStart(3, "0")}章_*.md`;
    return report;
  }

  const tools = pipelineTools(bookPath, dataDir);
  if (
    !tools.gate_runner
    || basename(tools.gate_runner) !== "gate_runner.py"
    || !tools.platform
    || !tools.python
    || !existsSync(tools.gate_runner)
  ) {
    report.warning = "workbench.json 未配置 gate_runner/platform，只能显示已存下的 findings";
    return report;
  }

  const args = [tools.gate_runner, "--platform", tools.platform, "--json"];
  if (genre) args.push("--genre", String(genre));
  args.push(body);
  const result = await run(tools.python, args, {
    cwd: bookPath,
    env: { WORKBENCH_CONFIG: tools.configPath },
  });
  try {
    const parsed = JSON.parse(result.stdout);
    if (Array.isArray(parsed) && parsed.length && typeof parsed[0] === "object") {
      report.gates = parsed[0];
      report.source = "gate_runner";
    } else {
      report.warning = "gate_runner 没有返回闸门结果";
    }
  } catch {
    report.warning = `gate_runner 输出无法解析（exit=${result.code}）`;
  }
  return report;
}
