import { api } from "../lib/api.js";
import { el, clear, fmtDate, fmtNumber, toast } from "../lib/dom.js";

export async function renderStory({ bookId }) {
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
      onclick: () => { activeTab = id; activeDoc = null; paint(); },
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

  async function openDoc(path, keep = false) {
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
      onclick: async () => {
        try {
          const saved = await api(`/api/books/${bookId}/story/doc`, {
            method: "PUT",
            body: { path, content: textarea.value, base_sha: docData.sha256 },
          });
          docData = saved;
          dirty = false;
          saveButton.textContent = "已保存";
          toast("故事资料已保存");
          story = await api(`/api/books/${bookId}/story`);
        } catch (error) {
          if (error.code === "story_conflict") {
            toast("这份资料已被其他窗口修改，请刷新后再改", "error");
          } else {
            toast(error.message, "error");
          }
        }
      },
    });
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

  paint();
}
