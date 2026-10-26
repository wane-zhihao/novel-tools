import { api } from "../lib/api.js";
import { el, clear, fmtDate, fmtNumber, toast } from "../lib/dom.js";

export async function renderStory({ bookId, state, docPath = null }) {
  const view = document.querySelector("#view");
  view.classList.remove("wide");
  clear(view).append(el("p", { class: "empty", text: "正在整理故事资料…" }));

  let story;
  try {
    story = await api(`/api/books/${bookId}/story`);
  } catch (error) {
    clear(view).append(el("div", { class: "empty" }, el("strong", { text: "故事库打不开" }), el("p", { text: error.message })));
    return;
  }

  let activeTab = "characters";
  let activeDoc = null;
  let docData = null;
  let dirty = false;

  // 守卫注册链路（配合 app.js 的 leaveGuard 协议）：
  // - state.leaveGuard：应用内导航（侧栏/返回/抽屉关闭后的重渲染）统一在 render
  //   入口询问，一次确认。story 重建 renderStory 时重新注册即覆盖旧的。
  // - beforeunload：浏览器级离开（刷新/关标签页），confirm 帮不上，走原生拦截。
  //   页面被导航离开时由 unloadWatcher 自毁——守卫否决的导航不算离开，
  //   所以不能挂 once 的 hashchange 就卸载。
  const isStoryPage = () => {
    const parts = location.hash.replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean);
    return parts[0] === "books" && parts[2] === "story";
  };
  const onUnload = (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  };
  const unloadWatcher = () => {
    if (isStoryPage()) return;
    window.removeEventListener("beforeunload", onUnload);
    window.removeEventListener("hashchange", unloadWatcher);
  };
  window.addEventListener("beforeunload", onUnload);
  window.addEventListener("hashchange", unloadWatcher);
  if (state) state.leaveGuard = guardUnsaved;

  const tabs = el("div", { class: "tabs" });
  const list = el("div", { class: "card-list" });
  const editor = el("div", { class: "doc-editor" });
  const page = el("div", { class: "story" },
    el("header", {},
      el("h1", { class: "page-title", text: "故事" }),
      el("p", { class: "page-sub", text: "人物、世界、大纲与伏笔。文件和 AI 用的是同一份资料。" })),
    tabs,
    el("div", { class: "story-layout" }, el("div", {}, list), editor));
  view.replaceChildren(page);

  const TAB_LABELS = { characters: "人物", world: "世界", outline: "大纲", foreshadow: "伏笔", timeline: "时间线" };

  function paintTabs() {
    clear(tabs).append(...Object.entries(TAB_LABELS).map(([id, text]) => el("button", {
      class: "tab", type: "button", "aria-selected": activeTab === id ? "true" : "false",
      text,
      onclick: async () => {
        if (id === activeTab) return;
        if (!(await guardUnsaved())) return;
        activeTab = id; activeDoc = null; paint();
      },
    })));
  }

  function docsForTab() {
    if (activeTab === "characters") return story.groups.find((group) => group.id === "characters")?.docs ?? [];
    if (activeTab === "world") return story.groups.find((group) => group.id === "world")?.docs ?? [];
    if (activeTab === "outline") {
      const outline = story.groups.find((group) => group.id === "outline")?.docs ?? [];
      return [...outline, ...(story.chapter_outlines ?? [])];
    }
    if (activeTab === "timeline") return story.timeline ?? [];
    return [];
  }

  function paint() {
    paintTabs();
    clear(editor);
    clear(list);
    if (activeTab === "foreshadow") {
      const items = story.foreshadow ?? [];
      if (!items.length) list.append(el("p", { class: "muted small", text: "还没有伏笔记录。" }));
      for (const item of items) {
        list.append(el("div", { class: "list-row" },
          el("span", { text: item.text || item.name }),
          el("span", { class: "chip", text: item.status })));
      }
      editor.append(el("div", { class: "empty" }, el("p", { text: "伏笔来自追踪账本与 追踪/伏笔.md，投稿前记得核对回收。" })));
      return;
    }
    const docs = docsForTab();
    if (!docs.length) {
      list.append(el("p", { class: "muted small", text: "这一组还没有文件。" }));
      return;
    }
    for (const doc of docs) {
      list.append(el("button", {
        class: "doc-item", type: "button",
        "aria-current": doc.path === activeDoc ? "true" : "false",
        onclick: () => openDoc(doc.path),
      },
        el("strong", { text: doc.title || doc.path.split("/").at(-1) }),
        el("span", { text: doc.exists ? `${fmtNumber(doc.words)} 字` : "未创建" })));
    }
    if (activeDoc) openDoc(activeDoc, true);
  }

  let currentTextarea = null;
  let currentPath = null;
  let currentSave = null;

  async function saveDoc() {
    if (!currentTextarea || !currentSave) return false;
    try {
      const saved = await api(`/api/books/${bookId}/story/doc`, {
        method: "PUT",
        body: { path: currentPath, content: currentTextarea.value, base_sha: docData.sha256 },
      });
      docData = saved;
      dirty = false;
      const btn = currentSave;
      btn.textContent = "已保存";
      toast("故事资料已保存");
      story = await api(`/api/books/${bookId}/story`);
      return true;
    } catch (error) {
      if (error.code === "story_conflict") {
        toast("这份资料已被其他窗口修改，请刷新后再改", "error");
      } else {
        toast(error.message, "error");
      }
      return false;
    }
  }

  // dirty 守卫（Phase 2）：编辑未保存时，任何「会丢掉当前编辑内容」的动作前，
  // 先问一次。三处出口都守：页内切 tab/切文档、应用内导航（go）、浏览器级离开。
  // 此前 story.js 有 dirty 标志但没有任何出口使用它——改了字不保存、点到别的
  // 文档，内容就没了。
  async function guardUnsaved() {
    if (!dirty) return true;
    const proceed = window.confirm("这份资料还没保存。离开会丢失未保存的修改。\n\n「确定」= 先保存再离开；「取消」= 留在这里。");
    if (!proceed) return false;
    return saveDoc();   // 保存失败（如冲突）就不放行——丢的仍是没落盘的内容
  }

  async function openDoc(path, keep = false) {
    if (!keep && path !== activeDoc && dirty) {
      const saved = await guardUnsaved();
      if (!saved) return;   // 留在原文档，什么都不改
    }
    if (!keep) activeDoc = path;
    clear(editor).append(el("p", { class: "muted small", text: "正在读取…" }));
    try {
      docData = await api(`/api/books/${bookId}/story/doc?path=${encodeURIComponent(path)}`);
    } catch (error) {
      clear(editor).append(el("p", { class: "danger-text", text: error.message }));
      return;
    }
    dirty = false;
    const textarea = el("textarea", { "aria-label": path, placeholder: "在这里写这份资料…" });
    textarea.value = docData.content;
    textarea.addEventListener("input", () => { dirty = true; saveButton.textContent = "保存"; });
    const saveButton = el("button", {
      class: "button button-primary", type: "button", text: "保存",
      onclick: () => saveDoc(),
    });
    currentTextarea = textarea;
    currentPath = path;
    currentSave = saveButton;
    const title = path.split("/").at(-1).replace(/\.md$/, "");
    clear(editor).append(
      el("div", { class: "panel-head" },
        el("h2", { text: title }),
        el("div", { class: "inline-fields" },
          el("span", { class: "muted small", text: docData.exists ? `上次修改 ${fmtDate(docData.updated_at ?? "")}` : "新文件" }),
          saveButton)),
      textarea);
    for (const doc of docsForTab()) {
      const node = [...list.children].find((child) => child.textContent?.includes(doc.title || ""));
      if (node) node.setAttribute("aria-current", doc.path === activeDoc ? "true" : "false");
    }
  }

  // ?doc= 深链：把文档路径映射回所属 tab 再打开。找不到（路径过期/拼错）
  // 不假装成功——toast 说明后落在默认 tab。
  if (docPath) {
    const group = story.groups.find((g) => (g.docs ?? []).some((d) => d.path === docPath));
    if (group) {
      activeTab = group.id; activeDoc = docPath;
    } else if ((story.chapter_outlines ?? []).some((d) => d.path === docPath)) {
      activeTab = "outline"; activeDoc = docPath;
    } else if ((story.timeline ?? []).some((d) => (d.path ?? d) === docPath)) {
      activeTab = "timeline"; activeDoc = docPath;
    } else {
      toast(`深链的资料不存在：${docPath}`, "error");
    }
  }

  paint();
}
