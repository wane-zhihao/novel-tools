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

// B18：手动保存的版本默认折叠，不淹没 AI 改动
function isManual(source) {
  return source === "manual" || source === "manual-before";
}

function optionText(version) {
  return `${label(version.source)} · ${fmtDate(version.created_at)}`;
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
        el("p", { text: "先写到第一章，或者在稿纸上点「让 AI 写」。" }),
        el("button", { class: "button button-primary", type: "button", onclick: () => go(`#/books/${bookId}/write/1`), text: "去写第一章" }))));
    return;
  }

  let chapter = (withVersions.find((item) => item.versions >= 2) ?? withVersions.at(-1)).number;
  let versions = [];
  let leftId = null;
  let rightId = null;
  let manualOpen = false;
  let diffToken = 0;
  // 章节 tab 上的 "AI 版本 N 个"：读取过该章后才知分组数，先缓存
  const chapterVersionInfo = new Map();

  const page = el("div", { class: "revise" },
    el("header", { class: "revise-head" },
      el("div", {},
        el("h1", { class: "page-title", text: "AI 改了什么" }),
        el("p", { class: "page-sub", text: "默认自动跑；每次写入都留版本。看不过去就一键回滚。" }))));
  const chapterTabs = el("div", { class: "tabs" });
  // B14 中期：闸门结论只读面板——作者在「改」视图原地看到本章闸门结果，
  // 闸门结论就地展示，不再跳外部控制台。
  const gatePanel = el("div", { class: "gate-panel" });
  const versionList = el("div", { class: "version-list" });
  const diffHead = el("div", { class: "diff-head" });
  const diffBody = el("div", { class: "diff" });
  const diffPanel = el("div", { class: "diff-panel" }, diffHead, diffBody);
  page.append(chapterTabs, gatePanel, el("div", { class: "version-layout" }, versionList, diffPanel));
  view.replaceChildren(page);

  let gateToken = 0;
  function gateStatusChip(report) {
    const status = report.gates?.status;
    if (status === "block") return el("span", { class: "chip chip-danger", text: "闸门未通过" });
    if (status === "pass") return el("span", { class: "chip chip-ok", text: "闸门通过" });
    if (status === "warn") return el("span", { class: "chip chip-warn", text: "有提示项" });
    return null;
  }

  async function paintGate() {
    const token = ++gateToken;
    clear(gatePanel).append(el("p", { class: "muted small", text: "正在读取闸门结论…" }));
    let report;
    try {
      report = await api(`/api/books/${bookId}/report?chapter=${chapter}`);
    } catch (error) {
      if (token !== gateToken) return;
      clear(gatePanel).append(el("p", { class: "muted small", text: `闸门结论读取失败：${error.message}` }));
      return;
    }
    if (token !== gateToken) return;
    const findings = report.findings ?? null;
    const blocking = Number(findings?.blocking_count ?? 0);
    const advisory = Number(findings?.advisory_count ?? 0);
    const rows = [el("div", { class: "gate-row" },
      el("strong", { text: `第 ${chapter} 章闸门结论` }),
      gateStatusChip(report),
      findings
        ? el("span", {
            class: `chip ${blocking > 0 ? "chip-danger" : "chip-ok"}`,
            text: `${blocking} 阻断 / ${advisory} 提示`,
          })
        : null,
      findings?.resolved_at ? el("span", { class: "chip chip-ok", text: "已定稿解决" }) : null)];
    if (report.warning) {
      rows.push(el("p", { class: "muted small", text: report.warning }));
    }
    if (!findings && !report.gates) {
      rows.push(el("p", {
        class: "muted small",
        text: "这一章还没跑过闸门。写完正文定稿时会自动跑一次闸门。",
      }));
    }
    const items = (findings?.items ?? []).slice(0, 10);
    if (items.length) {
      rows.push(el("ul", { class: "gate-items" }, ...items.map((item) => el("li", {},
        el("span", {
          class: `chip ${item.level === "B" ? "chip-danger" : "chip-warn"}`,
          text: item.level === "B" ? "阻断" : "提示",
        }),
        el("span", { text: item.name || item.criterion || item.id || "问题" }),
        item.note ? el("span", { class: "muted small", text: String(item.note).slice(0, 80) }) : null))));
    }
    clear(gatePanel).append(...rows);
  }

  function paintTabs() {
    clear(chapterTabs).append(...withVersions.map((item) => {
      const info = chapterVersionInfo.get(item.number);
      const text = info
        ? (info.ai > 0 ? `第 ${item.number} 章 · AI 版本 ${info.ai} 个` : `第 ${item.number} 章 · 手动 ${info.manual} 个`)
        : `第 ${item.number} 章 · ${item.versions} 版`;
      return el("button", {
        class: "tab", type: "button", "aria-selected": item.number === chapter ? "true" : "false",
        text,
        onclick: () => { chapter = item.number; leftId = null; rightId = null; load(); },
      });
    }));
  }

  async function load() {
    paintTabs();
    paintGate();
    clear(versionList).append(el("p", { class: "muted small", text: "正在读取版本…" }));
    try {
      const data = await api(`/api/books/${bookId}/versions?chapter=${chapter}`);
      versions = data.versions ?? [];
    } catch (error) {
      clear(versionList).append(el("p", { class: "danger-text", text: error.message }));
      return;
    }
    if (!versions.length) {
      leftId = null;
      rightId = null;
      clear(versionList).append(el("p", { class: "muted small", text: "这一章还没有版本。" }));
      paintVersions();
      await paintDiff();
      return;
    }
    const ai = versions.filter((version) => !isManual(version.source));
    const manual = versions.filter((version) => isManual(version.source));
    chapterVersionInfo.set(chapter, { ai: ai.length, manual: manual.length });
    manualOpen = false;
    // 默认对比最近两个 AI 版本；AI 不足两个时退回全量
    const base = ai.length >= 2 ? ai : versions;
    leftId = base[1]?.id ?? null;
    rightId = base[0]?.id ?? null;
    paintTabs();
    paintVersions();
    await paintDiff();
  }

  function paintVersions() {
    clear(versionList);
    const ai = versions.filter((version) => !isManual(version.source));
    const manual = versions.filter((version) => isManual(version.source));
    const item = (version) => el("button", {
      class: "version-item", type: "button",
      "aria-current": String(version.id) === String(rightId) ? "true" : "false",
      title: "点按选为对比右侧（较新版本）",
      onclick: () => pickRight(version.id),
    },
      el("strong", { text: label(version.source) }),
      el("span", { text: `${fmtDate(version.created_at)} · ${fmtNumber(version.words)} 字` }));
    versionList.append(...ai.map(item));
    if (manual.length) {
      versionList.append(el("details", { class: "version-group", open: manualOpen ? true : null },
        el("summary", { text: `手动保存 ${manual.length} 个` }),
        ...manual.map(item)));
    }
  }

  function pickRight(id) {
    rightId = id;
    if (String(leftId) === String(rightId)) {
      const other = versions.find((version) => String(version.id) !== String(id));
      leftId = other?.id ?? null;
    }
    paintVersions();
    paintDiff();
  }

  function versionSelect(value, onPick) {
    return el("select", {
      "aria-label": "选择对比版本",
      onchange: (event) => onPick(event.target.value),
    }, ...versions.map((version) => el("option", {
      value: String(version.id),
      selected: String(version.id) === String(value) ? true : null,
      text: optionText(version),
    })));
  }

  async function paintDiff() {
    const token = ++diffToken;
    clear(diffBody);
    const left = versions.find((version) => String(version.id) === String(leftId)) ?? null;
    const right = versions.find((version) => String(version.id) === String(rightId)) ?? null;
    if (versions.length < 2 || !left || !right) {
      diffHead.replaceChildren(el("span", {
        text: versions.length < 2 ? "只有一个版本，暂无对比" : "选择两个版本查看差异",
      }));
      return;
    }
    const [leftVersion, rightVersion] = Number(left.id) < Number(right.id) ? [left, right] : [right, left];
    diffHead.replaceChildren(
      el("div", { class: "diff-pickers" },
        versionSelect(leftVersion.id, (value) => {
          if (String(value) !== String(rightId)) leftId = Number(value);
          paintVersions();
          paintDiff();
        }),
        el("span", { class: "diff-arrow", text: "→" }),
        versionSelect(rightVersion.id, (value) => {
          if (String(value) !== String(leftId)) rightId = Number(value);
          paintVersions();
          paintDiff();
        })),
      el("span", { text: `${label(leftVersion.source)} → ${label(rightVersion.source)}` }),
      el("button", {
        class: "button button-small button-danger", type: "button", text: "回滚到较早版本",
        onclick: () => restore(leftVersion),
      }));
    try {
      const data = await api(`/api/books/${bookId}/versions/${leftVersion.id}/diff/${rightVersion.id}`);
      if (token !== diffToken) return;
      diffBody.append(...data.lines.map((line) => el("div", { class: `diff-line ${line.type}` },
        el("span", { class: "mark", text: line.type === "add" ? "+" : line.type === "del" ? "−" : " " }),
        el("span", { text: line.text || " " }))));
      diffHead.append(el("span", { class: "chip", text: `+${data.stats.added} / −${data.stats.removed}` }));
    } catch (error) {
      if (token !== diffToken) return;
      diffBody.append(el("p", { class: "danger-text", text: error.message }));
    }
  }

  async function restore(version) {
    if (!version) return;
    if (!window.confirm(`回滚到「${label(version.source)}」？当前正文会先备份，再写回这个版本。`)) return;
    try {
      await api(`/api/books/${bookId}/versions/${version.id}/restore`, { method: "POST", body: {} });
      toast("已回滚。这一章会标记为待重新定稿。");
      leftId = null;
      rightId = null;
      await load();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  await load();
}
