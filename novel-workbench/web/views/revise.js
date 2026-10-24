import { api } from "../lib/api.js";
import { el, clear, fmtDate, fmtNumber, toast } from "../lib/dom.js";

const SOURCE_LABELS = {
  manual: "你保存的版本",
  "manual-before": "保存前",
  "ai-draft": "AI 初稿",
  "ai-rewrite": "AI 改写",
  "n14": "AI 定点改写",
  "n20f": "AI 补丁",
  rollback: "回滚点",
};

function label(source) {
  if (SOURCE_LABELS[source]) return SOURCE_LABELS[source];
  if (String(source).startsWith("ai-before")) return "AI 写入前";
  if (String(source).startsWith("ai-after")) return "AI 写入后";
  if (String(source).startsWith("rollback")) return "回滚点";
  return source || "版本";
}

export async function renderRevise({ bookId, go }) {
  const view = document.querySelector("#view");
  view.classList.remove("wide");
  clear(view).append(el("p", { class: "empty", text: "正在整理版本…" }));
  let manuscript;
  try {
    manuscript = await api(`/api/books/${bookId}/manuscript`);
  } catch (error) {
    clear(view).append(el("div", { class: "empty" }, el("strong", { text: "版本打不开" }), el("p", { text: error.message })));
    return;
  }
  const withVersions = manuscript.chapters.filter((chapter) => chapter.versions > 0);
  if (!withVersions.length) {
    clear(view).append(el("div", { class: "revise" },
      el("header", { class: "revise-head" },
        el("div", {}, el("h1", { class: "page-title", text: "AI 改动" }), el("p", { class: "page-sub", text: "每次 AI 写正文都会留下版本，你可以对比、回滚。" }))),
      el("div", { class: "empty" },
        el("strong", { text: "还没有版本" }),
        el("p", { text: "先写到第一章，或者让制作台跑一次 AI 初稿。" }),
        el("button", { class: "button button-primary", type: "button", onclick: () => go(`#/books/${bookId}/write/1`), text: "去写第一章" }))));
    return;
  }

  let chapter = (withVersions.find((item) => item.versions >= 2) ?? withVersions.at(-1)).number;
  let versions = [];
  let selected = null;
  let compare = null;

  const page = el("div", { class: "revise" },
    el("header", { class: "revise-head" },
      el("div", {},
        el("h1", { class: "page-title", text: "AI 改了什么" }),
        el("p", { class: "page-sub", text: "默认自动跑；每次写入都留版本。看不过去就一键回滚。" }))));
  const chapterTabs = el("div", { class: "tabs" });
  const versionList = el("div", { class: "version-list" });
  const diffHead = el("div", { class: "diff-head", text: "选择两个版本查看差异" });
  const diffBody = el("div", { class: "diff" });
  const diffPanel = el("div", { class: "diff-panel" }, diffHead, diffBody);
  page.append(chapterTabs, el("div", { class: "version-layout" }, versionList, diffPanel));
  view.replaceChildren(page);

  function paintTabs() {
    clear(chapterTabs).append(...withVersions.map((item) => el("button", {
      class: "tab", type: "button", "aria-selected": item.number === chapter ? "true" : "false",
      text: `第 ${item.number} 章 · ${item.versions} 版`,
      onclick: () => { chapter = item.number; selected = null; compare = null; load(); },
    })));
  }

  async function load() {
    paintTabs();
    clear(versionList).append(el("p", { class: "muted small", text: "正在读取版本…" }));
    try {
      const data = await api(`/api/books/${bookId}/versions?chapter=${chapter}`);
      versions = data.versions ?? [];
    } catch (error) {
      clear(versionList).append(el("p", { class: "danger-text", text: error.message }));
      return;
    }
    if (!versions.length) {
      clear(versionList).append(el("p", { class: "muted small", text: "这一章还没有版本。" }));
      return;
    }
    selected = selected && versions.some((version) => version.id === selected) ? selected : versions[0].id;
    compare = versions.at(-1).id === selected ? versions[1]?.id ?? null : versions.at(-1).id;
    paintVersions();
    await paintDiff();
  }

  function paintVersions() {
    clear(versionList).append(...versions.map((version) => el("button", {
      class: "version-item", type: "button",
      "aria-current": version.id === selected ? "true" : "false",
      onclick: () => { selected = version.id; paintVersions(); paintDiff(); },
    },
      el("strong", { text: label(version.source) }),
      el("span", { text: `${fmtDate(version.created_at)} · ${fmtNumber(version.words)} 字` }))));
  }

  async function paintDiff() {
    clear(diffBody);
    if (!selected) {
      diffHead.textContent = "选择两个版本查看差异";
      return;
    }
    const other = versions.find((version) => version.id !== selected);
    if (!other) {
      diffHead.textContent = "只有一个版本，暂无对比";
      return;
    }
    const current = versions.find((version) => version.id === selected);
    const [leftVersion, rightVersion] = current.id < other.id ? [current, other] : [other, current];
    diffHead.replaceChildren(
      el("span", { text: `${label(leftVersion?.source)} → ${label(rightVersion?.source)}` }),
      el("button", {
        class: "button button-small button-danger", type: "button", text: "回滚到较早版本",
        onclick: () => restore(leftVersion),
      }));
    try {
      const data = await api(`/api/books/${bookId}/versions/${leftVersion.id}/diff/${rightVersion.id}`);
      diffBody.append(...data.lines.map((line) => el("div", { class: `diff-line ${line.type}` },
        el("span", { class: "mark", text: line.type === "add" ? "+" : line.type === "del" ? "−" : " " }),
        el("span", { text: line.text || " " }))));
      diffHead.append(el("span", { class: "chip", text: `+${data.stats.added} / −${data.stats.removed}` }));
    } catch (error) {
      diffBody.append(el("p", { class: "danger-text", text: error.message }));
    }
  }

  async function restore(version) {
    if (!version) return;
    if (!window.confirm(`回滚到「${label(version.source)}」？当前正文会先备份，再写回这个版本。`)) return;
    try {
      await api(`/api/books/${bookId}/versions/${version.id}/restore`, { method: "POST", body: {} });
      toast("已回滚。这一章会标记为待重新定稿。");
      selected = null;
      compare = null;
      await load();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  await load();
}
