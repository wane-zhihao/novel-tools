// 错误码 → 「一句人话 + 一个出口」的单一映射。
//
// 铁律（plan 0.6 / §2.8）：后端只抛结构化 code，文案与出口由这里决定；
// 禁止把后端 stderr、堆栈或裸 JSON 拼进 UI。以前这套映射散在各视图里，
// 同一个码在不同页面可能给不同说法，用户看到的是技术细节而不是下一步。
//
// 每条：{ text: 一句人话, tone: ok|warn|danger, actions: [{ label, run }] }
// actions 的 run 接收 { go, bookId, chapter } —— 只给「能做到的下一步」，
// 做不到就别给按钮（给假出口比不给更糟）。

/** @typedef {{label: string, run: (ctx: object) => void}} ErrorAction */

const COMMIT_ERRORS = {
  gate_blocked: {
    tone: "danger",
    text: (error) => `没过闸门：${error?.details?.gate?.blocking_count ?? 0} 条阻断，先改再定稿。`,
    actions: [{ label: "去审：看问题", run: ({ go, bookId, chapter }) => go(`#/books/${bookId}/revise/${chapter}`) }],
  },
  length_out_of_band: {
    tone: "danger",
    text: () => "字数不在本章目标区间内。",
    actions: [{ label: "按当前字数定稿", run: ({ retryWithLength }) => retryWithLength?.() }],
  },
  tracking_missing: {
    tone: "danger",
    text: () => "这一章还没有故事记录，不能直接定稿（会损坏后续章节的上下文）。",
    actions: [{ label: "让 AI 写这一章", run: ({ go, bookId, chapter }) => go(`#/books/${bookId}/write/${chapter}`) }],
  },
  tracking_invalid: {
    tone: "danger",
    text: () => "这一章的故事记录不合格，需要重建后再定稿。",
    actions: [{ label: "去审：看详情", run: ({ go, bookId, chapter }) => go(`#/books/${bookId}/revise/${chapter}`) }],
  },
  chapter_gap: {
    tone: "danger",
    // 后端的 message 更具体（会说缺哪一章），但它可能等于 code 本身——那时用兜底文案
    text: (error) => {
      const message = error?.message ?? "";
      return message && message !== "chapter_gap" ? message : "不能跳过前面未定稿的章节。";
    },
    actions: [{
      label: "去定前一章",
      run: ({ go, bookId, chapter }) => go(`#/books/${bookId}/write/${Math.max(1, chapter - 1)}`),
    }],
  },
  chapter_busy: {
    tone: "danger",
    text: () => "流水线正卡在一个节点上，先恢复或停止再定稿。",
    actions: [],
  },
  run_running: {
    tone: "danger",
    text: () => "这一章还在写，等运行结束再定稿。",
    actions: [],
  },
  revision_conflict: {
    tone: "danger",
    text: () => "磁盘上的账本比这里新（另一个窗口刚改过）。重新载入后再定稿。",
    actions: [{ label: "重新载入", run: () => window.location.reload() }],
  },
  commit_timeout: {
    tone: "danger",
    text: () => "定稿超时了。账本已备份，重试是安全的（重复定稿不会写两次）。",
    actions: [],
  },
  quality_blocked: {
    tone: "danger",
    text: (error) => `质量闸门未过：${error?.details?.reason ?? "见审阅页"}`,
    actions: [{ label: "去审：看问题", run: ({ go, bookId, chapter }) => go(`#/books/${bookId}/revise/${chapter}`) }],
  },
  quality_tool_unavailable: {
    tone: "danger",
    text: () => "质检工具没跑起来（多半是路径配置问题）。正文没动，先修设置。",
    actions: [{ label: "去设置", run: ({ go }) => go("#/settings") }],
  },
  fact_blocked: {
    tone: "danger",
    text: (error) => {
      const count = error?.details?.fact_gate?.blocking_count;
      const countStr = count != null ? `${count} 条阻断` : (error?.message || "事实层阻断");
      return `事实层未过：${countStr}，先改再定稿。`;
    },
    actions: [{ label: "去审：看问题", run: ({ go, bookId, chapter }) => go(`#/books/${bookId}/revise/${chapter}`) }],
  },
  fact_tool_unavailable: {
    tone: "danger",
    text: () => "事实闸门不可用（多半是路径配置问题）。正文没动，先修设置。",
    actions: [{ label: "去设置", run: ({ go }) => go("#/settings") }],
  },
};

const CONTENT_ERRORS = {
  content_conflict: {
    tone: "danger",
    text: () => "磁盘上的正文比这里新。为了不覆盖，自动保存已暂停。",
    actions: [{ label: "载入磁盘版本", run: () => window.location.reload() }],
  },
  committed_edit_required: {
    tone: "warn",
    text: () => "这一章已定稿，改它需要先确认。",
    actions: [],
  },
};

const STORY_ERRORS = {
  story_conflict: {
    tone: "danger",
    text: () => "设定已被其他窗口修改。重新载入后再保存，别覆盖对方的改动。",
    actions: [{ label: "重新载入", run: () => window.location.reload() }],
  },
};

const ALL = { ...COMMIT_ERRORS, ...CONTENT_ERRORS, ...STORY_ERRORS };

export class ErrorState {
  constructor({ code, text, tone, actions, error }) {
    this.code = code;
    this.text = text;
    this.tone = tone;
    this.actions = actions;
    this.error = error;
  }
}

/**
 * 把任意异常收敛成一个可渲染的 ErrorState。
 * 未知错误码不隐藏：仍给后端的 message（它已经是人话），但没有出口就给空数组，
 * 由调用方决定是否加通用出口。
 */
export function errorState(error, ctx = {}) {
  const code = error?.code ?? null;
  const spec = code ? ALL[code] : null;
  if (!spec) {
    return new ErrorState({
      code,
      text: error?.message || "操作失败。",
      tone: "danger",
      actions: [],
      error,
    });
  }
  const actions = (spec.actions ?? []).map((action) => ({
    label: action.label,
    onClick: () => action.run({ ...ctx, error }),
  }));
  return new ErrorState({
    code,
    text: typeof spec.text === "function" ? spec.text(error) : spec.text,
    tone: spec.tone ?? "danger",
    actions,
    error,
  });
}

/** 已知错误码清单（测试与文档用，避免码散落各处无人登记）。 */
export function knownErrorCodes() {
  return Object.keys(ALL).sort();
}
