import { el, clear, fmtDate } from "../lib/dom.js";
import { api } from "../lib/api.js";
import { errorState } from "../lib/errors.js";

// 高级抽屉（Phase 2）：nodes / log / config / submissions 四个 tab。
//
// 它是**覆盖层**，不是路由页：打开/关闭/切 tab 都不重渲染底下的写页 DOM。
// 理由：写页的 textarea 里可能有未保存草稿（1.2 秒才自动存一次），任何一次
// 整页重渲染都会丢草稿。制作台此前就是第二个整页（iframe 两套状态），降为
// 抽屉正是为了消掉这第二个渲染出口。
//
// 数据全部复用既有端点；config tab 读 /api/agents（服务端已脱敏，密钥不上前端）。

const TABS = [
  { id: "nodes", label: "节点" },
  { id: "log", label: "运行日志" },
  { id: "config", label: "Agent 配置" },
  { id: "submissions", label: "投稿记录" },
];

function statusClass(status) {
  if (status === "done" || status === "pass" || status === "ok") return "adv-ok";
  if (status === "blocked" || status === "failed" || status === "fail") return "adv-bad";
  return "adv-idle";
}

async function renderNodes(panel, bookId) {
  const { nodes } = await api(`/api/books/${bookId}`);
  if (!nodes?.length) {
    panel.append(el("p", { class: "muted", text: "还没有节点记录。跑过流水线后这里会列出每个节点的执行状态。" }));
    return;
  }
  panel.append(el("table", { class: "adv-table" },
    el("thead", {}, el("tr", {},
      ...["节点", "章", "状态", "尝试", "时间"].map((h) => el("th", { text: h })))),
    el("tbody", {}, ...nodes.map((n) => el("tr", {},
      el("td", { class: "adv-mono", text: n.node_id }),
      el("td", { text: n.chapter != null ? String(n.chapter) : "—" }),
      el("td", {}, el("span", { class: `adv-chip ${statusClass(n.status)}`, text: n.status ?? "—" })),
      el("td", { text: String(n.attempts ?? 0) }),
      el("td", { class: "small muted", text: n.happened_at ? fmtDate(n.happened_at) : "—" }))))));
}

async function renderLog(panel, bookId) {
  const { runs } = await api(`/api/books/${bookId}/runs`);
  if (!runs?.length) {
    panel.append(el("p", { class: "muted", text: "还没有运行记录。" }));
    return;
  }
  panel.append(el("table", { class: "adv-table" },
    el("thead", {}, el("tr", {},
      ...["开始", "类型", "状态", "节点", "结果"].map((h) => el("th", { text: h })))),
    el("tbody", {}, ...runs.map((r) => el("tr", {},
      el("td", { class: "small", text: r.requested_at ? fmtDate(r.requested_at) : "—" }),
      el("td", { text: r.kind ?? r.action ?? "—" }),
      el("td", {}, el("span", { class: `adv-chip ${statusClass(r.status)}`, text: r.status ?? "—" })),
      el("td", { class: "adv-mono", text: r.node ?? "—" }),
      el("td", { class: "small", text: r.result?.status ?? "—" }))))));
}

async function renderConfig(panel, bookId) {
  const data = await api(`/api/agents?book=${bookId}`);
  // roles 在 config 下；model 挂在角色上（provider 只有 type/base_url）。
  // 只展示角色→provider 映射，不展示 base_url/api_key_env——那些归设置页。
  const roles = data.config?.roles ?? {};
  const providers = data.providers ?? [];
  const rows = Object.entries(roles).map(([name, role]) => {
    const provider = providers.find((p) => p.name === role?.provider);
    const kind = [provider?.type, role?.model].filter(Boolean).join(" · ") || "—";
    return el("tr", {},
      el("td", { text: name }),
      el("td", { class: "adv-mono", text: role?.provider ?? "—" }),
      el("td", { text: kind }));
  });
  if (!rows.length) {
    panel.append(el("p", { class: "muted", text: "还没有角色配置。" }));
    return;
  }
  const table = el("table", { class: "adv-table" });
  table.append(el("thead", {}, el("tr", {},
    ...["角色", "Provider", "类型 / 模型"].map((h) => el("th", { text: h })))));
  table.append(el("tbody", {}, ...rows));
  panel.append(table,
    el("p", { class: "small muted", text: "改配置请去 设置 → 高级：路径与完整 Agent 配置。此处只读（密钥从不下发到前端）。" }));
}

async function renderSubmissions(panel, bookId) {
  const { submissions } = await api(`/api/books/${bookId}/submissions`);
  if (!submissions?.length) {
    panel.append(el("p", { class: "muted", text: "还没有投稿记录。" }));
    return;
  }
  const rows = submissions.map((s) => el("tr", {},
    el("td", { text: s.platform ?? "—" }),
    el("td", { class: "small", text: (s.submitted_at ?? s.created_at) ? fmtDate(s.submitted_at ?? s.created_at) : "—" }),
    el("td", { text: s.title ?? s.name ?? "—" }),
    el("td", {}, el("span", { class: `adv-chip ${statusClass(s.status)}`, text: s.status ?? "已投" }))));
  const table = el("table", { class: "adv-table" });
  table.append(el("thead", {}, el("tr", {},
    ...["平台", "时间", "标题", "状态"].map((h) => el("th", { text: h })))));
  table.append(el("tbody", {}, ...rows));
  panel.append(table);
}

const RENDERERS = {
  nodes: renderNodes,
  log: renderLog,
  config: renderConfig,
  submissions: renderSubmissions,
};

export function createAdvancedDrawer({ bookId, tab = "nodes", onClose }) {
  const validTab = TABS.some((t) => t.id === tab) ? tab : "nodes";
  const overlay = el("div", { class: "adv-overlay", role: "dialog", "aria-modal": "true", "aria-label": "高级" });
  let dead = false;

  const panel = el("div", { class: "adv-panel" });
  const tabsBar = el("nav", { class: "adv-tabs", "aria-label": "高级页签" });

  function loadTab(id) {
    clear(panel);
    panel.append(el("p", { class: "muted", text: "加载中…" }));
    RENDERERS[id](panel, bookId).catch((error) => {
      if (dead) return;
      const state = errorState(error, { go: () => {}, bookId });
      clear(panel).append(el("p", { class: "run-hint run-hint-bad", text: `读取失败：${state.text}` }));
    });
  }

  function paintTabs(active) {
    clear(tabsBar);
    for (const t of TABS) {
      tabsBar.append(el("button", {
        class: `adv-tab${t.id === active ? " adv-tab-active" : ""}`,
        type: "button", text: t.label, "aria-current": t.id === active ? "true" : "false",
        onclick: () => { paintTabs(t.id); loadTab(t.id); },
      }));
    }
  }

  function onKey(event) {
    if (event.key === "Escape") close();
  }

  function close() {
    if (dead) return;
    dead = true;
    document.removeEventListener("keydown", onKey);
    overlay.remove();
    onClose?.();
  }

  document.addEventListener("keydown", onKey);
  overlay.append(
    el("div", { class: "adv-sheet" },
      el("header", { class: "adv-head" },
        el("h2", { text: "高级" }),
        el("button", { class: "button button-small button-quiet", type: "button", text: "关闭", onclick: close })),
      tabsBar,
      panel),
  );
  // 点遮罩关闭（点在抽屉本体上不冒泡关闭）
  overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });

  let currentTab = validTab;
  function setTab(id) {
    if (!TABS.some((t) => t.id === id) || id === currentTab) return;
    currentTab = id;
    paintTabs(id);
    loadTab(id);
  }

  paintTabs(validTab);
  loadTab(validTab);

  return {
    node: overlay,
    close,
    setTab,
    // 被路由强拆时走这里：只清理监听与 DOM，不触发 onClose（否则会把用户
    // 刚点开的目标路由改回底页）
    destroy() {
      if (dead) return;
      dead = true;
      document.removeEventListener("keydown", onKey);
      overlay.remove();
    },
  };
}
