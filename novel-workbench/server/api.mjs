import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import {
  HttpError, assertLocalRequest, errorPayload, readJsonBody, sendJson, serveStaticFile,
} from "./http.mjs";
import { syncAll, syncBook } from "./sync.mjs";
import { loadAgentConfig, redactSecrets } from "./agent-config.mjs";
import { SKILL_DIR } from "./paths.mjs";
import { resolveRuntimePaths } from "./agent/runtime.mjs";
import { buildChapterReport } from "./gate-report.mjs";
import { hasStepToken, RunNeedsResumeError } from "./run-manager.mjs";
import { SCHEMA_VERSION } from "./schema.mjs";
import { effectiveSettings, saveSettings, SettingsError } from "./settings.mjs";
import { workbenchVersion } from "./version.mjs";
import { SecretError, deleteSecret, secretInfo, setSecret } from "./secrets.mjs";
import {
  ContentConflictError, ContentError, readChapterContent, saveChapterContent,
} from "./content.mjs";
import { diffStats, lineDiff } from "./diff.mjs";
import {
  getVersion, listVersions, recordVersion, restoreVersion, versionContent,
} from "./versions.mjs";
import {
  StoryConflictError, foreshadowItems, readStoryDoc, saveStoryDoc, storyIndex, timelineDocs,
  upsertStoryIndex,
} from "./story.mjs";
import {
  appendSubmissionEvent, packageSubmission, preflightSubmission, readSubmissionEvents,
} from "./submissions.mjs";

const FINDING_LIMIT = 200;
const RUN_LIMIT = 50;

function runPython(python, args, { cwd, timeoutMs = 120_000, env = {} } = {}) {
  return new Promise((accept) => {
    execFile(
      python,
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

function parseJson(text, fallback = null) {
  try {
    return JSON.parse(text);
  } catch {
    return fallback;
  }
}

function bookSummary(row) {
  return {
    id: row.id,
    path: row.path,
    title: row.title,
    genre: row.genre,
    target_words: row.target_words,
    max_chapters: row.max_chapters,
    cursor: row.cursor,
    current_chapter: row.current_chapter,
    chapters_committed: row.chapters_committed,
    words_total: row.words_total,
    blocked_reason: row.blocked_reason,
    finished: Boolean(row.finished),
    fix_rounds: parseJson(row.fix_rounds_json ?? "{}", {}),
    n20_rounds: row.n20_rounds,
    llm_rounds: row.llm_rounds,
    created_at: row.created_at,
    synced_at: row.synced_at,
    chapter_count: row.chapter_count,
    active_findings: row.active_findings,
    blocking_findings: row.blocking_findings,
  };
}

function createBookSkeleton(bookPath, { title, genre, targetWords, maxChapters, oneLine }) {
  for (const sub of ["正文", "大纲", "设定", "追踪", "投稿包", "_pipeline"]) {
    mkdirSync(join(bookPath, sub), { recursive: true });
  }
  const state = {
    schema_version: 2,
    book: title,
    genre,
    target_words: targetWords,
    max_chapters: maxChapters,
    created_at: new Date().toISOString(),
    current_chapter: 0,
    chapters_committed: 0,
    words_total: 0,
    cursor: "N01",
    nodes: {},
    decisions_taken: [],
    pending_questions: [],
    blocked_reason: null,
    relay_count: 0,
    one_line: oneLine || null,
  };
  const target = join(bookPath, "_pipeline", "state.json");
  const temp = `${target}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(temp, `${JSON.stringify(state, null, 1)}\n`, "utf8");
  renameSync(temp, target);
}

function sanitizeFolder(value) {
  return String(value ?? "")
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "")
    .replace(/^[. ]+|[. ]+$/g, "")
    .slice(0, 60);
}

function isInside(root, target) {
  const rel = relative(root, target);
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel));
}

const SECRET_KEY = /^(api_?key|secret|password|access_token|token)$/i;
const WINDOWS_RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;

function assertNoSecrets(value, path = "config") {
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    if (SECRET_KEY.test(key)) {
      throw new HttpError(400, "secret_not_allowed", `${path}.${key} 不允许写入配置文件；密钥请用环境变量`);
    }
    assertNoSecrets(child, `${path}.${key}`);
  }
}

function readSubmissions(bookPath) {
  const file = join(bookPath, "_pipeline", "submissions.json");
  if (!existsSync(file)) return [];
  const doc = parseJson(readFileSync(file, "utf8"), null);
  if (Array.isArray(doc)) return doc;
  return Array.isArray(doc?.items) ? doc.items : [];
}

function writeSubmissions(bookPath, items) {
  const file = join(bookPath, "_pipeline", "submissions.json");
  mkdirSync(join(bookPath, "_pipeline"), { recursive: true });
  const temp = `${file}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(temp, `${JSON.stringify({ schema_version: 1, items }, null, 2)}\n`, "utf8");
  renameSync(temp, file);
}

function textField(body, key, maxLength) {
  const value = body?.[key];
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") throw new HttpError(400, "invalid_field", `${key} 必须是字符串`);
  if (value.length > maxLength) throw new HttpError(400, "invalid_field", `${key} 超过 ${maxLength} 字`);
  return value.trim() || null;
}

export function createApi({
  db, roots: rootOverride = null, runManager, dataDir, allowNetwork = false, networkToken = null,
  webDir = join(SKILL_DIR, "web"),
}) {
  const rootsFor = () => {
    if (rootOverride?.length) return rootOverride;
    const settings = effectiveSettings({ dataDir });
    const valid = settings.books_roots.filter((root, index) => settings.exists?.books_roots?.[index]);
    if (valid.length) return valid;
    const fallback = join(homedir(), "Documents", "NovelWorkbench", "books");
    try {
      mkdirSync(fallback, { recursive: true });
    } catch {}
    return [fallback];
  };
  const bookSelect = `
    SELECT b.*,
      (SELECT COUNT(*) FROM chapters c WHERE c.book_id = b.id) AS chapter_count,
      (SELECT COUNT(*) FROM findings f WHERE f.book_id = b.id AND f.active = 1) AS active_findings,
      (SELECT COUNT(*) FROM findings f WHERE f.book_id = b.id AND f.active = 1 AND f.blocking = 1) AS blocking_findings
    FROM books b
  `;

  function getBookRow(id) {
    const numeric = Number(id);
    if (!Number.isInteger(numeric) || numeric < 1) throw new HttpError(400, "invalid_book", "书 id 必须是正整数");
    const row = db.prepare(`${bookSelect} WHERE b.id = ?`).get(numeric);
    if (!row) throw new HttpError(404, "book_not_found", "书不存在，先执行一次同步");
    return row;
  }

  function refresh(row) {
    try {
      syncBook(db, row.path);
      return { row: db.prepare(`${bookSelect} WHERE b.id = ?`).get(row.id), warning: null };
    } catch (error) {
      return { row, warning: `同步失败：${error.message}` };
    }
  }

  async function handleBooks(request, response, url) {
    if (request.method === "GET") {
      const roots = rootsFor();
      const summary = syncAll(db, roots);
      const rows = db.prepare(`${bookSelect} ORDER BY b.title, b.id`).all();
      sendJson(response, 200, {
        books: rows.map((row) => ({ ...bookSummary(row), sync_warning: null })),
        roots,
        data_dir: dataDir,
        sync: { discovered: summary.discovered, synced: summary.synced, skipped: summary.skipped, errors: summary.errors },
      });
      return;
    }
    if (request.method === "POST") {
      const body = await readJsonBody(request);
      const title = textField(body, "title", 80);
      if (!title) throw new HttpError(400, "invalid_title", "title 不能为空");
      const oneLine = textField(body, "one_line", 200);
      const genre = body.genre ?? "niandai";
      if (typeof genre !== "string" || !/^[a-z][a-z0-9_-]{1,23}$/.test(genre)) {
        throw new HttpError(400, "invalid_genre", "genre 只能是小写字母开头的标识符");
      }
      const targetWords = body.target_words ?? 20000;
      if (!Number.isInteger(targetWords) || targetWords < 5000 || targetWords > 2_000_000) {
        throw new HttpError(400, "invalid_target_words", "target_words 必须是 5000–2000000 的整数");
      }
      const maxChapters = body.max_chapters ?? 12;
      if (!Number.isInteger(maxChapters) || maxChapters < 1 || maxChapters > 500) {
        throw new HttpError(400, "invalid_max_chapters", "max_chapters 必须是 1–500 的整数");
      }
      const root = rootsFor()[0];
      if (!root) throw new HttpError(400, "books_root_missing", "未配置书库根目录，请先在路径设置里指定");
      const folder = sanitizeFolder(body.folder || title);
      if (!folder) throw new HttpError(400, "invalid_folder", "无法从书名得到合法目录名，请显式传 folder");
      if (WINDOWS_RESERVED.test(folder)) {
        throw new HttpError(400, "invalid_folder", `目录名是 Windows 保留名：${folder}`);
      }
      const bookPath = resolve(root, folder);
      if (!isInside(root, bookPath) || bookPath === resolve(root)) {
        throw new HttpError(400, "invalid_folder", "folder 越界");
      }
      if (existsSync(bookPath)) throw new HttpError(409, "book_exists", `目录已存在：${bookPath}`);

      mkdirSync(root, { recursive: true });
      let engineReady = true;
      let paths = null;
      try {
        paths = resolveRuntimePaths({ dataDir });
      } catch {
        engineReady = false;
      }
      if (engineReady) {
        const init = await runPython(paths.python, [
          paths.autopilot, "init",
          "--title", title,
          "--genre", genre,
          "--target-words", String(targetWords),
          "--max-chapters", String(maxChapters),
          "--book", bookPath,
        ], {
          cwd: root,
          timeoutMs: 180_000,
          env: { WORKBENCH_CONFIG: paths.configPath },
        });
        if (init.code !== 0) {
          throw new HttpError(400, "init_failed", "autopilot init 失败", {
            exit_code: init.code,
            output: `${init.stdout}${init.stderr}`.slice(-1200),
          });
        }
      } else {
        createBookSkeleton(bookPath, { title, genre, targetWords, maxChapters, oneLine });
      }
      syncBook(db, bookPath, { force: true });
      const row = db.prepare(`${bookSelect} WHERE b.path = ?`).get(bookPath);
      recordEvent(row.id, "book_created", { engine: engineReady });
      sendJson(response, 201, { book: bookSummary(row), engine_ready: engineReady });
      return;
    }
    throw new HttpError(405, "method_not_allowed", "不支持的方法");
  }

  async function handleBookDetail(request, response, url, id) {
    const base = getBookRow(id);
    const { row, warning } = refresh(base);
    const chapters = db.prepare(
      "SELECT number, title, file_path, words, committed, committed_at FROM chapters WHERE book_id = ? ORDER BY number",
    ).all(row.id);
    const nodes = db.prepare(
      "SELECT node_id, chapter, status, attempts, happened_at, output_excerpt FROM nodes WHERE book_id = ? ORDER BY node_id",
    ).all(row.id);
    const findings = db.prepare(`
      SELECT id, scope, chapter, source, criterion, name, level, blocking, quote, file_path, fix, note,
             first_seen_at, last_seen_at, resolved_at, active
      FROM findings WHERE book_id = ?
      ORDER BY active DESC, blocking DESC, chapter DESC, id DESC
      LIMIT ?
    `).all(row.id, FINDING_LIMIT);
    const judgments = db.prepare(
      "SELECT node_id, verdict, summary, recorded_at FROM judgments WHERE book_id = ? ORDER BY recorded_at DESC",
    ).all(row.id);
    sendJson(response, 200, {
      book: bookSummary(row),
      sync_warning: warning,
      chapters,
      nodes,
      findings,
      judgments,
    });
  }

  async function handleReport(request, response, url, id) {
    const row = getBookRow(id);
    const chapter = Number(url.searchParams.get("chapter"));
    const report = await buildChapterReport(row.path, chapter, { genre: row.genre, dataDir });
    sendJson(response, 200, report);
  }

  async function handleRuns(request, response, url, id) {
    const row = getBookRow(id);
    const rows = db.prepare(`
      SELECT id, action, exit_code, node, kind, requested_at, executed_at, result_json,
             run_id, status, events_json
      FROM runs WHERE book_id = ? ORDER BY requested_at DESC LIMIT ?
    `).all(row.id, RUN_LIMIT);
    sendJson(response, 200, {
      runs: rows.map((run) => ({
        id: run.id,
        run_id: run.run_id,
        status: run.status,
        action: run.action,
        exit_code: run.exit_code,
        node: run.node,
        kind: run.kind,
        requested_at: run.requested_at,
        executed_at: run.executed_at,
        result: parseJson(run.result_json ?? "null", null),
        events: parseJson(run.events_json ?? "[]", []),
      })),
    });
  }

  async function handleRunStart(request, response, url, id) {
    const row = getBookRow(id);
    if (runManager.active(row.id)) throw new HttpError(409, "run_running", "这本书已有一次执行在进行");
    if (hasStepToken(row.path)) {
      throw new HttpError(409, "needs_resume", "上次执行被中断，请先点「恢复并继续」");
    }
    const body = await readJsonBody(request);
    const mode = body.mode === "mock" ? "mock" : "real";
    let maxSteps = Infinity;
    if (body.max_steps !== undefined) {
      if (!Number.isInteger(body.max_steps) || body.max_steps < 1 || body.max_steps > 1000) {
        throw new HttpError(400, "invalid_max_steps", "max_steps 必须是 1–1000 的整数");
      }
      maxSteps = body.max_steps;
    }
    const loaded = loadAgentConfig({ bookDir: row.path, dataDir });
    if (loaded.errors.length) {
      throw new HttpError(400, "invalid_agent_config", "Agent 配置有误", { errors: loaded.errors });
    }
    const config = structuredClone(loaded.config);
    if (mode === "mock") {
      for (const role of Object.keys(config.roles ?? {})) {
        config.roles[role].provider = "mock";
        config.roles[role].model = `mock-${role}`;
      }
    }
    let snapshot;
    try {
      snapshot = runManager.start({
        bookId: row.id,
        bookPath: row.path,
        config,
        mode,
        maxSteps,
      });
    } catch (error) {
      if (error instanceof RunNeedsResumeError) {
        throw new HttpError(409, "needs_resume", error.message);
      }
      if (error.code === "run_running") throw new HttpError(409, "run_running", error.message);
      throw error;
    }
    sendJson(response, 202, { run: snapshot, warnings: loaded.warnings });
  }

  async function handleResume(request, response, url, id) {
    const row = getBookRow(id);
    if (runManager.active(row.id)) throw new HttpError(409, "run_running", "执行进行中，不能恢复");
    const paths = resolveRuntimePaths({ dataDir });
    const result = await runPython(
      paths.python,
      [paths.autopilot, "resume", "--book", row.path],
      {
        cwd: row.path,
        timeoutMs: 60_000,
        env: { WORKBENCH_CONFIG: paths.configPath },
      },
    );
    if (result.code !== 0) {
      throw new HttpError(400, "resume_failed", "恢复失败", {
        output: `${result.stdout}${result.stderr}`.slice(-800),
      });
    }
    syncBook(db, row.path, { force: true });
    sendJson(response, 200, {
      resumed: true,
      book: bookSummary(db.prepare(`${bookSelect} WHERE b.id = ?`).get(row.id)),
    });
  }

  async function handleRunStatus(request, response, url, id) {
    const row = getBookRow(id);
    const since = Number(url.searchParams.get("since") ?? 0);
    const snapshot = runManager.snapshot(row.id, Number.isInteger(since) && since > 0 ? since : 0);
    if (snapshot) {
      sendJson(response, 200, { run: snapshot, needs_resume: hasStepToken(row.path) });
      return;
    }
    const last = db.prepare(`
      SELECT id, action, exit_code, node, kind, requested_at, executed_at, result_json,
             run_id, status, events_json
      FROM runs WHERE book_id = ? ORDER BY requested_at DESC LIMIT 1
    `).get(row.id);
    sendJson(response, 200, {
      run: null,
      needs_resume: hasStepToken(row.path),
      last: last
        ? {
          id: last.id,
          run_id: last.run_id,
          status: last.status,
          action: last.action,
          exit_code: last.exit_code,
          node: last.node,
          kind: last.kind,
          requested_at: last.requested_at,
          executed_at: last.executed_at,
          result: parseJson(last.result_json ?? "null", null),
          events: parseJson(last.events_json ?? "[]", []),
        }
        : null,
    });
  }

  async function handleRunStop(request, response, url, id) {
    const row = getBookRow(id);
    const stopped = runManager.stop(row.id);
    if (!stopped) throw new HttpError(409, "run_not_active", "这本书当前没有进行中的执行");
    sendJson(response, 202, { stopping: true });
  }

  async function handleSubmissions(request, response, url, id) {
    const row = getBookRow(id);
    if (request.method === "GET") {
      syncBook(db, row.path);
      const items = db.prepare(`
        SELECT external_id, channel, submitted_at, editor, status, feedback, next_action, created_at,
               manuscript_version, package_path, editor_contact, expected_reply_at, replied_at, result
        FROM submissions WHERE book_id = ? ORDER BY COALESCE(submitted_at, created_at) DESC, id DESC
      `).all(row.id);
      sendJson(response, 200, { submissions: items });
      return;
    }
    if (request.method === "POST") {
      const body = await readJsonBody(request);
      const items = readSubmissions(row.path);
      const entry = {
        id: `sub-${randomUUID()}`,
        channel: textField(body, "channel", 60),
        submitted_at: textField(body, "submitted_at", 40) ?? new Date().toISOString(),
        editor: textField(body, "editor", 60),
        status: textField(body, "status", 40) ?? "已投稿",
        feedback: textField(body, "feedback", 20000),
        next_action: textField(body, "next_action", 500),
        manuscript_version: textField(body, "manuscript_version", 120),
        package_path: textField(body, "package_path", 500),
        editor_contact: textField(body, "editor_contact", 200),
        expected_reply_at: textField(body, "expected_reply_at", 40),
        replied_at: textField(body, "replied_at", 40),
        result: textField(body, "result", 60),
        events: [{ at: new Date().toISOString(), type: "created", note: "创建投稿记录" }],
        created_at: new Date().toISOString(),
      };
      items.push(entry);
      writeSubmissions(row.path, items);
      syncBook(db, row.path, { force: true });
      recordEvent(row.id, "submission_sent", { submission: entry.id, channel: entry.channel });
      sendJson(response, 201, { submission: entry });
      return;
    }
    throw new HttpError(405, "method_not_allowed", "不支持的方法");
  }

  async function handleSubmissionUpdate(request, response, url, id, externalId) {
    const row = getBookRow(id);
    const body = await readJsonBody(request);
    const items = readSubmissions(row.path);
    const index = items.findIndex((item) => String(item.id) === externalId);
    if (index < 0) throw new HttpError(404, "submission_not_found", "投稿记录不存在");
    const fields = {
      channel: textField(body, "channel", 60),
      submitted_at: textField(body, "submitted_at", 40),
      editor: textField(body, "editor", 60),
      status: textField(body, "status", 40),
      feedback: textField(body, "feedback", 20000),
      next_action: textField(body, "next_action", 500),
      manuscript_version: textField(body, "manuscript_version", 120),
      package_path: textField(body, "package_path", 500),
      editor_contact: textField(body, "editor_contact", 200),
      expected_reply_at: textField(body, "expected_reply_at", 40),
      replied_at: textField(body, "replied_at", 40),
      result: textField(body, "result", 60),
    };
    for (const [key, value] of Object.entries(fields)) {
      if (value !== null) items[index][key] = value;
    }
    writeSubmissions(row.path, items);
    syncBook(db, row.path, { force: true });
    sendJson(response, 200, { submission: items[index] });
  }

  function agentPayload(bookId) {
    const bookDir = bookId ? getBookRow(bookId).path : null;
    const secrets = secretInfo({ dataDir });
    const result = loadAgentConfig({
      bookDir,
      dataDir,
      secretNames: new Set(secrets.names.map((item) => item.name)),
    });
    return {
      scope: bookId ? "book" : "global",
      book_id: bookId ?? null,
      config: redactSecrets(result.config),
      errors: result.errors,
      warnings: result.warnings,
      providers: result.providers,
      base_path: result.basePath,
      global_path: result.globalPath,
      override_path: result.overridePath,
      secrets,
    };
  }

  async function handleProviderSecret(request, response, url, providerName) {
    const merged = loadAgentConfig({ dataDir });
    const provider = merged.config.providers?.[providerName];
    if (!provider) throw new HttpError(404, "provider_not_found", `provider 不存在：${providerName}`);
    if (provider.type !== "openai") {
      throw new HttpError(400, "provider_not_supported", "只有 openai 兼容 provider 需要密钥");
    }
    if (!provider.api_key_env) {
      throw new HttpError(400, "api_key_env_missing", "该 provider 未声明 api_key_env");
    }
    try {
      if (request.method === "PUT") {
        const body = await readJsonBody(request);
        if (typeof body.api_key !== "string" || !body.api_key.trim()) {
          throw new HttpError(400, "invalid_secret", "api_key 不能为空");
        }
        if (body.api_key.length > 4096) throw new HttpError(400, "invalid_secret", "api_key 过长");
        const result = setSecret(provider.api_key_env, body.api_key, { dataDir });
        sendJson(response, 200, { provider: providerName, ...result });
        return;
      }
      if (request.method === "DELETE") {
        const result = deleteSecret(provider.api_key_env, { dataDir });
        sendJson(response, 200, { provider: providerName, ...result });
        return;
      }
      throw new HttpError(405, "method_not_allowed", "不支持的方法");
    } catch (error) {
      if (error instanceof SecretError) throw new HttpError(400, "secret_error", error.message);
      throw error;
    }
  }

  async function handleAgents(request, response, url) {
    if (request.method === "GET") {
      const bookId = url.searchParams.get("book");
      sendJson(response, 200, agentPayload(bookId ? Number(bookId) : null));
      return;
    }
    if (request.method === "PUT") {
      const body = await readJsonBody(request);
      const scope = body.scope === "book" ? "book" : "global";
      const config = body.config;
      if (!config || typeof config !== "object" || Array.isArray(config)) {
        throw new HttpError(400, "invalid_config", "config 必须是对象");
      }
      assertNoSecrets(config);
      let targetPath = join(dataDir, "agents.json");
      let bookDir = null;
      if (scope === "book") {
        const bookId = Number(body.book_id);
        if (!Number.isInteger(bookId) || bookId < 1) throw new HttpError(400, "invalid_book", "book_id 必须是正整数");
        bookDir = getBookRow(bookId).path;
        targetPath = join(bookDir, "_pipeline", "agents.json");
      }
      const previous = existsSync(targetPath) ? readFileSync(targetPath, "utf8") : null;
      mkdirSync(dirname(targetPath), { recursive: true });
      const temp = `${targetPath}.${process.pid}.${Date.now()}.tmp`;
      writeFileSync(temp, `${JSON.stringify(config, null, 2)}\n`, "utf8");
      renameSync(temp, targetPath);
      const check = loadAgentConfig(bookDir ? { bookDir, dataDir } : { dataDir });
      if (check.errors.length) {
        if (previous === null) {
          try {
            unlinkSync(targetPath);
          } catch {}
        } else {
          writeFileSync(targetPath, previous, "utf8");
        }
        throw new HttpError(400, "invalid_config", "配置未通过校验", { errors: check.errors });
      }
      sendJson(response, 200, agentPayload(bookDir ? Number(body.book_id) : null));
      return;
    }
    throw new HttpError(405, "method_not_allowed", "不支持的方法");
  }

  function recordEvent(bookId, type, payload = null) {
    try {
      db.prepare(`
        INSERT INTO analytics_events (book_id, type, payload_json, created_at)
        VALUES (?, ?, ?, ?)
      `).run(bookId, type, payload ? JSON.stringify(payload) : null, new Date().toISOString());
    } catch {}
  }

  function chapterRow(bookId, chapter) {
    return db.prepare("SELECT * FROM chapters WHERE book_id = ? AND number = ?").get(bookId, chapter) ?? null;
  }

  async function handleChapterContent(request, response, url, id, chapter) {
    const row = getBookRow(id);
    if (request.method === "GET") {
      let file;
      try {
        file = readChapterContent(row.path, chapter);
      } catch (error) {
        if (error instanceof ContentError) throw new HttpError(400, error.code, error.message);
        throw error;
      }
      const meta = chapterRow(row.id, chapter);
      sendJson(response, 200, {
        chapter,
        ...file,
        title: meta?.title ?? null,
        committed: Boolean(meta?.committed),
        dirty: Boolean(meta?.dirty),
        author_state: meta?.author_state ?? null,
        versions: listVersions(db, row.id, chapter).slice(0, 20),
      });
      return;
    }
    if (request.method !== "PUT") throw new HttpError(405, "method_not_allowed", "不支持的方法");
    const body = await readJsonBody(request);
    if (typeof body.content !== "string") throw new HttpError(400, "invalid_content", "content 必须是字符串");
    const meta = chapterRow(row.id, chapter);
    if (meta && body.base_sha === undefined) {
      throw new HttpError(409, "base_sha_required", "保存已存在的章节必须带 base_sha");
    }
    try {
      const before = readChapterContent(row.path, chapter);
      const saved = saveChapterContent({
        bookDir: row.path,
        chapter,
        content: body.content,
        baseSha: body.base_sha ?? null,
        title: body.title ?? meta?.title ?? "未命名",
        committed: Boolean(meta?.committed),
        allowCommittedEdit: body.allow_committed_edit === true,
      });
      const changed = before.content !== saved.content;
      if (before.exists && changed) {
        recordVersion(db, {
          bookDir: row.path, bookId: row.id, chapter, content: before.content,
          source: "manual-before", kind: "manual-before",
        });
      }
      const version = recordVersion(db, {
        bookDir: row.path, bookId: row.id, chapter, content: saved.content,
        source: "manual", kind: "manual",
      });
      syncBook(db, row.path, { force: true });
      const dirty = Boolean(meta?.committed) && changed ? 1 : 0;
      db.prepare(`
        UPDATE chapters SET dirty = ?, author_state = ?, current_version_id = ?
        WHERE book_id = ? AND number = ?
      `).run(
        dirty,
        meta?.committed ? (dirty ? "draft_edit" : "committed") : "draft",
        version?.id ?? null,
        row.id,
        chapter,
      );
      if (dirty) recordEvent(row.id, "chapter_dirty", { chapter });
      recordEvent(row.id, "chapter_saved", { chapter, words: saved.words });
      const after = readChapterContent(row.path, chapter);
      sendJson(response, 200, {
        chapter,
        ...after,
        title: chapterRow(row.id, chapter)?.title ?? null,
        committed: Boolean(meta?.committed),
        dirty: Boolean(dirty),
        author_state: dirty ? "draft_edit" : meta?.committed ? "committed" : "draft",
        version,
      });
      return;
    } catch (error) {
      if (error instanceof ContentConflictError) {
        throw new HttpError(409, "content_conflict", "正文已被其他窗口修改", {
          current_sha: error.current.sha256,
          words: error.current.words,
          preview: error.current.content.slice(0, 120),
        });
      }
      if (error instanceof ContentError) throw new HttpError(400, error.code, error.message);
      throw error;
    }
  }

  async function handleManuscript(request, response, url, id) {
    const row = getBookRow(id);
    refresh(row);
    const chapters = db.prepare(`
      SELECT number, title, file_path, words, committed, dirty, author_state, committed_at
      FROM chapters WHERE book_id = ? ORDER BY number
    `).all(row.id);
    const versions = db.prepare(`
      SELECT chapter, COUNT(*) AS count, MAX(created_at) AS last_at
      FROM versions WHERE book_id = ? GROUP BY chapter
    `).all(row.id);
    const versionMap = new Map(versions.map((item) => [item.chapter, item]));
    sendJson(response, 200, {
      chapters: chapters.map((chapter) => ({
        ...chapter,
        versions: versionMap.get(chapter.number)?.count ?? 0,
        last_version_at: versionMap.get(chapter.number)?.last_at ?? null,
      })),
      next_chapter: Math.max(1, Number(row.current_chapter) + 1),
    });
  }

  async function handleVersionList(request, response, url, id) {
    const row = getBookRow(id);
    const chapter = Number(url.searchParams.get("chapter"));
    if (!Number.isInteger(chapter) || chapter < 1) {
      throw new HttpError(400, "invalid_chapter", "chapter 必须是正整数");
    }
    sendJson(response, 200, { chapter, versions: listVersions(db, row.id, chapter) });
  }

  async function handleVersionContent(request, response, url, id, versionId) {
    const row = getBookRow(id);
    const version = getVersion(db, row.id, Number(versionId));
    if (!version) throw new HttpError(404, "version_not_found", "版本不存在");
    const content = versionContent(row.path, version);
    sendJson(response, 200, { version, content });
  }

  async function handleVersionDiff(request, response, url, id, leftId, rightId) {
    const row = getBookRow(id);
    const left = getVersion(db, row.id, Number(leftId));
    const right = getVersion(db, row.id, Number(rightId));
    if (!left || !right) throw new HttpError(404, "version_not_found", "版本不存在");
    const lines = lineDiff(versionContent(row.path, left), versionContent(row.path, right));
    sendJson(response, 200, {
      left: { id: left.id, source: left.source, created_at: left.created_at, words: left.words },
      right: { id: right.id, source: right.source, created_at: right.created_at, words: right.words },
      lines,
      stats: diffStats(lines),
    });
  }

  async function handleVersionRestore(request, response, url, id, versionId) {
    const row = getBookRow(id);
    const version = getVersion(db, row.id, Number(versionId));
    if (!version) throw new HttpError(404, "version_not_found", "版本不存在");
    try {
      const restored = restoreVersion(db, {
        bookDir: row.path,
        bookId: row.id,
        chapter: version.chapter,
        versionId: version.id,
      });
      syncBook(db, row.path, { force: true });
      db.prepare("UPDATE chapters SET dirty = 1, author_state = 'draft_edit' WHERE book_id = ? AND number = ?")
        .run(row.id, version.chapter);
      recordEvent(row.id, "version_restored", { chapter: version.chapter, version: version.id });
      sendJson(response, 200, {
        chapter: version.chapter,
        content: restored.content,
        sha256: restored.sha256,
        words: restored.words,
        version: restored.version,
        dirty: true,
      });
      return;
    } catch (error) {
      if (error instanceof ContentConflictError) {
        throw new HttpError(409, "content_conflict", "正文已被其他窗口修改，无法回滚");
      }
      throw error;
    }
  }

  async function handleStory(request, response, url, id) {
    const row = getBookRow(id);
    if (request.method === "GET") {
      const index = storyIndex(row.path);
      upsertStoryIndex(db, row.id, index);
      sendJson(response, 200, {
        ...index,
        foreshadow: foreshadowItems(row.path),
        timeline: timelineDocs(row.path),
      });
      return;
    }
    throw new HttpError(405, "method_not_allowed", "不支持的方法");
  }

  async function handleStoryDoc(request, response, url, id) {
    const row = getBookRow(id);
    if (request.method === "GET") {
      const path = url.searchParams.get("path") || "";
      sendJson(response, 200, readStoryDoc(row.path, path));
      return;
    }
    if (request.method !== "PUT") throw new HttpError(405, "method_not_allowed", "不支持的方法");
    const body = await readJsonBody(request);
    if (typeof body.path !== "string" || !body.path) throw new HttpError(400, "invalid_path", "path 不能为空");
    if (!/^(设定|大纲|追踪)\//.test(body.path) || !body.path.endsWith(".md")) {
      throw new HttpError(400, "invalid_path", "只允许编辑 设定/大纲/追踪 下的 Markdown");
    }
    if (typeof body.content !== "string") throw new HttpError(400, "invalid_content", "content 必须是字符串");
    try {
      const saved = saveStoryDoc({
        bookPath: row.path,
        relPath: body.path,
        content: body.content,
        baseSha: body.base_sha ?? null,
      });
      upsertStoryIndex(db, row.id, storyIndex(row.path));
      recordEvent(row.id, "story_saved", { path: body.path });
      sendJson(response, 200, saved);
      return;
    } catch (error) {
      if (error instanceof StoryConflictError) {
        throw new HttpError(409, "story_conflict", "设定已被其他窗口修改", {
          current_sha: error.current.sha256,
        });
      }
      throw error;
    }
  }

  async function handleSubmissionPreflight(request, response, url, id) {
    const row = getBookRow(id);
    refresh(row);
    const fresh = db.prepare(`${bookSelect} WHERE b.id = ?`).get(row.id);
    sendJson(response, 200, preflightSubmission(db, fresh));
  }

  async function handleSubmissionPackage(request, response, url, id) {
    const row = getBookRow(id);
    refresh(row);
    const fresh = db.prepare(`${bookSelect} WHERE b.id = ?`).get(row.id);
    const body = await readJsonBody(request);
    try {
      const result = packageSubmission(db, fresh, { channel: body.channel || "七猫" });
      recordEvent(row.id, "submission_packaged", { channel: body.channel || "七猫", path: result.path });
      sendJson(response, 200, result);
      return;
    } catch (error) {
      if (error.code === "preflight_failed") {
        throw new HttpError(400, "preflight_failed", error.message, { checks: error.checks });
      }
      throw error;
    }
  }

  async function handleSubmissionEvents(request, response, url, id, externalId) {
    const row = getBookRow(id);
    if (request.method === "GET") {
      sendJson(response, 200, { events: readSubmissionEvents(row.path, externalId) });
      return;
    }
    if (request.method !== "POST") throw new HttpError(405, "method_not_allowed", "不支持的方法");
    const body = await readJsonBody(request);
    try {
      const events = appendSubmissionEvent(row.path, externalId, {
        type: body.type || "note",
        note: textField(body, "note", 2000) || "",
        at: body.at || undefined,
      });
      syncBook(db, row.path, { force: true });
      recordEvent(row.id, "submission_event", { submission: externalId, type: body.type || "note" });
      sendJson(response, 201, { events });
      return;
    } catch (error) {
      if (error.code === "submission_not_found") throw new HttpError(404, "submission_not_found", error.message);
      throw error;
    }
  }

  function handleStatic(request, response, url) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      throw new HttpError(405, "method_not_allowed", "不支持的方法");
    }
    const relativePath = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    const file = resolve(webDir, relativePath);
    if (!isInside(webDir, file)) throw new HttpError(403, "path_outside_web", "路径越界");
    if (!existsSync(file)) throw new HttpError(404, "not_found", "页面不存在");
    serveStaticFile(response, file);
  }

  return async function handle(request, response) {
    assertLocalRequest(request, { allowNetwork, networkToken });
    const url = new URL(request.url, `http://${request.headers.host ?? "127.0.0.1"}`);
    const path = url.pathname;

    if (path === "/api/health") {
      const settings = effectiveSettings({ dataDir, rootsOverride: rootOverride });
      let aiReady = false;
      try {
        const agents = loadAgentConfig({ dataDir });
        aiReady = (agents.providers ?? []).some((provider) => provider.configured && provider.type !== "mock");
      } catch {}
      sendJson(response, 200, {
        ok: true,
        version: workbenchVersion(),
        schema_version: SCHEMA_VERSION,
        needs_setup: settings.needs_setup,
        data_dir: dataDir,
        roots: rootsFor(),
        books: db.prepare("SELECT COUNT(*) AS n FROM books").get().n,
        engine: {
          python: settings.exists.python,
          story: settings.exists.storyctl,
          ai: aiReady,
        },
      });
      return;
    }
    if (path === "/api/settings") {
      if (request.method === "GET") {
        sendJson(response, 200, {
          settings: effectiveSettings({ dataDir, rootsOverride: rootOverride }),
          version: workbenchVersion(),
          schema_version: SCHEMA_VERSION,
        });
        return;
      }
      if (request.method === "PUT") {
        const body = await readJsonBody(request);
        let settings;
        try {
          settings = saveSettings({ dataDir, patch: body });
        } catch (error) {
          if (error instanceof SettingsError) {
            throw new HttpError(400, "invalid_settings", error.message, { errors: error.errors });
          }
          throw error;
        }
        const sync = syncAll(db, settings.books_roots);
        sendJson(response, 200, {
          settings: effectiveSettings({ dataDir, rootsOverride: rootOverride }),
          sync: { discovered: sync.discovered, synced: sync.synced, skipped: sync.skipped, errors: sync.errors },
          version: workbenchVersion(),
          schema_version: SCHEMA_VERSION,
        });
        return;
      }
      throw new HttpError(405, "method_not_allowed", "不支持的方法");
    }
    if (path === "/api/books") return handleBooks(request, response, url);

    let match = /^\/api\/books\/(\d+)$/.exec(path);
    if (match) {
      if (request.method !== "GET") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleBookDetail(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/sync$/.exec(path);
    if (match) {
      if (request.method !== "POST") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      const row = getBookRow(Number(match[1]));
      try {
        syncBook(db, row.path, { force: true });
      } catch (error) {
        throw new HttpError(400, "sync_failed", `同步失败：${error.message}`);
      }
      sendJson(response, 200, { book: bookSummary(db.prepare(`${bookSelect} WHERE b.id = ?`).get(row.id)) });
      return;
    }
    match = /^\/api\/books\/(\d+)\/report$/.exec(path);
    if (match) {
      if (request.method !== "GET") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleReport(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/runs$/.exec(path);
    if (match) {
      if (request.method !== "GET") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleRuns(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/run$/.exec(path);
    if (match) {
      const id = Number(match[1]);
      if (request.method === "POST") return handleRunStart(request, response, url, id);
      if (request.method === "GET") return handleRunStatus(request, response, url, id);
      throw new HttpError(405, "method_not_allowed", "不支持的方法");
    }
    match = /^\/api\/books\/(\d+)\/resume$/.exec(path);
    if (match) {
      if (request.method !== "POST") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleResume(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/stop$/.exec(path);
    if (match) {
      if (request.method !== "POST") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleRunStop(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/manuscript$/.exec(path);
    if (match) {
      if (request.method !== "GET") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleManuscript(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/chapters\/(\d+)\/content$/.exec(path);
    if (match) {
      return handleChapterContent(request, response, url, Number(match[1]), Number(match[2]));
    }
    match = /^\/api\/books\/(\d+)\/versions$/.exec(path);
    if (match) {
      if (request.method !== "GET") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleVersionList(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/versions\/(\d+)$/.exec(path);
    if (match) {
      if (request.method !== "GET") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleVersionContent(request, response, url, Number(match[1]), Number(match[2]));
    }
    match = /^\/api\/books\/(\d+)\/versions\/(\d+)\/diff\/(\d+)$/.exec(path);
    if (match) {
      if (request.method !== "GET") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleVersionDiff(request, response, url, Number(match[1]), Number(match[2]), Number(match[3]));
    }
    match = /^\/api\/books\/(\d+)\/versions\/(\d+)\/restore$/.exec(path);
    if (match) {
      if (request.method !== "POST") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleVersionRestore(request, response, url, Number(match[1]), Number(match[2]));
    }
    match = /^\/api\/books\/(\d+)\/story$/.exec(path);
    if (match) return handleStory(request, response, url, Number(match[1]));
    match = /^\/api\/books\/(\d+)\/story\/doc$/.exec(path);
    if (match) return handleStoryDoc(request, response, url, Number(match[1]));
    match = /^\/api\/books\/(\d+)\/submissions\/preflight$/.exec(path);
    if (match) {
      if (request.method !== "POST") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleSubmissionPreflight(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/submissions\/package$/.exec(path);
    if (match) {
      if (request.method !== "POST") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      return handleSubmissionPackage(request, response, url, Number(match[1]));
    }
    match = /^\/api\/books\/(\d+)\/submissions\/([^/]+)\/events$/.exec(path);
    if (match) {
      let submissionId;
      try {
        submissionId = decodeURIComponent(match[2]);
      } catch {
        throw new HttpError(400, "invalid_encoding", "路径编码非法");
      }
      return handleSubmissionEvents(request, response, url, Number(match[1]), submissionId);
    }
    match = /^\/api\/books\/(\d+)\/submissions$/.exec(path);
    if (match) return handleSubmissions(request, response, url, Number(match[1]));
    match = /^\/api\/books\/(\d+)\/submissions\/([^/]+)$/.exec(path);
    if (match) {
      if (request.method !== "PUT") throw new HttpError(405, "method_not_allowed", "不支持的方法");
      let externalId;
      try {
        externalId = decodeURIComponent(match[2]);
      } catch {
        throw new HttpError(400, "invalid_encoding", "路径编码非法");
      }
      return handleSubmissionUpdate(request, response, url, Number(match[1]), externalId);
    }

    match = /^\/api\/providers\/([A-Za-z0-9_-]+)\/secret$/.exec(path);
    if (match) {
      return handleProviderSecret(request, response, url, match[1]);
    }
    if (path === "/api/agents") return handleAgents(request, response, url);

    if (path.startsWith("/api/")) throw new HttpError(404, "not_found", "接口不存在");
    return handleStatic(request, response, url);
  };
}

export function createHandler(api) {
  return async function handler(request, response) {
    try {
      await api(request, response);
    } catch (error) {
      const { status, body } = errorPayload(error);
      if (status >= 500) console.error("[workbench-api]", error);
      if (response.headersSent) {
        response.end();
        return;
      }
      sendJson(response, status, body);
    }
  };
}
