import { api, commitChapter } from "../lib/api.js";
import { el, clear, fmtNumber, toast, renderMarkdown } from "../lib/dom.js";
import { errorState } from "../lib/errors.js";
import { createRunPanel } from "./run-panel.js";

const AUTOSAVE_MS = 1200;
const MAX_SAVE_WAIT = 10000;

export async function renderWrite({ bookId, chapter, go, state }) {
  const view = document.querySelector("#view");
  view.classList.add("wide");
  clear(view);
  view.append(el("p", { class: "empty", text: "正在打开稿纸…" }));

  let manuscript;
  let data;
  try {
    [manuscript, data] = await Promise.all([
      api(`/api/books/${bookId}/manuscript`),
      api(`/api/books/${bookId}/chapters/${chapter}/content`),
    ]);
  } catch (error) {
    view.classList.remove("wide");
    clear(view).append(el("div", { class: "empty" },
      el("strong", { text: "这一章打不开" }),
      el("p", { text: error.message }),
      el("button", { class: "button", type: "button", onclick: () => go("#/"), text: "回书房" })));
    return;
  }

  const title = data.title && data.title !== "未命名" ? data.title : `第${chapter}章`;
  let baseSha = data.sha256;
  let lastSaved = data.content;
  let dirty = false;
  let saving = false;
  let allowCommittedEdit = false;
  let saveTimer = null;
  let maxTimer = null;
  let previewing = false;

  const editor = el("textarea", {
    id: "editorInput",
    spellcheck: "true",
    "aria-label": "正文",
    placeholder: "在这里写第一段。先写下来，再改好。",
  });
  editor.value = data.content;

  const saveState = el("span", { class: "save-state", "data-state": "saved", text: data.exists ? "已保存" : "新章" });
  const wordLabel = el("span", { text: `${fmtNumber(data.words)} 字` });
  const banner = el("div", { id: "writeBanner" });
  const preview = el("div", { class: "preview", hidden: "hidden" });

  function setSaveState(value, text) {
    saveState.dataset.state = value;
    saveState.textContent = text;
  }

  // ── 定稿 ──────────────────────────────────────────────────────────────
  // 一个函数覆盖三种入口：定稿（append）/ 重新定稿（revision）/ 字数越界后的放行。
  // 错误一律走「一句话原因 + 一个主出口」，不把后端 detail 直接抛给用户。
  let committing = false;

  async function runCommit(mode = "auto", { acceptLength = false } = {}) {
    if (committing) return;
    committing = true;
    setSaveState("saving", "正在定稿…");
    try {
      if (dirty) await save();
      const result = await commitChapter(bookId, chapter, {
        mode, acceptCurrentLength: acceptLength,
      });
      const words = fmtNumber(result.words ?? 0);
      if (result.already) {
        setSaveState("saved", "已定稿");
        showBanner(`这一章已经是定稿状态（${words} 字），没有重复提交。`, "warn");
      } else {
        setSaveState("saved", "已定稿");
        showBanner(`第 ${chapter} 章已定稿 · ${words} 字 · 闸门 ${result.gate?.blocking_count ?? 0} 阻断。`, "ok");
        data.dirty = false;
        data.committed = true;
        // commitButton 在下方渲染时创建；这里按 DOM 查，避免声明顺序耦合
        const btn = view.querySelector(".write-foot .button-primary");
        if (btn) {
          btn.textContent = "已定稿";
          btn.setAttribute("disabled", "disabled");
        }
      }
      // 不自动跳转：定稿是本章终点，用户可能还要看一眼闸门结论或接着写下一章。
      // 结论留在页面上，由用户自己决定下一步（点「下一章」或回书房）。
    } catch (error) {
      setSaveState("error", "定稿失败");
      // 0.6：文案与出口统一由 web/lib/errors.js 决定，视图不再自己维护错误码表。
      const state = errorState(error, {
        go, bookId, chapter,
        retryWithLength: () => runCommit(mode, { acceptLength: true }),
      });
      // 把错误码留在 DOM 上：用户看到人话，排障时能直接读到 code
      banner.dataset.errorCode = state.code ?? "unknown";
      showBanner(state.text, state.tone, state.actions);
    } finally {
      committing = false;
    }
  }

  function updateWords() {
    const count = editor.value.replace(/\s+/g, "").length;
    wordLabel.textContent = `${fmtNumber(count)} 字`;
  }

  function clearTimers() {
    clearTimeout(saveTimer);
    clearTimeout(maxTimer);
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, AUTOSAVE_MS);
    if (!maxTimer) {
      maxTimer = setTimeout(save, MAX_SAVE_WAIT);
    }
  }

  function localKey() {
    return `workbench:draft:${bookId}:${chapter}`;
  }

  async function save() {
    clearTimers();
    maxTimer = null;
    if (saving || editor.value === lastSaved) {
      if (editor.value === lastSaved) setSaveState("saved", "已保存");
      return;
    }
    if (data.committed && !allowCommittedEdit) {
      const ok = window.confirm("本章已定稿。修改后需要重新定稿，并会让投稿包过期。继续修改？");
      if (!ok) return;
      allowCommittedEdit = true;
    }
    saving = true;
    setSaveState("saving", "保存中…");
    try {
      const saved = await api(`/api/books/${bookId}/chapters/${chapter}/content`, {
        method: "PUT",
        body: {
          base_sha: baseSha,
          content: editor.value,
          title: data.title && data.title !== "未命名" ? data.title : "未命名",
          allow_committed_edit: allowCommittedEdit,
        },
      });
      baseSha = saved.sha256;
      lastSaved = saved.content;
      data = { ...data, ...saved };
      dirty = false;
      localStorage.removeItem(localKey());
      setSaveState("saved", "已保存");
      updateWords();
      if (saved.dirty) {
        // 定稿入口就在这里，不再把用户支去制作台
        showBanner("已保存。这章改过，投稿包还是旧的。", "warn", [
          { label: "重新定稿", onClick: () => runCommit("revision") },
          { label: "对比改了什么", onClick: () => go(`#/books/${bookId}/revise/${chapter}`) },
        ]);
      }
    } catch (error) {
      if (error.code === "content_conflict") {
        setSaveState("error", "有冲突");
        // 文案与「载入磁盘版本」出口都来自 errors.js（单一来源）
        const conflict = errorState(error, { go, bookId, chapter });
        showBanner(conflict.text, conflict.tone, conflict.actions);
        return;
      }
      setSaveState("error", "保存失败");
      if (error.code === "committed_edit_required") {
        allowCommittedEdit = true;
        scheduleSave();
      } else {
        toast(error.message, "error");
      }
    } finally {
      saving = false;
    }
  }

  function showBanner(text, tone = "warn", actions = []) {
    clear(banner);
    // tone → class 的映射集中在这里，errors.js 只负责给语义 tone
    const toneClass = { danger: " banner-danger", ok: " banner-ok", warn: "" }[tone] ?? "";
    banner.append(el("div", { class: `banner${toneClass}` },
      el("span", { text }),
      ...actions.map((action) => el("button", {
        class: "button button-small", type: "button", text: action.label, onclick: action.onClick,
      }))));
  }

  editor.addEventListener("input", () => {
    dirty = true;
    setSaveState("dirty", "未保存");
    updateWords();
    try {
      localStorage.setItem(localKey(), JSON.stringify({ content: editor.value, sha: baseSha, at: Date.now() }));
    } catch {}
    scheduleSave();
  });

  document.addEventListener("keydown", function onKey(event) {
    if (!document.body.contains(editor)) {
      document.removeEventListener("keydown", onKey);
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      save();
    }
    if (event.key === "Escape" && document.body.classList.contains("focus-mode")) {
      document.body.classList.remove("focus-mode");
    }
  });

  const onBeforeUnload = (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  };
  window.addEventListener("beforeunload", onBeforeUnload);
  window.addEventListener(
    "hashchange",
    () => window.removeEventListener("beforeunload", onBeforeUnload),
    { once: true },
  );

  // 本地崩溃草稿：同 sha 时提示恢复
  try {
    const draft = JSON.parse(localStorage.getItem(localKey()) || "null");
    if (draft && draft.sha === baseSha && draft.content !== data.content) {
      showBanner("发现上次未保存的本地草稿。", "warn", [
        { label: "恢复草稿", onClick: () => { editor.value = draft.content; updateWords(); scheduleSave(); } },
        { label: "丢弃", onClick: () => { localStorage.removeItem(localKey()); clear(banner); } },
      ]);
    } else if (draft && draft.sha !== baseSha) {
      localStorage.removeItem(localKey());
    }
  } catch {}

  // Phase 1.2：运行面板放章节栏顶部——写的时候能看见 AI 跑到哪个节点、还活着吗。
  // 离开本页时销毁，否则轮询定时器会跟着路由泄漏。
  const runPanel = createRunPanel({ bookId, go });
  window.addEventListener("hashchange", () => runPanel.destroy(), { once: true });

  const rail = el("aside", { class: "chapter-rail", "aria-label": "章节" },
    runPanel.node,
    el("h2", { text: "章节" }),
    el("div", { class: "chapter-list" },
      ...manuscript.chapters.map((item) => el("button", {
        class: "chapter-item", type: "button",
        "aria-current": item.number === chapter ? "true" : "false",
        onclick: () => go(`#/books/${bookId}/write/${item.number}`),
      },
        el("strong", { text: item.title || `第${item.number}章` }),
        el("span", { text: `${fmtNumber(item.words)} 字${item.dirty ? " · 待重新定稿" : ""}` }))),
      data.exists ? null : el("button", {
        class: "chapter-item", type: "button", "aria-current": "true",
        onclick: () => go(`#/books/${bookId}/write/${chapter}`),
      },
        el("strong", { text: `第${chapter}章（新）` }),
        el("span", { text: "空白稿纸" }))));

  const previewButton = el("button", {
    class: "button button-small button-quiet", type: "button", text: "预览",
    onclick: () => {
      previewing = !previewing;
      editor.hidden = previewing;
      preview.hidden = !previewing;
      if (previewing) preview.innerHTML = renderMarkdown(editor.value);
      previewButton.textContent = previewing ? "编辑" : "预览";
    },
  });

  const head = el("header", { class: "write-head" },
    el("button", {
      class: "icon-button", type: "button", "aria-label": "回书房",
      onclick: () => { if (!dirty || window.confirm("有未保存的内容，确定离开？")) go("#/"); },
      text: "‹",
    }),
    el("div", { class: "write-title" },
      el("h1", { text: title }),
      el("span", { text: `第 ${chapter} 章` })),
    saveState,
    el("div", { class: "write-tools" },
      el("button", {
        class: "button button-small button-quiet", type: "button", text: "专注",
        onclick: () => { document.body.classList.toggle("focus-mode"); toast(document.body.classList.contains("focus-mode") ? "专注模式，按 Esc 退出" : "已退出专注"); },
      }),
      el("button", {
        class: "button button-small button-quiet", type: "button", text: "夜间",
        onclick: () => document.body.classList.toggle("theme-night"),
      }),
      previewButton,
      el("button", {
        class: "button button-small", type: "button", text: "保存",
        onclick: save,
      })));

  const committed = data.committed === true;
  const commitButton = el("button", {
    class: "button button-small button-primary", type: "button",
    text: committed ? (data.dirty ? "重新定稿" : "已定稿") : "定稿",
    title: committed
      ? "用当前正文重跑闸门并更新本章故事记录"
      : "把这一章写进故事账本，之后才能投稿",
    disabled: committed && !data.dirty ? "disabled" : null,
    onclick: () => runCommit(committed ? "revision" : "append"),
  });

  const foot = el("footer", { class: "write-foot" },
    el("span", { class: "small" }, wordLabel, " · 每 1.2 秒自动保存"),
    el("div", { class: "inline-fields" },
      el("button", {
        class: "button button-small button-quiet", type: "button", text: "上一章",
        disabled: chapter <= 1 ? "disabled" : null,
        onclick: () => go(`#/books/${bookId}/write/${chapter - 1}`),
      }),
      el("button", {
        class: "button button-small button-quiet", type: "button", text: "下一章",
        onclick: () => go(`#/books/${bookId}/write/${chapter + 1}`),
      }),
      commitButton));

  view.replaceChildren(el("div", { class: "write-view" },
    rail,
    el("div", { class: "paper-wrap" }, head, banner, el("div", { class: "paper" }, editor, preview), foot)));
  updateWords();
}
