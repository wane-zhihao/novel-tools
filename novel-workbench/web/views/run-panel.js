import { el, clear, toast } from "../lib/dom.js";
import { errorState } from "../lib/errors.js";
import { getRun, startRun, stopRun, resumeRun } from "../lib/api.js";

// 运行面板（Phase 1.2）：把「AI 正在写」这件事变成可看见的状态。
//
// 此前界面上既没有启动入口，也没有任何进度——用户只能反复点、或怀疑卡死。
// 这里给三件事：现在在哪（节点/第几章）、还活着吗（心跳新鲜度）、能做什么
// （启动/停止/恢复）。
//
// 设计取舍：
// - 不画进度条：流水线节点数随分支变化，编一个百分比是骗人的。
// - 卡死判定交给心跳：60 秒没有心跳就说「可能卡住」，并给出停止按钮。
// - 轮询只在 running/stopping 时进行，结束即停，避免空转。

const POLL_MS = 2000;
const STALE_MS = 60_000;

const PHASE_LABEL = {
  running: "正在跑",
  chapter_done: "本章写完",
  stopping: "正在停",
  blocked: "受阻停下",
  failed: "失败停下",
  finished: "已结束",
};

// 服务重启后内存快照消失，但 runs 表里还留着上次记录。把它归一成面板能显示的
// 形状，标 historical——UI 必须区分「现在在跑」和「上次跑成什么样」。
function phaseFromStatus(status) {
  if (status === "running") return "running";
  if (status === "stopping") return "stopping";
  if (status === "blocked") return "blocked";
  if (status === "failed") return "failed";
  return "finished";
}

function fromLast(last) {
  if (!last) return null;
  // runs 表里混着命令行动作记录（action=resume 之类，status 为 null、没有 executed_at）。
  // 那些不是一次 agent 运行，拿它们渲染会得到「上次 · 已结束 · 用时 —」这种假信息。
  if (!last.status) return null;
  if (last.action && last.action !== "agent_run") return null;
  const started = last.requested_at ? Date.parse(last.requested_at) : NaN;
  const ended = last.executed_at ? Date.parse(last.executed_at) : NaN;
  return {
    id: last.run_id ?? last.id,
    status: last.status,
    phase: phaseFromStatus(last.status),
    historical: true,
    started_at: last.requested_at ?? null,
    finished_at: last.executed_at ?? null,
    error: last.result?.error ?? null,
    result: last.result ?? null,
    current_node: null,
    chapter: null,
    node_started_at: null,
    heartbeat_at: null,
    heartbeat_age_ms: null,
    elapsed_ms: Number.isFinite(started) && Number.isFinite(ended)
      ? Math.max(0, ended - started) : null,
  };
}

function fmtDuration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const total = Math.round(ms / 1000);
  const min = Math.floor(total / 60);
  const sec = total % 60;
  if (min >= 60) {
    const hour = Math.floor(min / 60);
    return `${hour} 小时 ${min % 60} 分`;
  }
  return min > 0 ? `${min} 分 ${sec} 秒` : `${sec} 秒`;
}

function fmtAge(ms) {
  if (!Number.isFinite(ms)) return "—";
  if (ms < 3000) return "刚刚";
  if (ms < 60_000) return `${Math.round(ms / 1000)} 秒前`;
  return `${Math.round(ms / 60_000)} 分钟前`;
}

export function createRunPanel({ bookId, go, chapter = null, onChange = null }) {
  const box = el("div", { class: "run-panel" });
  let timer = null;
  let busy = false;
  let snapshot = null;
  let needsResume = false;
  let lastError = null;

  const live = () => snapshot && ["running", "stopping"].includes(snapshot.status);

  function stopPolling() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  function startPolling() {
    if (timer) return;
    timer = setInterval(() => { void refresh({ quiet: true }); }, POLL_MS);
  }

  function destroy() {
    stopPolling();
    box.remove();
  }

  async function refresh({ quiet = false } = {}) {
    try {
      const data = await getRun(bookId);
      // run=内存里的实时快照；last=数据库里的上次记录（服务重启后只有它）
      snapshot = data?.run ?? fromLast(data?.last);
      needsResume = data?.needs_resume === true;
      lastError = null;
    } catch (error) {
      // 静默轮询不弹错，避免网络抖动刷屏；保留上一帧
      if (!quiet) lastError = errorState(error, { go, bookId });
    }
    paint();
    if (live()) startPolling();
    else stopPolling();
    onChange?.(snapshot);
  }

  async function act(fn, { done } = {}) {
    if (busy) return;
    busy = true;
    paint();
    try {
      await fn();
      toast(done ?? "已提交");
      await refresh();
    } catch (error) {
      const state = errorState(error, { go, bookId });
      // 409 等业务冲突用 banner 更合适，但面板空间小——用 toast 并刷新真实状态
      toast(state.text, state.tone === "ok" ? "info" : "error");
      await refresh();
    } finally {
      busy = false;
      paint();
    }
  }

  function paint() {
    clear(box);
    const phase = snapshot?.phase ?? null;

    if (lastError) {
      box.append(el("div", { class: "run-head" },
        el("span", { class: "run-dot run-dot-bad" }),
        el("strong", { text: "运行状态读不到" }),
        el("span", { class: "muted small", text: lastError.text })));
    }

    if (!snapshot && needsResume) {
      // 崩溃后 step_token 还在：此时 POST /run 会被 409 挡住，必须先解除中断。
      // 注意后端 resume 只清掉卡住的状态、不会自己继续跑，所以文案不能承诺「继续」。
      box.append(...[
        el("div", { class: "run-head" },
          el("span", { class: "run-dot run-dot-bad" }),
          el("strong", { text: "上次被中断，需要先解除" }),
          el("span", { class: "muted small", text: "不解除的话，「让 AI 写」会被拒绝" })),
        el("p", { class: "run-hint", text: "解除后点「让 AI 写」，会从断点所在章节继续，不会从头重写。" }),
        el("div", { class: "run-actions" },
          el("button", {
            class: "button button-primary", type: "button", text: "解除中断",
            disabled: busy ? "disabled" : null,
            onclick: () => act(() => resumeRun(bookId), { done: "已解除中断，现在可以「让 AI 写」了" }),
          })),
      ].filter(Boolean));
      return;
    }

    if (!snapshot) {
      box.append(
        el("div", { class: "run-head" },
          el("span", { class: "run-dot" }),
          el("strong", { text: "AI 还没开始" }),
          el("span", { class: "muted small", text: "点了才开始，随时可以停" })),
        el("div", { class: "run-actions" },
          el("button", {
            class: "button button-primary", type: "button",
            text: chapter ? `让 AI 写第 ${chapter} 章` : "让 AI 写",
            title: chapter ? "只写这一章，写完就停，不动下一章" : "开始写",
            disabled: busy ? "disabled" : null,
            onclick: () => act(
              () => startRun(bookId, { mode: "real", untilChapter: chapter ?? null }),
              { done: chapter ? `已开始写第 ${chapter} 章` : "已开始，可以边看进度边写" }),
          }),
          el("button", {
            class: "button button-quiet", type: "button", text: "演练一次",
            title: "用占位文本走一遍链路，不写真正文",
            disabled: busy ? "disabled" : null,
            onclick: () => act(() => startRun(bookId, { mode: "mock" }), { done: "演练已开始" }),
          })),
        // 说实话：演练用的是占位文本，过不了真实校验（故事记录字段不全），
        // 通常会在定稿前停下。它的价值是「快速确认链路通不通」，不是产出正文。
        el("p", { class: "run-hint", text: "演练只验证链路连通，用占位文本、不写真文本；遇到严格校验会停下，属正常。" }));
      return;
    }

    const stale = live() && Number.isFinite(snapshot.heartbeat_age_ms)
      && snapshot.heartbeat_age_ms > STALE_MS;
    const dotClass = stale ? "run-dot-bad"
      : phase === "running" ? "run-dot-live"
        : phase === "blocked" || phase === "failed" ? "run-dot-bad" : "run-dot-idle";

    // 有残留 step_token 时，真实故事是「没跑完、需要接管」，不是笼统的「失败」
    const where = snapshot.current_node
      ? `${snapshot.chapter ? `第 ${snapshot.chapter} 章 · ` : ""}节点 ${snapshot.current_node}`
      : needsResume
        ? "上次没跑完，需要先解除"
        : `${snapshot.historical ? "上次 · " : ""}${PHASE_LABEL[phase] ?? "已结束"}`;

    const meta = [];
    if (live()) {
      meta.push(`已用 ${fmtDuration(snapshot.elapsed_ms)}`);
      meta.push(stale ? `心跳停在 ${fmtAge(snapshot.heartbeat_age_ms)}` : `心跳 ${fmtAge(snapshot.heartbeat_age_ms)}`);
    } else {
      meta.push(PHASE_LABEL[phase] ?? "已结束");
      meta.push(`用时 ${fmtDuration(snapshot.elapsed_ms)}`);
      if (snapshot.result?.status && snapshot.result.status !== "done") {
        meta.push(`结果：${snapshot.result.status}`);
      }
    }

    const actions = el("div", { class: "run-actions" });
    if (live()) {
      actions.append(el("button", {
        class: "button button-small", type: "button", text: "停止",
        disabled: busy ? "disabled" : null,
        onclick: () => act(() => stopRun(bookId), { done: "已发出停止指令" }),
      }));
    } else if (needsResume) {
      actions.append(el("button", {
        class: "button button-primary", type: "button", text: "解除中断",
        disabled: busy ? "disabled" : null,
        onclick: () => act(() => resumeRun(bookId), { done: "已解除中断，现在可以「让 AI 写」了" }),
      }));
    } else {
      actions.append(el("button", {
        class: "button button-small", type: "button", text: "再跑一次",
        disabled: busy ? "disabled" : null,
        onclick: () => act(
          () => startRun(bookId, { mode: "real", untilChapter: chapter ?? null }),
          { done: "已开始" }),
      }));
    }
    actions.append(el("button", {
      class: "button button-small button-quiet", type: "button", text: "刷新",
      disabled: busy ? "disabled" : null,
      onclick: () => { void refresh(); },
    }));

    // 注意：这里是原生 append，不是 el()——原生 append 会把 null 转成文本
    // "null" 显示出来。必须先过滤再展开。
    box.append(...[
      el("div", { class: "run-head" },
        el("span", { class: `run-dot ${dotClass}` }),
        el("strong", { text: where }),
        el("span", { class: "muted small", text: meta.join(" · ") })),
      stale ? el("p", { class: "run-hint", text: "60 秒没有心跳，可能卡住了。可以停止后重跑；已写入的内容不会丢。" }) : null,
      snapshot.error ? el("p", { class: "run-hint run-hint-bad", text: snapshot.error }) : null,
      !snapshot.error && snapshot.result?.note
        ? el("p", { class: "run-hint", text: snapshot.result.note }) : null,
      actions,
    ].filter(Boolean));
  }

  paint();
  void refresh();
  return { node: box, refresh, destroy };
}
