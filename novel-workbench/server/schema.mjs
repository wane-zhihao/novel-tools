export const SCHEMA_VERSION = 4;

function migration1(db) {
  db.exec(`
    CREATE TABLE books (
      id INTEGER PRIMARY KEY,
      path TEXT NOT NULL UNIQUE,
      title TEXT,
      genre TEXT,
      target_words INTEGER,
      max_chapters INTEGER,
      state_schema_version INTEGER,
      cursor TEXT,
      current_chapter INTEGER,
      chapters_committed INTEGER NOT NULL DEFAULT 0,
      words_total INTEGER NOT NULL DEFAULT 0,
      blocked_reason TEXT,
      finished INTEGER NOT NULL DEFAULT 0,
      fix_rounds_json TEXT,
      n20_rounds INTEGER NOT NULL DEFAULT 0,
      llm_rounds INTEGER NOT NULL DEFAULT 0,
      created_at TEXT,
      synced_at TEXT NOT NULL,
      fingerprint TEXT
    );

    CREATE TABLE chapters (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      number INTEGER NOT NULL,
      title TEXT,
      file_path TEXT NOT NULL,
      words INTEGER NOT NULL DEFAULT 0,
      committed INTEGER NOT NULL DEFAULT 0,
      sha256 TEXT,
      committed_at TEXT,
      UNIQUE(book_id, number)
    );

    CREATE TABLE nodes (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      node_id TEXT NOT NULL,
      chapter INTEGER,
      status TEXT,
      attempts INTEGER NOT NULL DEFAULT 0,
      happened_at TEXT,
      output_excerpt TEXT,
      UNIQUE(book_id, node_id)
    );

    CREATE TABLE findings (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      scope TEXT NOT NULL,
      chapter INTEGER NOT NULL,
      source TEXT,
      source_file TEXT NOT NULL,
      key TEXT NOT NULL,
      criterion TEXT,
      name TEXT,
      level TEXT,
      blocking INTEGER NOT NULL DEFAULT 0,
      quote TEXT,
      file_path TEXT,
      fix TEXT,
      note TEXT,
      first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL,
      resolved_at TEXT,
      active INTEGER NOT NULL DEFAULT 1,
      UNIQUE(book_id, scope, chapter, source_file, key)
    );

    CREATE TABLE judgments (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      node_id TEXT NOT NULL,
      verdict TEXT,
      summary TEXT,
      payload_json TEXT NOT NULL,
      recorded_at TEXT NOT NULL,
      UNIQUE(book_id, node_id)
    );

    CREATE TABLE runs (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      action TEXT,
      command TEXT,
      exit_code INTEGER,
      node TEXT,
      kind TEXT,
      requested_at TEXT NOT NULL,
      executed_at TEXT,
      result_json TEXT,
      UNIQUE(book_id, requested_at, action)
    );

    CREATE TABLE submissions (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      channel TEXT,
      submitted_at TEXT,
      editor TEXT,
      status TEXT,
      feedback TEXT,
      next_action TEXT,
      created_at TEXT NOT NULL
    );

    CREATE INDEX idx_chapters_book ON chapters(book_id, number);
    CREATE INDEX idx_nodes_book ON nodes(book_id, status);
    CREATE INDEX idx_findings_book ON findings(book_id, active, blocking);
    CREATE INDEX idx_findings_chapter ON findings(book_id, scope, chapter);
    CREATE INDEX idx_judgments_book ON judgments(book_id, recorded_at);
    CREATE INDEX idx_runs_book ON runs(book_id, requested_at);
    CREATE INDEX idx_submissions_book ON submissions(book_id, submitted_at);
  `);
}

function migration2(db) {
  // 投稿记录以 _pipeline/submissions.json 为真相，DB 保存索引；
  // external_id 对应文件里的 id，便于追加/更新时去重。
  db.exec("ALTER TABLE submissions ADD COLUMN external_id TEXT");
  db.exec("CREATE INDEX idx_submissions_external ON submissions(book_id, external_id)");
}

function migration3(db) {
  // 运行记录从“结束才写”改为“开始即写”，重启后能识别中断
  db.exec("ALTER TABLE runs ADD COLUMN run_id TEXT");
  db.exec("ALTER TABLE runs ADD COLUMN status TEXT");
  db.exec("ALTER TABLE runs ADD COLUMN events_json TEXT");
  db.exec("CREATE UNIQUE INDEX idx_runs_run_id ON runs(run_id) WHERE run_id IS NOT NULL");
}

function migration4(db) {
  // 正文中心：作者手改、版本、AI 改动、故事库、投稿 CRM
  db.exec("ALTER TABLE chapters ADD COLUMN dirty INTEGER NOT NULL DEFAULT 0");
  db.exec("ALTER TABLE chapters ADD COLUMN author_state TEXT");
  db.exec("ALTER TABLE chapters ADD COLUMN current_version_id INTEGER");

  db.exec(`
    CREATE TABLE versions (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      chapter INTEGER NOT NULL,
      kind TEXT,
      source TEXT,
      file_path TEXT NOT NULL,
      sha256 TEXT NOT NULL,
      words INTEGER NOT NULL DEFAULT 0,
      parent_id INTEGER,
      pinned INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE INDEX idx_versions_book ON versions(book_id, chapter, created_at);

    CREATE TABLE story_docs (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      path TEXT NOT NULL,
      sha256 TEXT,
      updated_at TEXT NOT NULL,
      UNIQUE(book_id, path)
    );

    CREATE TABLE approvals (
      id INTEGER PRIMARY KEY,
      book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
      run_id TEXT NOT NULL,
      node TEXT NOT NULL,
      chapter INTEGER,
      status TEXT NOT NULL DEFAULT 'pending',
      diff_json TEXT,
      created_at TEXT NOT NULL,
      decided_at TEXT,
      reject_reason TEXT
    );
    CREATE INDEX idx_approvals_book ON approvals(book_id, status);

    CREATE TABLE analytics_events (
      id INTEGER PRIMARY KEY,
      book_id INTEGER,
      type TEXT NOT NULL,
      payload_json TEXT,
      created_at TEXT NOT NULL
    );
    CREATE INDEX idx_events_type ON analytics_events(type, created_at);
  `);

  for (const column of [
    "manuscript_version TEXT",
    "package_path TEXT",
    "editor_contact TEXT",
    "expected_reply_at TEXT",
    "replied_at TEXT",
    "result TEXT",
  ]) {
    db.exec(`ALTER TABLE submissions ADD COLUMN ${column}`);
  }
  db.exec("ALTER TABLE runs ADD COLUMN pending_json TEXT");
}

export const migrations = [
  { version: 1, name: "initial", up: migration1 },
  { version: 2, name: "submission-external-id", up: migration2 },
  { version: 3, name: "run-persistence", up: migration3 },
  { version: 4, name: "author-content", up: migration4 },
];
