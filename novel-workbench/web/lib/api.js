export class ApiError extends Error {
  constructor(message, { status = 0, code = null, details = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function api(path, { method = "GET", body = null } = {}) {
  const init = { method };
  if (body !== null) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    throw new ApiError(data?.error?.message ?? `请求失败（HTTP ${response.status}）`, {
      status: response.status,
      code: data?.error?.code ?? null,
      details: data?.error?.details ?? null,
    });
  }
  return data;
}

/** 单章定稿。mode 省略=自动（下一章走 append，已定稿章走 revision）。 */
export function commitChapter(bookId, chapter, options = {}) {
  return api(`/api/books/${bookId}/chapters/${chapter}/commit`, {
    method: "POST",
    body: {
      mode: options.mode ?? "auto",
      accept_current_length: options.acceptCurrentLength === true,
    },
  });
}

export function openStudio(bookId) {
  const suffix = bookId ? `#/books/${bookId}` : "";
  window.location.href = `/studio.html${suffix}`;
}

// ── 运行（Phase 1.2）──────────────────────────────────────────────────
// v2 界面此前完全没有 run 能力：后端能跑流水线，但界面上既无法启动也无法观察，
// 用户只能靠旧 studio 或命令行。这三个函数补上「启动 / 观察 / 停止」最小闭环。

/** 启动一次流水线。mode=mock 是演练（不写真正文），real 才调真实模型。 */
export function startRun(bookId, { mode = "real", maxSteps = null } = {}) {
  const body = { mode };
  if (Number.isInteger(maxSteps) && maxSteps > 0) body.max_steps = maxSteps;
  return api(`/api/books/${bookId}/run`, { method: "POST", body });
}

/** 取运行快照。since>0 时只返回该 seq 之后的事件（增量轮询用）。 */
export function getRun(bookId, since = 0) {
  const query = Number.isInteger(since) && since > 0 ? `?since=${since}` : "";
  return api(`/api/books/${bookId}/run${query}`);
}

/** 停止当前运行（杀进程树）。已写入的内容不回滚，但会停下。 */
export function stopRun(bookId) {
  return api(`/api/books/${bookId}/stop`, { method: "POST", body: {} });
}

/** 接管上次被中断的运行（step_token 还在时必须先走这里）。 */
export function resumeRun(bookId, { mode = "real" } = {}) {
  return api(`/api/books/${bookId}/resume`, { method: "POST", body: { mode } });
}
