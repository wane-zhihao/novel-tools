import {
  copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, renameSync,
  statSync, unlinkSync, writeFileSync,
} from "node:fs";
import { dirname, extname, isAbsolute, join, relative, resolve, sep } from "node:path";

const TEXT_EXTENSIONS = new Set([".md", ".txt", ".json", ".yaml", ".yml", ".csv"]);
const PROTECTED_EXACT = new Set([
  "_pipeline/state.json",
  "_pipeline/.step_token",
  "_pipeline/workbench.json",
  "_pipeline/run.json",
  "_pipeline/.next.lock",
]);
const PROTECTED_PREFIXES = ["追踪/", "投稿包/", "_pipeline/findings_"];
const BACKUP_LIMIT = 50;

function normalizedRel(rel) {
  return String(rel).replaceAll("\\", "/").toLowerCase();
}

function isProtected(rel) {
  const normalized = normalizedRel(rel);
  if (PROTECTED_EXACT.has(normalized)) return true;
  return PROTECTED_PREFIXES.some((prefix) => (
    normalized === prefix.slice(0, -1) || normalized.startsWith(prefix)
  ));
}

export const TOOL_DEFINITIONS = [
  {
    type: "function",
    function: {
      name: "read_file",
      description: "读取书目录内的文本文件。大文件可用 offset/limit 分段读取。",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "相对书目录的路径，例如 大纲/细纲_第003章.md" },
          offset: { type: "integer", minimum: 0, description: "从第几个字符开始，默认 0" },
          limit: { type: "integer", minimum: 1, maximum: 60000, description: "最多读取多少字符，默认 40000" },
        },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description: "把完整内容写入书目录内的文本文件，父目录会自动创建。",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "相对书目录的路径" },
          content: { type: "string", description: "完整文件内容（不是补丁）" },
        },
        required: ["path", "content"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "list_dir",
      description: "列出书目录内某个目录的文件与子目录。",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "相对书目录的目录，默认 ." },
        },
      },
    },
  },
];

function insideRoot(root, target) {
  const rel = relative(root, target);
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel));
}

function realInside(root, target) {
  let probe = target;
  while (!existsSync(probe)) {
    const parent = dirname(probe);
    if (parent === probe) break;
    probe = parent;
  }
  try {
    const realProbe = realpathSync(probe);
    const realRoot = realpathSync(root);
    return insideRoot(realRoot, realProbe);
  } catch {
    return false;
  }
}

function fail(code, message) {
  return { ok: false, content: `ERROR[${code}] ${message}` };
}

export function createToolExecutor({
  bookDir,
  readRoots = [],
  maxReadChars = 60000,
  maxWriteBytes = 2_000_000,
} = {}) {
  const root = resolve(bookDir);
  const readableRoots = [root, ...readRoots.map((entry) => resolve(entry))];

  function resolvePath(input) {
    if (typeof input !== "string" || !input.trim()) throw new Error("path 不能为空");
    const target = resolve(root, input);
    if (!insideRoot(root, target)) throw new Error(`路径越界：${input}`);
    if (!realInside(root, target)) throw new Error(`路径经由符号链接指向书目录外：${input}`);
    return target;
  }

  function resolveReadPath(input) {
    if (typeof input !== "string" || !input.trim()) throw new Error("path 不能为空");
    const candidates = isAbsolute(input)
      ? [resolve(input)]
      : [resolve(root, input), ...readableRoots.slice(1).map((readRoot) => resolve(readRoot, input))];
    for (const candidate of candidates) {
      if (!existsSync(candidate)) continue;
      for (const allowed of readableRoots) {
        if (insideRoot(allowed, candidate) && realInside(allowed, candidate)) return candidate;
      }
    }
    throw new Error(`路径不在书目录或只读参考目录内：${input}`);
  }

  function relativePosix(target) {
    return relative(root, target).split(sep).join("/");
  }

  function backupExisting(target, rel) {
    if (!normalizedRel(rel).startsWith("正文/") || !existsSync(target)) return;
    try {
      const backupDir = join(root, "_pipeline", "backups", "正文");
      mkdirSync(backupDir, { recursive: true });
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      const name = rel.split("/").at(-1);
      copyFileSync(target, join(backupDir, `${name}.${stamp}.bak`));
      const files = readdirSync(backupDir).filter((entry) => entry.endsWith(".bak")).sort();
      for (const old of files.slice(0, Math.max(0, files.length - BACKUP_LIMIT))) {
        unlinkSync(join(backupDir, old));
      }
    } catch {
      // 备份失败不应阻断写作，但保护名单仍然生效
    }
  }

  function readFile(args) {
    let target;
    try {
      target = resolveReadPath(args?.path);
    } catch (error) {
      return fail("PATH", error.message);
    }
    if (!existsSync(target) || !statSync(target).isFile()) return fail("NOT_FOUND", `文件不存在：${args.path}`);
    const ext = extname(target).toLowerCase();
    if (!TEXT_EXTENSIONS.has(ext)) return fail("TYPE", `只允许读取文本文件：${ext || "(无扩展名)"}`);
    const text = readFileSync(target, "utf8");
    const offset = Number.isInteger(args.offset) && args.offset > 0 ? args.offset : 0;
    const limit = Math.min(Number.isInteger(args.limit) && args.limit > 0 ? args.limit : 40000, maxReadChars);
    const slice = text.slice(offset, offset + limit);
    const truncated = offset + slice.length < text.length;
    return {
      ok: true,
      content: truncated
        ? `${slice}\n\n…[已截断：共 ${text.length} 字符，本次 ${offset}–${offset + slice.length}]`
        : slice,
    };
  }

  function writeFile(args) {
    let target;
    try {
      target = resolvePath(args?.path);
    } catch (error) {
      return fail("PATH", error.message);
    }
    const rel = relativePosix(target);
    if (isProtected(rel)) return fail("PROTECTED", `禁止改写控制/关键产物：${rel}`);
    const ext = extname(target).toLowerCase();
    if (!TEXT_EXTENSIONS.has(ext)) return fail("TYPE", `只允许写入文本文件：${ext || "(无扩展名)"}`);
    if (typeof args?.content !== "string") return fail("CONTENT", "content 必须是字符串");
    const bytes = Buffer.byteLength(args.content, "utf8");
    if (bytes > maxWriteBytes) return fail("TOO_LARGE", `内容 ${bytes} 字节超过上限 ${maxWriteBytes}`);
    try {
      backupExisting(target, rel);
      mkdirSync(dirname(target), { recursive: true });
      const temp = `${target}.${process.pid}.${Date.now()}.tmp`;
      writeFileSync(temp, args.content, "utf8");
      renameSync(temp, target);
    } catch (error) {
      return fail("WRITE", `写入失败：${error.message}`);
    }
    return { ok: true, content: `已写入 ${rel}（${bytes} 字节）` };
  }

  function listDir(args) {
    let target;
    try {
      target = resolveReadPath(args?.path ?? ".");
    } catch (error) {
      return fail("PATH", error.message);
    }
    if (!existsSync(target) || !statSync(target).isDirectory()) return fail("NOT_FOUND", `目录不存在：${args?.path ?? "."}`);
    const entries = readdirSync(target, { withFileTypes: true })
      .sort((left, right) => Number(right.isDirectory()) - Number(left.isDirectory()) || left.name.localeCompare(right.name))
      .slice(0, 200);
    const lines = entries.map((entry) => {
      if (entry.isDirectory()) return `${entry.name}/`;
      try {
        return `${entry.name}  (${statSync(join(target, entry.name)).size} B)`;
      } catch {
        return `${entry.name}`;
      }
    });
    return { ok: true, content: lines.length ? lines.join("\n") : "(空目录)" };
  }

  function execute(name, args) {
    if (name === "read_file") return readFile(args ?? {});
    if (name === "write_file") return writeFile(args ?? {});
    if (name === "list_dir") return listDir(args ?? {});
    return fail("UNKNOWN_TOOL", `未知工具：${name}`);
  }

  return { execute, definitions: TOOL_DEFINITIONS };
}
