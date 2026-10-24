import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { normalizeText } from "./book-files.mjs";
import { resetData } from "./db.mjs";
import { storyIndex } from "./story.mjs";

const SYNC_DIRS = ["_pipeline", "正文", "大纲", "设定", "追踪", "投稿包"];
const MAX_HASH_BYTES = 4_000_000;
const ENDGAME_CHAPTER = -1;

function toPosix(path) {
  return path.split(sep).join("/");
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function sha256(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

function countVisibleChars(text) {
  return text.replace(/\s+/g, "").length;
}

export function hasPipelineState(bookDir) {
  return existsSync(join(bookDir, "_pipeline", "state.json"));
}

export function discoverBooks(roots) {
  const found = [];
  for (const root of roots) {
    const absolute = resolve(root);
    if (!existsSync(absolute)) continue;
    if (hasPipelineState(absolute)) {
      found.push(absolute);
      continue;
    }
    let entries = [];
    try {
      entries = readdirSync(absolute, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const dir = join(absolute, entry.name);
      if (hasPipelineState(dir)) found.push(dir);
    }
  }
  return [...new Set(found)];
}

function fileSha(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function fingerprintOf(bookDir) {
  // 逐文件 路径:size:内容sha 递归拼接后哈希：
  // - 同 size 同 mtime 的内容变化（保留 mtime 的拷贝/回滚）→ 内容 sha 不同，检出
  // - rename 互换 → 路径与内容绑定，检出
  // - 纯 touch（内容不变）→ 指纹不变，正确跳过
  // 超大文件（>4MB）退化为 size:mtimeMs，避免为指纹读入过大内容
  const entries = [];
  for (const dirName of SYNC_DIRS) {
    const dir = join(bookDir, dirName);
    if (!existsSync(dir)) continue;
    let scanned = [];
    try {
      scanned = readdirSync(dir, { withFileTypes: true, recursive: true });
    } catch {
      continue;
    }
    for (const entry of scanned) {
      if (!entry.isFile()) continue;
      const full = join(entry.parentPath ?? dir, entry.name);
      const rel = toPosix(relative(bookDir, full));
      try {
        const info = statSync(full);
        if (info.size > MAX_HASH_BYTES) {
          entries.push(`${rel}:${info.size}:${Math.round(info.mtimeMs)}`);
          continue;
        }
        entries.push(`${rel}:${info.size}:${fileSha(full)}`);
      } catch {}
    }
  }
  entries.sort();
  return `${entries.length}:${sha256(entries.join("\n"))}`;
}

function upsertBook(db, bookDir, state, fingerprint, nowIso) {
  db.prepare(`
    INSERT INTO books (
      path, title, genre, target_words, max_chapters, state_schema_version,
      cursor, current_chapter, chapters_committed, words_total, blocked_reason,
      finished, fix_rounds_json, n20_rounds, llm_rounds, created_at, synced_at, fingerprint
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(path) DO UPDATE SET
      title = excluded.title,
      genre = excluded.genre,
      target_words = excluded.target_words,
      max_chapters = excluded.max_chapters,
      state_schema_version = excluded.state_schema_version,
      cursor = excluded.cursor,
      current_chapter = excluded.current_chapter,
      chapters_committed = excluded.chapters_committed,
      words_total = excluded.words_total,
      blocked_reason = excluded.blocked_reason,
      finished = excluded.finished,
      fix_rounds_json = excluded.fix_rounds_json,
      n20_rounds = excluded.n20_rounds,
      llm_rounds = excluded.llm_rounds,
      created_at = excluded.created_at,
      synced_at = excluded.synced_at,
      fingerprint = excluded.fingerprint
  `).run(
    bookDir,
    state.book ?? null,
    state.genre ?? null,
    state.target_words ?? null,
    state.max_chapters ?? null,
    state.schema_version ?? null,
    state.cursor ?? null,
    state.current_chapter ?? null,
    Number(state.chapters_committed) || 0,
    Number(state.words_total) || 0,
    state.blocked_reason ?? null,
    state.cursor === "__done__" ? 1 : 0,
    JSON.stringify(state.fix_rounds ?? {}),
    Number(state.n20_rounds) || 0,
    Number(state.llm_rounds) || 0,
    state.created_at ?? null,
    nowIso,
    fingerprint,
  );
  return db.prepare("SELECT id FROM books WHERE path = ?").get(bookDir).id;
}

function readTracking(bookDir) {
  const path = join(bookDir, "追踪", "_tracking-state.json");
  if (!existsSync(path)) return {};
  try {
    return readJson(path);
  } catch {
    return {};
  }
}

/** versions.mjs 的磁盘文件名：<ISO stamp 把 : . 换成 ->_<safeSource>.md */
function stampToIso(stamp) {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z$/.exec(stamp);
  if (!match) return null;
  return `${match[1]}T${match[2]}:${match[3]}:${match[4]}.${match[5]}Z`;
}

function versionKindFromSource(source) {
  if (source.startsWith("ai-before")) return "ai-before";
  if (source.startsWith("ai-rewrite")) return "ai-rewrite";
  if (source.startsWith("ai-draft")) return "ai-draft";
  if (source.startsWith("rollback")) return "rollback";
  if (source.startsWith("manual-before")) return "manual-before";
  return source;
}

/**
 * 从 _pipeline/versions/第XXX章/*.md 重建 versions 行（rebuild 无损的关键路径）。
 * 按文件名字典序（= 时间序）处理，parent_id 链接到同章上一行，id 尽量保持稳定
 * （已存在的 file_path 只更新内容字段，不重新分配 id）。
 */
function syncVersions(db, bookId, bookDir) {
  const root = join(bookDir, "_pipeline", "versions");
  const find = db.prepare("SELECT id FROM versions WHERE book_id = ? AND file_path = ?");
  const update = db.prepare(`
    UPDATE versions SET chapter = ?, kind = ?, source = ?, sha256 = ?, words = ?, created_at = ?
    WHERE id = ?
  `);
  const insert = db.prepare(`
    INSERT INTO versions (book_id, chapter, kind, source, file_path, sha256, words, parent_id, pinned, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
  `);
  const keep = [];
  let total = 0;
  if (existsSync(root)) {
    let chapterDirs = [];
    try {
      chapterDirs = readdirSync(root, { withFileTypes: true });
    } catch {
      chapterDirs = [];
    }
    for (const entry of chapterDirs) {
      if (!entry.isDirectory()) continue;
      const match = /^第(\d{3})章$/.exec(entry.name);
      if (!match) continue;
      const chapter = Number(match[1]);
      const dir = join(root, entry.name);
      let files = [];
      try {
        files = readdirSync(dir).filter((name) => name.endsWith(".md")).sort();
      } catch {
        continue;
      }
      for (const name of files) {
        const rel = `_pipeline/versions/第${String(chapter).padStart(3, "0")}章/${name}`;
        keep.push(rel);
        const file = join(dir, name);
        let text;
        try {
          text = readFileSync(file, "utf8");
        } catch {
          continue;
        }
        const normalized = normalizeText(text);
        const createdAt = stampToIso(name.slice(0, name.indexOf("_")))
          ?? statSync(file).mtime.toISOString();
        const source = name.slice(name.indexOf("_") + 1).replace(/\.md$/, "");
        const kind = versionKindFromSource(source);
        const sha = sha256(normalized);
        const words = countVisibleChars(normalized);
        const existing = find.get(bookId, rel);
        if (existing) {
          update.run(chapter, kind, source, sha, words, createdAt, existing.id);
        } else {
          const parent = db.prepare(
            "SELECT MAX(id) AS id FROM versions WHERE book_id = ? AND chapter = ?",
          ).get(bookId, chapter)?.id ?? null;
          insert.run(bookId, chapter, kind, source, rel, sha, words, parent, createdAt);
        }
        total += 1;
      }
    }
  }
  if (keep.length) {
    db.prepare(`DELETE FROM versions WHERE book_id = ? AND file_path NOT IN (${keep.map(() => "?").join(",")})`)
      .run(bookId, ...keep);
  } else {
    db.prepare("DELETE FROM versions WHERE book_id = ?").run(bookId);
  }
  return total;
}

/** story_docs 索引（设定/大纲/伏笔等）：只收磁盘上存在的文档，删掉已消失的行，rebuild 后不丢。 */
function syncStoryDocs(db, bookId, bookDir) {
  let index;
  try {
    index = storyIndex(bookDir);
  } catch {
    return 0;
  }
  const upsert = db.prepare(`
    INSERT INTO story_docs (book_id, kind, path, sha256, updated_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(book_id, path) DO UPDATE SET
      kind = excluded.kind, sha256 = excluded.sha256, updated_at = excluded.updated_at
  `);
  const nowIso = new Date().toISOString();
  const keep = [];
  const docs = [
    ...index.groups.flatMap((group) => group.docs),
    ...index.chapter_outlines,
  ];
  for (const doc of docs) {
    if (!doc.exists || !doc.sha256) continue;
    keep.push(doc.path);
    upsert.run(bookId, doc.kind, doc.path, doc.sha256, nowIso);
  }
  if (keep.length) {
    db.prepare(`DELETE FROM story_docs WHERE book_id = ? AND path NOT IN (${keep.map(() => "?").join(",")})`)
      .run(bookId, ...keep);
  } else {
    db.prepare("DELETE FROM story_docs WHERE book_id = ?").run(bookId);
  }
  return keep.length;
}

function syncChapters(db, bookId, bookDir, state) {
  const bodyDir = join(bookDir, "正文");
  const records = readTracking(bookDir).wordcount_records ?? {};
  const rows = [];
  if (existsSync(bodyDir)) {
    for (const name of readdirSync(bodyDir)) {
      const match = /^第(\d{3})章_(.*)\.md$/.exec(name);
      if (!match) continue;
      const number = Number(match[1]);
      const file = join(bodyDir, name);
      const text = readFileSync(file, "utf8");
      const record = records[String(number)];
      const committed = record ? 1 : 0;
      const sha = sha256(text);
      // dirty：已封板且正文与封板时记录的 body_sha256 不一致（AI 改过或作者改过）
      const committedSha = typeof record?.body_sha256 === "string" ? record.body_sha256 : null;
      const dirty = committed && committedSha && sha !== committedSha ? 1 : 0;
      rows.push({
        number,
        title: match[2],
        filePath: toPosix(relative(bookDir, file)),
        words: Number(record?.actual) || countVisibleChars(text),
        committed,
        sha256: sha,
        committedAt: state.nodes?.[`C${number}.N16`]?.at ?? null,
        dirty,
      });
    }
  }
  // 这三个字段以前只由运行期/保存接口写，rebuild 后全部丢失；现在从磁盘推导回写
  const previousStates = new Map(
    db.prepare("SELECT number, author_state FROM chapters WHERE book_id = ?").all(bookId)
      .map((row) => [row.number, row.author_state]),
  );
  const latestVersions = new Map(
    db.prepare("SELECT chapter, MAX(id) AS id FROM versions WHERE book_id = ? GROUP BY chapter")
      .all(bookId).map((row) => [row.chapter, row.id]),
  );
  const upsert = db.prepare(`
    INSERT INTO chapters (book_id, number, title, file_path, words, committed, sha256, committed_at, dirty, author_state, current_version_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(book_id, number) DO UPDATE SET
      title = excluded.title,
      file_path = excluded.file_path,
      words = excluded.words,
      committed = excluded.committed,
      sha256 = excluded.sha256,
      committed_at = excluded.committed_at,
      dirty = excluded.dirty,
      author_state = excluded.author_state,
      current_version_id = excluded.current_version_id
  `);
  for (const row of rows) {
    const previousState = previousStates.get(row.number) ?? null;
    const authorState = row.committed
      ? (row.dirty ? (previousState === "ai_edit" ? "ai_edit" : "draft_edit") : "committed")
      : "draft";
    upsert.run(
      bookId, row.number, row.title, row.filePath, row.words, row.committed, row.sha256,
      row.committedAt, row.dirty, authorState, latestVersions.get(row.number) ?? null,
    );
  }
  const keep = rows.map((row) => row.number);
  if (keep.length) {
    db.prepare(`DELETE FROM chapters WHERE book_id = ? AND number NOT IN (${keep.map(() => "?").join(",")})`)
      .run(bookId, ...keep);
  } else {
    db.prepare("DELETE FROM chapters WHERE book_id = ?").run(bookId);
  }
  return rows.length;
}

function syncNodes(db, bookId, state) {
  const nodes = Object.entries(state.nodes ?? {});
  const upsert = db.prepare(`
    INSERT INTO nodes (book_id, node_id, chapter, status, attempts, happened_at, output_excerpt)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(book_id, node_id) DO UPDATE SET
      chapter = excluded.chapter,
      status = excluded.status,
      attempts = excluded.attempts,
      happened_at = excluded.happened_at,
      output_excerpt = excluded.output_excerpt
  `);
  for (const [nodeId, info] of nodes) {
    const chapterMatch = /^C(\d+)\./.exec(nodeId);
    upsert.run(
      bookId,
      nodeId,
      chapterMatch ? Number(chapterMatch[1]) : null,
      info?.status ?? null,
      Number(info?.attempts) || 0,
      info?.at ?? null,
      typeof info?.output === "string" ? info.output.slice(-800) : null,
    );
  }
  const keep = nodes.map(([nodeId]) => nodeId);
  if (keep.length) {
    db.prepare(`DELETE FROM nodes WHERE book_id = ? AND node_id NOT IN (${keep.map(() => "?").join(",")})`)
      .run(bookId, ...keep);
  } else {
    db.prepare("DELETE FROM nodes WHERE book_id = ?").run(bookId);
  }
  return nodes.length;
}

function syncFindingsFile(db, bookId, bookDir, file, scope, chapter, nowIso, defaultSource) {
  const relativePath = toPosix(relative(bookDir, file));
  const doc = readJson(file);
  const items = Array.isArray(doc.items) ? doc.items : [];
  const resolvedAt = doc.resolved_at ?? null;
  const seen = new Set();
  const upsert = db.prepare(`
    INSERT INTO findings (
      book_id, scope, chapter, source, source_file, key, criterion, name, level, blocking,
      quote, file_path, fix, note, first_seen_at, last_seen_at, resolved_at, active
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
    ON CONFLICT(book_id, scope, chapter, source_file, key) DO UPDATE SET
      source = excluded.source,
      criterion = excluded.criterion,
      name = excluded.name,
      level = excluded.level,
      blocking = excluded.blocking,
      quote = excluded.quote,
      file_path = excluded.file_path,
      fix = excluded.fix,
      note = excluded.note,
      last_seen_at = excluded.last_seen_at,
      resolved_at = excluded.resolved_at,
      active = 1
  `);
  const duplicates = new Map();
  items.forEach((item, index) => {
    const base = String(item.id ?? item.criterion ?? `item-${index}`);
    const count = (duplicates.get(base) ?? 0) + 1;
    duplicates.set(base, count);
    const key = count > 1 ? `${base}#${count}` : base;
    seen.add(key);
    upsert.run(
      bookId,
      scope,
      chapter,
      item.source ?? defaultSource,
      relativePath,
      key,
      item.criterion ?? null,
      item.name ?? item.criterion ?? null,
      item.level ?? null,
      item.blocking ? 1 : 0,
      item.quote ?? null,
      item.file ?? item.file_path ?? null,
      item.fix ?? null,
      item.note ?? null,
      nowIso,
      nowIso,
      resolvedAt,
    );
  });
  const stale = db.prepare("UPDATE findings SET active = 0, last_seen_at = ? WHERE book_id = ? AND source_file = ? AND active = 1");
  stale.run(nowIso, bookId, relativePath);
  if (seen.size) {
    db.prepare(`
      UPDATE findings SET active = 1
      WHERE book_id = ? AND source_file = ? AND key IN (${[...seen].map(() => "?").join(",")})
    `).run(bookId, relativePath, ...seen);
  }
  return items.length;
}

function syncFindings(db, bookId, bookDir, nowIso) {
  const pipelineDir = join(bookDir, "_pipeline");
  if (!existsSync(pipelineDir)) return 0;
  const sourceFiles = new Set();
  let total = 0;
  for (const name of readdirSync(pipelineDir)) {
    const chapterMatch = /^findings_C(\d+)\.json$/.exec(name);
    if (!chapterMatch) continue;
    const chapter = Number(chapterMatch[1]);
    sourceFiles.add(`_pipeline/${name}`);
    total += syncFindingsFile(
      db, bookId, bookDir, join(pipelineDir, name), "chapter", chapter, nowIso, "N13",
    );
  }
  const endgame = join(pipelineDir, "endgame_findings.json");
  if (existsSync(endgame)) {
    sourceFiles.add("_pipeline/endgame_findings.json");
    total += syncFindingsFile(
      db, bookId, bookDir, endgame, "endgame", ENDGAME_CHAPTER, nowIso, "N18",
    );
  }
  // 整个 findings 文件被删掉后，旧行不能继续以 active=1 出现在问题列表里
  if (sourceFiles.size) {
    db.prepare(`
      UPDATE findings SET active = 0
      WHERE book_id = ? AND active = 1 AND source_file NOT IN (${[...sourceFiles].map(() => "?").join(",")})
    `).run(bookId, ...sourceFiles);
  } else {
    db.prepare("UPDATE findings SET active = 0 WHERE book_id = ? AND active = 1").run(bookId);
  }
  return total;
}

function syncJudgments(db, bookId, bookDir) {
  const pipelineDir = join(bookDir, "_pipeline");
  if (!existsSync(pipelineDir)) return 0;
  const nodes = [];
  const upsert = db.prepare(`
    INSERT INTO judgments (book_id, node_id, verdict, summary, payload_json, recorded_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(book_id, node_id) DO UPDATE SET
      verdict = excluded.verdict,
      summary = excluded.summary,
      payload_json = excluded.payload_json,
      recorded_at = excluded.recorded_at
  `);
  for (const name of readdirSync(pipelineDir)) {
    const match = /^judgments_(.+)\.json$/.exec(name);
    if (!match) continue;
    const file = join(pipelineDir, name);
    const nodeId = match[1];
    // 文件存在即视为该节点仍有记录；解析失败时保留上一次入库内容，避免瞬时坏文件清空历史
    nodes.push(nodeId);
    let doc;
    try {
      doc = readJson(file);
    } catch {
      continue;
    }
    if (!doc || typeof doc !== "object") continue;
    upsert.run(
      bookId,
      nodeId,
      doc.verdict ?? null,
      doc.summary ?? null,
      JSON.stringify(doc),
      statSync(file).mtime.toISOString(),
    );
  }
  if (nodes.length) {
    db.prepare(`DELETE FROM judgments WHERE book_id = ? AND node_id NOT IN (${nodes.map(() => "?").join(",")})`)
      .run(bookId, ...nodes);
  } else {
    db.prepare("DELETE FROM judgments WHERE book_id = ?").run(bookId);
  }
  return nodes.length;
}

function syncSubmissions(db, bookId, bookDir, nowIso) {
  const file = join(bookDir, "_pipeline", "submissions.json");
  if (!existsSync(file)) return 0;
  let doc;
  try {
    doc = readJson(file);
  } catch {
    return 0;
  }
  const items = Array.isArray(doc) ? doc : Array.isArray(doc?.items) ? doc.items : [];
  const find = db.prepare("SELECT id FROM submissions WHERE book_id = ? AND external_id = ?");
  const update = db.prepare(`
    UPDATE submissions SET channel = ?, submitted_at = ?, editor = ?, status = ?, feedback = ?,
      next_action = ?, manuscript_version = ?, package_path = ?, editor_contact = ?,
      expected_reply_at = ?, replied_at = ?, result = ?, created_at = ?
    WHERE id = ?
  `);
  const insert = db.prepare(`
    INSERT INTO submissions (book_id, external_id, channel, submitted_at, editor, status, feedback,
      next_action, manuscript_version, package_path, editor_contact, expected_reply_at, replied_at,
      result, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  items.forEach((item, index) => {
    const externalId = String(item.id ?? `submission-${index}`);
    const values = [
      item.channel ?? null,
      item.submitted_at ?? null,
      item.editor ?? null,
      item.status ?? null,
      item.feedback ?? null,
      item.next_action ?? null,
      item.manuscript_version ?? null,
      item.package_path ?? null,
      item.editor_contact ?? null,
      item.expected_reply_at ?? null,
      item.replied_at ?? null,
      item.result ?? null,
      item.created_at ?? nowIso,
    ];
    const existing = find.get(bookId, externalId);
    if (existing) update.run(...values, existing.id);
    else insert.run(bookId, externalId, ...values);
  });
  return items.length;
}

function syncRun(db, bookId, bookDir) {
  const file = join(bookDir, "_pipeline", "run.json");
  if (!existsSync(file)) return 0;
  let doc;
  try {
    doc = readJson(file);
  } catch {
    return 0;
  }
  const requestedAt = doc.requested_at ?? statSync(file).mtime.toISOString();
  const action = doc.action ?? "unknown";
  db.prepare(`
    INSERT INTO runs (book_id, action, command, exit_code, node, kind, requested_at, executed_at, result_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(book_id, requested_at, action) DO UPDATE SET
      command = excluded.command,
      exit_code = excluded.exit_code,
      node = excluded.node,
      kind = excluded.kind,
      executed_at = excluded.executed_at,
      result_json = excluded.result_json
  `).run(
    bookId,
    action,
    doc.command ?? null,
    Number.isInteger(doc.exit_code) ? doc.exit_code : null,
    doc.node ?? null,
    doc.kind ?? null,
    requestedAt,
    doc.executed_at ?? null,
    JSON.stringify(doc),
  );
  return 1;
}

export function syncBook(db, bookDir, { now = new Date(), force = false } = {}) {
  const absolute = resolve(bookDir);
  const fingerprint = fingerprintOf(absolute);
  if (!force) {
    const existing = db.prepare("SELECT fingerprint FROM books WHERE path = ?").get(absolute);
    if (existing && existing.fingerprint === fingerprint) {
      return { path: absolute, skipped: true };
    }
  }
  const state = readJson(join(absolute, "_pipeline", "state.json"));
  const nowIso = now.toISOString();
  db.exec("BEGIN");
  try {
    const bookId = upsertBook(db, absolute, state, fingerprint, nowIso);
    const versions = syncVersions(db, bookId, absolute);
    const storyDocs = syncStoryDocs(db, bookId, absolute);
    const chapters = syncChapters(db, bookId, absolute, state);
    const nodes = syncNodes(db, bookId, state);
    const findings = syncFindings(db, bookId, absolute, nowIso);
    const judgments = syncJudgments(db, bookId, absolute);
    const runs = syncRun(db, bookId, absolute);
    const submissions = syncSubmissions(db, bookId, absolute, nowIso);
    db.exec("COMMIT");
    return {
      path: absolute, skipped: false, bookId, versions, storyDocs, chapters, nodes,
      findings, judgments, runs, submissions,
    };
  } catch (error) {
    db.exec("ROLLBACK");
    throw error;
  }
}

export function syncAll(db, roots, { force = false, now = new Date() } = {}) {
  const books = discoverBooks(roots);
  const summary = { roots, discovered: books.length, synced: 0, skipped: 0, errors: [] };
  for (const bookDir of books) {
    try {
      const result = syncBook(db, bookDir, { force, now });
      if (result.skipped) summary.skipped += 1;
      else summary.synced += 1;
    } catch (error) {
      summary.errors.push({ path: bookDir, message: error.message });
    }
  }
  return summary;
}

export function rebuild(db, roots, { now = new Date() } = {}) {
  resetData(db);
  return syncAll(db, roots, { force: true, now });
}

export { ENDGAME_CHAPTER };
