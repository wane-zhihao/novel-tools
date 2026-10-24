const state = {
  books: [],
  roots: [],
  dataDir: "",
  currentBookId: null,
  bookData: null,
  run: null,
  runSeq: 0,
  runEvents: [],
  runPoll: null,
  runBusy: false,
  needsResume: false,
  report: null,
  reportChapter: null,
  submissions: null,
  agents: null,
  agentsScope: "global",
  agentsDraft: "",
  settings: null,
  routeToken: 0,
  reportSeq: 0,
};

const elements = {
  bookList: document.querySelector("#bookList"),
  view: document.querySelector("#view"),
  workspaceMeta: document.querySelector("#workspaceMeta"),
  toastRegion: document.querySelector("#toastRegion"),
  newBookButton: document.querySelector("#newBookButton"),
  refreshBooksButton: document.querySelector("#refreshBooksButton"),
  settingsButton: document.querySelector("#settingsButton"),
  setupButton: document.querySelector("#setupButton"),
  dialog: document.querySelector("#newBookDialog"),
  form: document.querySelector("#newBookForm"),
  dialogClose: document.querySelector("#newBookClose"),
  dialogCancel: document.querySelector("#newBookCancel"),
  dialogSubmit: document.querySelector("#newBookSubmit"),
};

const STATUS_LABELS = {
  running: "执行中",
  stopping: "停止中",
  done: "已完成",
  blocked: "受阻断",
  failed: "失败",
  stopped: "已停止",
  max_steps: "到达步数上限",
  budget_exhausted: "预算耗尽",
  needs_resume: "待恢复",
  pending: "待办",
  skipped: "跳过",
};

const NODE_NAMES = {
  N01: "题材选择",
  N02: "文风锚点",
  N03: "选题卡",
  N04: "选题审查",
  N05: "设定",
  N06: "设定审查",
  N07: "卷纲",
  N08: "卷纲审查",
  N09d: "开篇细纲",
  N09: "单章场景卡",
  N10: "细纲闸门",
  N11: "上下文组装",
  N12: "写正文",
  N13: "平台闸门",
  N14: "定点改写",
  N15: "阶段审查",
  N16: "章末封板",
  N17: "全文整合",
  N18: "AI/退化扫描",
  N19: "文风/伏笔",
  N20: "评分卡终审",
  N20f: "单点补丁",
  N21: "投稿包打包",
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, { method = "GET", body = null } = {}) {
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
    const error = new Error(data?.error?.message ?? `请求失败（HTTP ${response.status}）`);
    error.code = data?.error?.code;
    error.details = data?.error?.details;
    throw error;
  }
  return data;
}

function toast(message, tone = "info") {
  const node = el("div", { class: "toast", "data-tone": tone, text: message });
  elements.toastRegion.append(node);
  window.setTimeout(() => node.remove(), 4200);
}

function fmtNumber(value) {
  return Number(value ?? 0).toLocaleString("zh-CN");
}

function fmtDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function nodeName(nodeId) {
  if (!nodeId) return "";
  const key = nodeId.includes(".") ? nodeId.split(".")[1] : nodeId;
  return NODE_NAMES[key] ?? "";
}

function statusChip(status) {
  const tone = status === "done" ? "chip-ok"
    : ["failed", "blocked"].includes(status) ? "chip-danger"
      : ["running", "stopping"].includes(status) ? "chip-accent"
        : ["max_steps", "skipped", "needs_resume"].includes(status) ? "chip-warn"
          : "";
  return el("span", { class: `chip ${tone}`.trim(), text: STATUS_LABELS[status] ?? status ?? "未知" });
}

function bookChip(book) {
  if (book.blocked_reason) return el("span", { class: "chip chip-danger", text: "受阻" });
  if (book.finished) return el("span", { class: "chip chip-ok", text: "完成" });
  return el("span", { class: "chip", text: book.cursor ?? "未开工" });
}

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "books" && parts[1]) {
    const id = Number(parts[1]);
    if (parts[2] === "submissions") return { view: "submissions", id };
    return { view: "book", id };
  }
  if (parts[0] === "settings") return { view: "settings" };
  if (parts[0] === "setup") return { view: "setup" };
  return { view: "home" };
}

function navigate(hash) {
  if (location.hash === hash) renderRoute();
  else location.hash = hash;
}

function stopPolling() {
  if (state.runPoll) {
    window.clearInterval(state.runPoll);
    state.runPoll = null;
  }
}

async function loadBooks({ announce = false } = {}) {
  try {
    const data = await api("/api/books");
    state.books = data.books ?? [];
    state.roots = data.roots ?? [];
    state.dataDir = data.data_dir ?? "";
    elements.workspaceMeta.textContent = state.roots.length
      ? `书库：${state.roots[0]}`
      : "未配置书库根目录";
    renderBookList();
    if (announce) toast("书库已刷新");
    return data;
  } catch (error) {
    elements.bookList.replaceChildren(el("p", { class: "loading", text: "书库读取失败" }));
    toast(error.message, "error");
    return null;
  }
}

function renderBookList() {
  elements.bookList.replaceChildren();
  if (!state.books.length) {
    elements.bookList.append(el("p", { class: "loading", text: "还没有书。点上方「新建书」开始。" }));
    return;
  }
  for (const book of state.books) {
    const active = Number(book.id) === Number(state.currentBookId);
    const button = el("button", {
      class: "book-item",
      type: "button",
      "aria-current": active ? "true" : "false",
      onclick: () => navigate(`#/books/${book.id}`),
    },
      el("span", { class: "book-item-title" },
        el("span", { text: book.title ?? "(无书名)" }),
        bookChip(book)),
      el("span", { class: "book-item-meta" },
        el("span", { text: `封板 ${book.chapters_committed}/${book.max_chapters}` }),
        el("span", { text: `${fmtNumber(book.words_total)} 字` }),
        book.active_findings ? el("span", { text: `问题 ${book.active_findings}` }) : null),
    );
    elements.bookList.append(button);
  }
}

function renderHome() {
  state.currentBookId = null;
  renderBookList();
  const totalWords = state.books.reduce((sum, book) => sum + Number(book.words_total ?? 0), 0);
  const blocked = state.books.filter((book) => book.blocked_reason).length;
  const done = state.books.filter((book) => book.finished).length;

  const view = el("div", { class: "view" },
    el("header", { class: "page-head" },
      el("div", { class: "page-title" },
        el("h1", { text: "书库" }),
        el("p", { class: "page-sub" },
          el("span", { text: `${state.books.length} 本书` }),
          el("span", { text: `累计 ${fmtNumber(totalWords)} 字` }),
          blocked ? el("span", { text: `${blocked} 本受阻` }) : null,
          done ? el("span", { text: `${done} 本完成` }) : null)),
      el("div", { class: "page-actions" },
        el("button", { class: "button button-primary", type: "button", onclick: openNewBookDialog, text: "新建书" }),
        el("button", { class: "button", type: "button", onclick: () => navigate("#/settings"), text: "Agent 配置" }))),
  );

  if (!state.books.length) {
    view.append(el("div", { class: "empty" },
      el("strong", { text: "还没有书" }),
      el("p", { text: "新建一本书，工作台会初始化目录与状态机；之后可以直接在浏览器里执行。" })));
  } else {
    const grid = el("div", { class: "stats" });
    for (const book of state.books) {
      grid.append(el("a", {
        class: "stat",
        href: `#/books/${book.id}`,
        style: "text-align:left;cursor:pointer;text-decoration:none;color:inherit;display:block",
      },
        el("dt", { text: book.title ?? "(无书名)" }),
        el("dd", {},
          `${book.chapters_committed}/${book.max_chapters} 章`,
          el("small", { text: `${fmtNumber(book.words_total)} 字` })),
        el("div", { class: "book-item-meta", style: "margin-top:8px" },
          bookChip(book),
          book.blocked_reason ? el("span", { text: book.blocked_reason.slice(0, 26) }) : null),
      ));
    }
    view.append(grid);
  }
  elements.view.replaceChildren(view);
}

function adoptRun(runInfo) {
  state.run = runInfo?.run ?? null;
  state.needsResume = Boolean(runInfo?.needs_resume);
  state.runEvents = [];
  state.runSeq = 0;
  if (state.run) {
    state.runEvents = (state.run.events ?? []).map(formatEvent);
    state.runSeq = state.run.seq ?? 0;
  }
}

function formatEvent(event) {
  switch (event.type) {
    case "run_started": return "开始执行";
    case "node_started": return `[${event.node}] ${event.role} · ${event.provider}/${event.model}（第 ${event.attempt} 次）`;
    case "tool_call": return `  工具 ${event.tool} ${event.path ?? ""}${event.ok ? "" : "（失败）"}`;
    case "node_done": return `完成 ${event.node}（${event.attempt} 次，工具 ${event.tool_steps} 步）`;
    case "node_retry": return `重试 ${event.node}：${event.reason}`;
    case "routed": return `路由 ${event.node} → ${event.routed_to}`;
    case "stop_requested": return "收到停止请求";
    case "run_finished": return `结束：${STATUS_LABELS[event.status] ?? event.status}${event.error ? ` · ${event.error}` : ""}`;
    default: return JSON.stringify(event);
  }
}

function startPolling() {
  stopPolling();
  state.runPoll = window.setInterval(pollRun, 800);
  pollRun();
}

async function pollRun() {
  const id = state.currentBookId;
  if (!id) return;
  try {
    const data = await api(`/api/books/${id}/run?since=${state.runSeq}`);
    if (!data.run) {
      stopPolling();
      return;
    }
    state.run = data.run;
    state.needsResume = Boolean(data.needs_resume);
    for (const event of data.run.events ?? []) state.runEvents.push(formatEvent(event));
    if (state.runEvents.length > 400) state.runEvents.splice(0, state.runEvents.length - 400);
    state.runSeq = data.run.seq ?? state.runSeq;
    renderRunPanel();
    if (!["running", "stopping"].includes(data.run.status)) {
      stopPolling();
      await refreshBook({ quiet: true });
    }
  } catch (error) {
    stopPolling();
    toast(`运行状态读取失败：${error.message}`, "error");
  }
}

async function refreshBook({ quiet = false } = {}) {
  const id = state.currentBookId;
  if (!id) return;
  try {
    const data = await api(`/api/books/${id}`);
    state.bookData = data;
    const route = parseRoute();
    if (route.view === "book" && Number(route.id) === Number(id)) renderBookView();
    if (!quiet) toast("已刷新");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function renderBookRoute(id) {
  const token = ++state.routeToken;
  state.currentBookId = Number(id);
  state.bookData = null;
  state.report = null;
  state.reportChapter = null;
  state.reportSeq += 1;
  renderBookList();
  elements.view.replaceChildren(el("p", { class: "loading", text: "正在读取书…" }));
  try {
    const [detail, runInfo] = await Promise.all([
      api(`/api/books/${id}`),
      api(`/api/books/${id}/run`),
    ]);
    if (token !== state.routeToken) return;
    state.bookData = detail;
    adoptRun(runInfo);
    renderBookView();
    if (state.run && ["running", "stopping"].includes(state.run.status)) startPolling();
  } catch (error) {
    if (token !== state.routeToken) return;
    elements.view.replaceChildren(el("div", { class: "empty" },
      el("strong", { text: "这本书暂时打不开" }),
      el("p", { text: error.message }),
      el("p", {}, el("a", { href: "#/", text: "返回书库" }))));
  }
}

function renderBookView() {
  const data = state.bookData;
  if (!data) return;
  const book = data.book;
  const view = el("div", { class: "view" });

  const actions = el("div", { class: "page-actions" },
    el("button", {
      class: "button", type: "button", title: "用 mock provider 演练一个节点，不调用真实模型",
      onclick: () => startRun("mock", 1), text: "演练一步",
    }),
    el("button", {
      class: "button button-primary", type: "button", title: "用配置的 provider 执行一个节点",
      onclick: () => startRun("real", 1), text: "执行一步",
    }),
    el("button", {
      class: "button", type: "button", title: "持续执行，直到收尾、阻断、预算或步数上限",
      onclick: () => startRun("real", null), text: "连续执行",
    }),
    el("button", {
      class: "button button-danger", type: "button", id: "stopRunButton",
      onclick: stopRun, text: "停止",
    }),
    el("button", {
      class: "button", type: "button", id: "resumeRunButton",
      onclick: resumeBook, text: "恢复",
    }),
    el("button", { class: "button button-quiet", type: "button", onclick: () => refreshBook(), text: "刷新" }),
    el("button", { class: "button button-quiet", type: "button", onclick: () => navigate(`#/books/${book.id}/submissions`), text: "投稿记录" }),
    el("a", { class: "button button-quiet", href: "/index.html", title: "回到书房（日常写作界面）", text: "回书房" }),
  );

  view.append(
    el("header", { class: "page-head" },
      el("div", { class: "page-title" },
        el("h1", { text: book.title ?? "(无书名)" }),
        el("p", { class: "page-sub" },
          el("span", { text: `题材 ${book.genre ?? "-"}` }),
          el("span", { text: `当前节点 ${book.cursor ?? "-"}` }),
          bookChip(book),
          data.sync_warning ? el("span", { class: "chip chip-warn", text: "同步异常" }) : null)),
      actions),
    data.sync_warning ? el("div", { class: "banner", text: data.sync_warning }) : null,
    book.blocked_reason ? el("div", { class: "banner", text: `受阻：${book.blocked_reason}` }) : null,
  );

  const stats = el("dl", { class: "stats" },
    statCard("已封板", `${book.chapters_committed}`, `章 / 共 ${book.max_chapters}`),
    statCard("正文", fmtNumber(book.words_total), `字 / 目标 ${fmtNumber(book.target_words)}`),
    statCard("章节文件", `${data.chapters.length}`, "篇"),
    statCard("现行阻断", `${book.blocking_findings}`, `问题 ${book.active_findings}`),
    statCard("改写轮次", `${Object.values(book.fix_rounds ?? {}).reduce((sum, n) => sum + Number(n), 0)}`, "次"),
  );
  view.append(stats);

  const left = el("div", { class: "col" },
    buildRunCard(),
    buildNodesCard(data.nodes),
    buildRunsCard(book, data),
  );
  const right = el("div", { class: "col" },
    buildChaptersCard(data.chapters),
    buildFindingsCard(data.findings),
  );
  view.append(el("div", { class: "columns" }, left, right));
  elements.view.replaceChildren(view);

  renderRunPanel();
  updateRunButtons();
  if (state.reportChapter && state.report) renderReport();
}

function statCard(label, value, suffix) {
  return el("div", { class: "stat" },
    el("dt", { text: label }),
    el("dd", {}, value, suffix ? el("small", { text: suffix }) : null));
}

function buildRunCard() {
  return el("section", { class: "card", id: "runCard" },
    el("div", { class: "card-head" },
      el("h2", { text: "执行" }),
      el("span", { class: "hint", text: "真实执行使用 Agent 配置；演练使用 mock，不调用模型" })),
    el("div", { class: "run-status", id: "runStatus" }),
    el("div", { class: "page-actions", id: "runActions" }),
    el("pre", { class: "run-log", id: "runLog", tabindex: "0" }),
    el("div", { class: "run-result", id: "runResult", hidden: "hidden" }),
  );
}

function renderRunPanel() {
  const statusNode = document.querySelector("#runStatus");
  const logNode = document.querySelector("#runLog");
  const resultNode = document.querySelector("#runResult");
  if (!statusNode || !logNode || !resultNode) return;

  const run = state.run;
  statusNode.replaceChildren();
  if (run) {
    statusNode.append(statusChip(run.status));
    statusNode.append(el("span", { class: "node-meta", text: `${run.mode === "mock" ? "演练" : "按配置"} · 开始于 ${fmtDate(run.started_at)}` }));
    if (run.finished_at) statusNode.append(el("span", { class: "node-meta", text: `结束于 ${fmtDate(run.finished_at)}` }));
    if (run.error) statusNode.append(el("span", { class: "chip chip-danger", text: run.error }));
  } else {
    statusNode.append(el("span", { class: "chip", text: "空闲" }));
  }
  if (state.needsResume && !(run && ["running", "stopping"].includes(run.status))) {
    statusNode.append(el("span", { class: "chip chip-warn", text: "上次执行被中断，需先恢复" }));
  }

  const atBottom = logNode.scrollHeight - logNode.scrollTop - logNode.clientHeight < 40;
  logNode.textContent = state.runEvents.length
    ? state.runEvents.join("\n")
    : "尚未执行。点「演练一步」可以用 mock 走通链路，不产生模型费用。";
  if (atBottom) logNode.scrollTop = logNode.scrollHeight;

  if (run && !["running", "stopping"].includes(run.status)) {
    const summary = [];
    summary.push(`状态：${STATUS_LABELS[run.status] ?? run.status}`);
    if (run.result?.book) {
      summary.push(`游标：${run.result.book.cursor ?? "-"} · 封板 ${run.result.book.chapters_committed ?? 0} 章 / ${fmtNumber(run.result.book.words_total)} 字`);
    }
    if (run.result?.steps?.length) {
      summary.push(`本轮节点：${run.result.steps.map((step) => step.node).join(" → ")}`);
      summary.push(`LLM 轮次：${run.result.llm_rounds ?? 0} · tokens ${run.result.usage?.prompt ?? 0}/${run.result.usage?.completion ?? 0}`);
    }
    if (run.error) summary.push(`原因：${run.error}`);
    resultNode.textContent = summary.join("\n");
    resultNode.hidden = false;
  } else {
    resultNode.hidden = true;
    resultNode.textContent = "";
  }
  updateRunButtons();
}

function updateRunButtons() {
  const active = Boolean(state.run && ["running", "stopping"].includes(state.run.status));
  for (const button of elements.view.querySelectorAll(".page-actions .button")) {
    const text = button.textContent;
    if (text === "停止") {
      button.disabled = !active || state.run?.status === "stopping";
      continue;
    }
    if (text === "恢复") {
      button.disabled = active || !state.needsResume || state.runBusy;
      continue;
    }
    if (["演练一步", "执行一步", "连续执行"].includes(text)) {
      button.disabled = active || state.runBusy || state.needsResume;
    }
  }
}

async function startRun(mode, maxSteps) {
  if (state.runBusy) return;
  const id = state.currentBookId;
  if (!id) return;
  state.runBusy = true;
  updateRunButtons();
  try {
    const body = { mode };
    if (maxSteps) body.max_steps = maxSteps;
    const data = await api(`/api/books/${id}/run`, { method: "POST", body });
    state.run = data.run;
    state.runEvents = [];
    state.runSeq = 0;
    renderRunPanel();
    startPolling();
    if (data.warnings?.length) toast(data.warnings[0]);
  } catch (error) {
    if (error.code === "needs_resume") {
      state.needsResume = true;
      renderRunPanel();
    }
    toast(error.message, "error");
  } finally {
    state.runBusy = false;
    updateRunButtons();
  }
}

async function resumeBook() {
  const id = state.currentBookId;
  if (!id) return;
  state.runBusy = true;
  updateRunButtons();
  try {
    await api(`/api/books/${id}/resume`, { method: "POST", body: {} });
    state.needsResume = false;
    toast("已恢复中断的节点，可以继续执行");
    await refreshBook({ quiet: true });
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.runBusy = false;
    updateRunButtons();
  }
}

async function stopRun() {
  const id = state.currentBookId;
  if (!id) return;
  try {
    await api(`/api/books/${id}/stop`, { method: "POST", body: {} });
    toast("已请求停止，等待当前步骤收尾");
  } catch (error) {
    toast(error.message, "error");
  }
}

function buildNodesCard(nodes) {
  const card = el("section", { class: "card", id: "nodesCard" },
    el("div", { class: "card-head" },
      el("h2", { text: "流程节点" }),
      el("span", { class: "hint", text: `${nodes.length} 个已记录` })));
  const filter = el("select", { "aria-label": "节点筛选" },
    el("option", { value: "all", text: "全部" }),
    el("option", { value: "active", text: "未完成" }),
    el("option", { value: "failed", text: "失败/阻断" }));
  card.querySelector(".card-head").append(filter);
  const list = el("div", { class: "node-list", id: "nodeList" });
  card.append(list);

  const render = () => {
    const mode = filter.value;
    list.replaceChildren();
    const visible = nodes.filter((node) => {
      if (mode === "active") return !["done", "skipped"].includes(node.status);
      if (mode === "failed") return ["failed", "running"].includes(node.status);
      return true;
    });
    if (!visible.length) {
      list.append(el("p", { class: "loading", text: "没有符合条件的节点" }));
      return;
    }
    for (const node of visible) {
      list.append(el("div", { class: "node-row", "data-status": node.status ?? "" },
        el("span", { class: "node-id", text: node.node_id }),
        el("span", {},
          el("span", { class: "node-name", text: nodeName(node.node_id) || "—" }),
          el("span", { class: "node-meta", text: node.happened_at ? ` · ${fmtDate(node.happened_at)}` : "" })),
        statusChip(node.status)));
    }
  };
  filter.addEventListener("change", render);
  render();
  return card;
}

function buildChaptersCard(chapters) {
  const card = el("section", { class: "card", id: "chaptersCard" },
    el("div", { class: "card-head" },
      el("h2", { text: "章节" }),
      el("span", { class: "hint", text: "点「闸门」查看实时指标" })));
  const list = el("div", { class: "chapter-list" });
  if (!chapters.length) {
    list.append(el("p", { class: "loading", text: "还没有正文" }));
  }
  for (const chapter of chapters) {
    list.append(el("div", { class: "chapter-row" },
      el("span", { class: "chapter-no", text: String(chapter.number).padStart(3, "0") }),
      el("span", { class: "chapter-title", title: chapter.title, text: chapter.title ?? "" }),
      el("span", { class: "chapter-words", text: `${fmtNumber(chapter.words)} 字` }),
      el("button", {
        class: "button button-small", type: "button", text: "闸门",
        onclick: () => openReport(chapter.number),
      })));
  }
  card.append(list, el("div", { id: "reportPanel" }));
  return card;
}

async function openReport(chapter) {
  const bookId = state.currentBookId;
  const requestId = ++state.reportSeq;
  state.reportChapter = chapter;
  state.report = null;
  const panel = document.querySelector("#reportPanel");
  if (panel) panel.replaceChildren(el("p", { class: "loading", text: `正在跑第 ${chapter} 章闸门…` }));
  let report;
  try {
    report = await api(`/api/books/${bookId}/report?chapter=${chapter}`);
  } catch (error) {
    if (requestId === state.reportSeq && state.currentBookId === bookId && panel) {
      panel.replaceChildren(el("p", { class: "report-warning", text: error.message }));
    }
    return;
  }
  if (requestId !== state.reportSeq || state.currentBookId !== bookId) return;
  state.report = report;
  renderReport();
}

function renderReport() {
  const panel = document.querySelector("#reportPanel");
  if (!panel || !state.report) return;
  const report = state.report;
  const box = el("div", { class: "report" },
    el("h3", { text: `第 ${report.chapter} 章 · 来源 ${report.source}` }));
  if (report.gates) {
    box.append(el("div", { class: "run-status" },
      el("span", { class: "chip chip-ok", text: `status ${report.gates.status ?? "-"}` }),
      el("span", { class: "chip", text: `blocked ${report.gates.blocked_count ?? 0}` }),
      el("span", { class: "chip", text: `advisory ${report.gates.advisory_count ?? 0}` })));
    const metrics = el("div", { class: "report-metrics" });
    for (const [key, value] of Object.entries(report.gates.metrics ?? {})) {
      metrics.append(el("span", { text: `${key}: ${value}` }));
    }
    box.append(metrics);
  }
  if (report.warning) box.append(el("p", { class: "report-warning", text: report.warning }));
  const items = report.findings?.items ?? [];
  if (items.length) {
    box.append(el("div", { class: "list", style: "margin-top:12px" }));
    for (const item of items) {
      box.append(el("div", { class: "finding", "data-level": item.level ?? "A" },
        el("div", { class: "finding-head" },
          el("span", { class: "chip", text: item.level ?? "-" }),
          el("span", { class: "finding-title", text: item.name ?? item.criterion ?? "问题" })),
        item.note ? el("p", { class: "finding-note", text: item.note }) : null,
        item.quote ? el("p", { class: "finding-quote", text: `“${item.quote}”` }) : null));
    }
  } else if (report.source !== "none") {
    box.append(el("p", { class: "loading", text: "本章没有已存 findings。" }));
  }
  panel.replaceChildren(box);
}

function buildFindingsCard(findings) {
  const card = el("section", { class: "card" },
    el("div", { class: "card-head" },
      el("h2", { text: "问题清单" }),
      el("span", { class: "hint", text: "B 级阻断，A 级仅记录" })));
  const active = findings.filter((item) => item.active === 1);
  const resolved = findings.filter((item) => item.active !== 1);
  if (!active.length && !resolved.length) {
    card.append(el("p", { class: "loading", text: "还没有 findings。" }));
    return card;
  }
  for (const item of active) card.append(renderFinding(item));
  if (resolved.length) {
    const details = el("details", { style: "margin-top:12px" },
      el("summary", { style: "cursor:pointer;color:var(--ink-3);font-size:12px", text: `已解决 ${resolved.length} 条` }));
    for (const item of resolved) details.append(renderFinding(item));
    card.append(details);
  }
  return card;
}

function renderFinding(item) {
  const chapterLabel = item.scope === "endgame" ? "全书" : `第 ${item.chapter} 章`;
  return el("div", {
    class: "finding",
    "data-level": item.level ?? "A",
    "data-resolved": item.active === 1 ? "false" : "true",
  },
    el("div", { class: "finding-head" },
      el("span", { class: "chip", text: item.level ?? "-" }),
      el("span", { class: "finding-title", text: item.name ?? item.criterion ?? "问题" }),
      el("span", { class: "chip", text: chapterLabel }),
      el("span", { class: "chip", text: item.source ?? "-" }),
      item.active !== 1 ? el("span", { class: "chip chip-ok", text: "已解决" }) : null),
    item.note ? el("p", { class: "finding-note", text: item.note }) : null,
    item.fix && item.fix !== item.note ? el("p", { class: "finding-note", text: `修复：${item.fix}` }) : null,
    item.quote ? el("p", { class: "finding-quote", text: `“${item.quote}”` }) : null);
}

function buildRunsCard(book, data) {
  const card = el("section", { class: "card" },
    el("div", { class: "card-head" },
      el("h2", { text: "运行记录" }),
      el("span", { class: "hint", text: "最近 10 次" })));
  const last = data.book.llm_rounds ?? 0;
  card.append(el("p", { class: "form-note", text: `累计 LLM 轮次 ${last}` }));
  const list = el("div", { class: "list", style: "margin-top:10px" });
  card.append(list);
  api(`/api/books/${book.id}/runs`).then((result) => {
    const runs = (result.runs ?? []).slice(0, 10);
    if (!runs.length) {
      list.append(el("p", { class: "loading", text: "还没有运行记录" }));
      return;
    }
    for (const run of runs) {
      const status = run.status
        ?? (run.action === "agent_run"
          ? (run.result?.status ?? (run.exit_code === 0 ? "done" : "failed"))
          : (run.exit_code === 0 ? "done" : "failed"));
      list.append(el("div", { class: "list-row" },
        el("span", {}, statusChip(status), el("span", { class: "mono", text: ` ${run.node ?? run.action}` })),
        el("span", { class: "mono", text: fmtDate(run.executed_at ?? run.requested_at) })));
    }
  }).catch(() => {
    list.append(el("p", { class: "loading", text: "运行记录读取失败" }));
  });
  return card;
}

async function renderSubmissionsRoute(id) {
  const token = ++state.routeToken;
  state.currentBookId = Number(id);
  renderBookList();
  elements.view.replaceChildren(el("p", { class: "loading", text: "正在读取投稿记录…" }));
  try {
    const [detail, data] = await Promise.all([
      api(`/api/books/${id}`),
      api(`/api/books/${id}/submissions`),
    ]);
    if (token !== state.routeToken) return;
    state.bookData = detail;
    state.submissions = data.submissions ?? [];
    renderSubmissionsView();
  } catch (error) {
    if (token !== state.routeToken) return;
    elements.view.replaceChildren(el("div", { class: "empty" },
      el("strong", { text: "投稿记录打不开" }),
      el("p", { text: error.message })));
  }
}

function renderSubmissionsView() {
  const book = state.bookData.book;
  const view = el("div", { class: "view" },
    el("header", { class: "page-head" },
      el("div", { class: "page-title" },
        el("h1", { text: "投稿记录" }),
        el("p", { class: "page-sub", text: `${book.title ?? ""} · 编辑反馈是下一轮校准的真数据` })),
      el("div", { class: "page-actions" },
        el("button", { class: "button", type: "button", onclick: () => navigate(`#/books/${book.id}`), text: "返回书" }))));

  const form = el("form", { class: "card form-grid", id: "submissionForm" },
    el("div", { class: "card-head" }, el("h2", { text: "新增投稿" })),
    el("div", { class: "field-row" },
      el("label", { class: "field" }, el("span", { text: "渠道" }),
        el("input", { name: "channel", type: "text", maxlength: "60", placeholder: "七猫 / 番茄", required: "required" })),
      el("label", { class: "field" }, el("span", { text: "编辑" }),
        el("input", { name: "editor", type: "text", maxlength: "60", placeholder: "可选" })),
      el("label", { class: "field" }, el("span", { text: "日期" }),
        el("input", { name: "submitted_at", type: "date" }))),
    el("label", { class: "field" }, el("span", { text: "状态" }),
      el("select", { name: "status" },
        ...["已投稿", "待回复", "编辑反馈", "过稿", "拒稿"].map((value) => el("option", { value, text: value })))),
    el("label", { class: "field" }, el("span", { text: "编辑反馈 / 备注" }),
      el("textarea", { name: "feedback", maxlength: "20000", placeholder: "原话记录，便于后续校准" })),
    el("label", { class: "field" }, el("span", { text: "下一步" }),
      el("input", { name: "next_action", type: "text", maxlength: "500", placeholder: "例如：按反馈改前三章" })),
    el("div", { class: "page-actions" },
      el("button", { class: "button button-primary", type: "submit", text: "保存投稿记录" })));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    if (!data.channel?.trim()) {
      toast("渠道不能为空", "error");
      return;
    }
    try {
      await api(`/api/books/${book.id}/submissions`, { method: "POST", body: data });
      toast("投稿记录已保存");
      const refreshed = await api(`/api/books/${book.id}/submissions`);
      state.submissions = refreshed.submissions ?? [];
      renderSubmissionsView();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  view.append(form);

  const list = el("section", { class: "card" },
    el("div", { class: "card-head" },
      el("h2", { text: "历史" }),
      el("span", { class: "hint", text: `${state.submissions.length} 条` })));
  if (!state.submissions.length) {
    list.append(el("p", { class: "loading", text: "还没有投稿记录。" }));
  }
  for (const submission of state.submissions) {
    list.append(renderSubmission(submission, book.id));
  }
  view.append(list);
  elements.view.replaceChildren(view);
}

function renderSubmission(submission, bookId) {
  const card = el("div", { class: "finding", style: "margin-top:10px" },
    el("div", { class: "finding-head" },
      el("span", { class: "chip", text: submission.status ?? "未标注" }),
      el("span", { class: "finding-title", text: submission.channel ?? "未填渠道" }),
      submission.editor ? el("span", { class: "chip", text: `编辑 ${submission.editor}` }) : null,
      el("span", { class: "chip", text: submission.submitted_at ? fmtDate(submission.submitted_at) : "未填日期" })),
    submission.feedback ? el("p", { class: "finding-note", text: submission.feedback }) : null);

  const status = el("select", { "aria-label": "状态" },
    ...["已投稿", "待回复", "编辑反馈", "过稿", "拒稿"].map((value) => el("option", {
      value, text: value, selected: value === submission.status ? "selected" : null,
    })));
  const next = el("input", { type: "text", maxlength: "500", value: submission.next_action ?? "", "aria-label": "下一步" });
  const save = el("button", {
    class: "button button-small", type: "button", text: "保存",
    onclick: async () => {
      try {
        await api(`/api/books/${bookId}/submissions/${encodeURIComponent(submission.external_id)}`, {
          method: "PUT",
          body: { status: status.value, next_action: next.value },
        });
        toast("已更新");
        const refreshed = await api(`/api/books/${bookId}/submissions`);
        state.submissions = refreshed.submissions ?? [];
        renderSubmissionsView();
      } catch (error) {
        toast(error.message, "error");
      }
    },
  });
  card.append(el("div", { class: "field-row", style: "margin-top:8px" },
    el("label", { class: "field" }, el("span", { text: "状态" }), status),
    el("label", { class: "field" }, el("span", { text: "下一步" }), next),
    el("div", { class: "field", style: "justify-content:flex-end" }, save)));
  return card;
}

const SOURCE_LABELS = {
  env: "环境变量",
  cli: "启动参数",
  user: "本地设置",
  default: "仓库默认",
  derived: "由目录推导",
  auto: "自动检测",
  none: "未配置",
};

function pathRow(label, value, exists, source) {
  return el("div", { class: "list-row" },
    el("span", {}, el("strong", { text: label }), el("span", { class: "mono", text: value ? ` ${value}` : " —" })),
    el("span", { class: "run-status", style: "margin:0" },
      el("span", { class: "chip", text: SOURCE_LABELS[source] ?? source ?? "-" }),
      exists === undefined ? null : el("span", {
        class: `chip ${exists ? "chip-ok" : "chip-warn"}`,
        text: exists ? "已存在" : "不存在",
      })));
}

async function renderSetupRoute() {
  const token = ++state.routeToken;
  elements.view.replaceChildren(el("p", { class: "loading", text: "正在读取路径设置…" }));
  try {
    const data = await api("/api/settings");
    if (token !== state.routeToken) return;
    state.settings = data.settings;
    renderSetupView(data.version);
  } catch (error) {
    if (token !== state.routeToken) return;
    elements.view.replaceChildren(el("div", { class: "empty" },
      el("strong", { text: "路径设置读取失败" }),
      el("p", { text: error.message })));
  }
}

function setupField(label, name, value, { placeholder = "", required = false, hint = "" } = {}) {
  return el("label", { class: "field" },
    el("span", {}, label, hint ? el("span", { class: "form-note", text: ` · ${hint}` }) : null),
    el("input", {
      name,
      type: "text",
      value: value ?? "",
      placeholder,
      required: required ? "required" : null,
    }));
}

function renderSetupView(version) {
  const settings = state.settings;
  if (!settings) return;
  const view = el("div", { class: "view" },
    el("header", { class: "page-head" },
      el("div", { class: "page-title" },
        el("h1", { text: settings.needs_setup ? "首次运行设置" : "路径设置" }),
        el("p", { class: "page-sub" },
          el("span", { text: `工作台 ${version ?? ""}`.trim() }),
          el("span", { text: `数据目录 ${settings.data_dir}` }),
          el("span", { text: "设置只保存在数据目录，不写入仓库" }))),
      el("div", { class: "page-actions" },
        el("button", { class: "button button-quiet", type: "button", onclick: () => navigate("#/"), text: "稍后设置" }))));

  const status = el("section", { class: "card" },
    el("div", { class: "card-head" },
      el("h2", { text: "当前路径" }),
      el("span", { class: "hint", text: settings.user_settings_present ? "已有本地设置" : "尚未生成本地设置" })));
  const list = el("div", { class: "list" });
  settings.books_roots.forEach((root, index) => {
    list.append(pathRow("书库根目录", root, settings.exists.books_roots[index], settings.sources.books_roots));
  });
  if (!settings.books_roots.length) {
    list.append(pathRow("书库根目录", "", false, settings.sources.books_roots));
  }
  list.append(pathRow("Python", settings.python, settings.exists.python, settings.sources.python));
  list.append(pathRow("story-long-write 技能目录", settings.story_long_write, Boolean(settings.story_long_write), settings.sources.story_long_write));
  list.append(pathRow("autopilot.py", settings.autopilot, settings.exists.autopilot, settings.sources.autopilot));
  list.append(pathRow("Node", settings.node, settings.exists.node, settings.sources.node));
  list.append(pathRow("gate_runner.py", settings.gate_runner, settings.exists.gate_runner, settings.sources.gate_runner));
  list.append(pathRow("平台目录", settings.platform_dir, settings.exists.platform_dir, settings.sources.platform_dir));
  status.append(list);
  if (settings.errors.length || settings.warnings.length) {
    const box = el("div", { style: "margin-top:12px" });
    for (const error of settings.errors) box.append(el("p", { class: "report-warning", text: `错误：${error}` }));
    for (const warning of settings.warnings) box.append(el("p", { class: "form-note", text: `提示：${warning}` }));
    status.append(box);
  }
  view.append(status);

  // 仓库默认值可能来自另一台机器；不存在时只做占位提示，不提交无效路径
  const pythonValue = settings.exists.python || settings.sources.python === "user" ? settings.python : "";
  const gateValue = settings.exists.gate_runner || settings.sources.gate_runner === "user"
    ? settings.gate_runner
    : "";
  const platformValue = settings.exists.platform_dir || settings.sources.platform_dir === "user"
    ? settings.platform_dir
    : "";

  const form = el("form", { class: "card form-grid" },
    el("div", { class: "card-head" }, el("h2", { text: "路径配置" })),
    setupField("书库根目录", "books_root", settings.books_root, {
      required: true,
      placeholder: "例如 D:\\小说\\works",
      hint: "所有书都放在这个目录下",
    }),
    setupField("Python 可执行文件", "python", pythonValue, {
      required: true,
      placeholder: settings.python ?? "例如 C:\\Python313\\python.exe",
      hint: "需要 3.10 以上",
    }),
    setupField("story-long-write 技能目录", "story_long_write", settings.story_long_write, {
      required: true,
      hint: "提供 storyctl / tracking_commit / 细纲校验脚本",
      placeholder: "例如 C:\\Users\\你\\.qoder\\skills\\story-long-write",
    }),
    setupField("Node 可执行文件", "node", settings.exists.node ? settings.node : "", {
      hint: "留空使用当前运行服务端的 Node",
      placeholder: settings.node ?? "",
    }),
    setupField("autopilot.py", "autopilot", settings.autopilot, {
      required: true,
      hint: "默认使用仓库自带脚本",
    }),
    setupField("gate_runner.py（可选）", "gate_runner", gateValue, {
      hint: "用于实时闸门报告",
      placeholder: settings.gate_runner ?? "留空则只显示已存 findings",
    }),
    setupField("平台目录（可选）", "platform_dir", platformValue, {
      hint: "七猫平台语料与闸门配置所在目录",
      placeholder: settings.platform_dir ?? "",
    }),
    el("label", { class: "field", style: "flex-direction:row;align-items:center;gap:8px" },
      el("input", { name: "create_books_root", type: "checkbox", checked: "checked" }),
      el("span", { text: "书库目录不存在时自动创建" })),
    el("p", { class: "form-note", id: "setupError" }) ,
    el("div", { class: "page-actions" },
      el("button", { class: "button button-primary", type: "submit", text: "保存并进入工作台" })));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const body = {
      books_root: data.get("books_root"),
      python: data.get("python"),
      story_long_write: data.get("story_long_write"),
      autopilot: data.get("autopilot"),
      create_books_root: data.get("create_books_root") === "on",
    };
    for (const key of ["node", "gate_runner", "platform_dir"]) {
      const value = data.get(key);
      if (value) body[key] = value;
    }
    const errorBox = form.querySelector("#setupError");
    errorBox.textContent = "";
    try {
      const result = await api("/api/settings", { method: "PUT", body });
      state.settings = result.settings;
      await loadBooks();
      toast("路径已保存");
      navigate("#/");
    } catch (error) {
      const detail = error.details?.errors?.join("；");
      errorBox.textContent = detail ? `${error.message}：${detail}` : error.message;
      toast("路径未保存，请检查输入", "error");
    }
  });
  view.append(form);

  const providerCard = el("section", { class: "card" },
    el("div", { class: "card-head" }, el("h2", { text: "模型与密钥" })),
    el("p", { class: "finding-note", text: "默认使用 mock provider，不需要密钥即可演练。" }),
    el("p", { class: "finding-note", text: "接真实模型：在 Agent 配置里选择 provider，并把密钥放到环境变量（例如 DEEPSEEK_API_KEY）。" }),
    el("div", { class: "page-actions", style: "margin-top:10px" },
      el("button", { class: "button", type: "button", onclick: () => navigate("#/settings"), text: "打开 Agent 配置" })));
  view.append(providerCard);

  elements.view.replaceChildren(view);
}

async function renderSettingsRoute() {
  const token = ++state.routeToken;
  const routeBook = state.currentBookId;
  state.agentsScope = routeBook ? "book" : "global";
  state.agents = null;
  elements.view.replaceChildren(el("p", { class: "loading", text: "正在读取 Agent 配置…" }));
  await loadAgents();
  if (token !== state.routeToken) return;
  renderSettingsView();
}

async function loadAgents() {
  const query = state.agentsScope === "book" && state.currentBookId ? `?book=${state.currentBookId}` : "";
  try {
    state.agents = await api(`/api/agents${query}`);
    state.agentsDraft = JSON.stringify(state.agents.config, null, 2);
  } catch (error) {
    toast(error.message, "error");
    state.agents = null;
  }
}

function renderSettingsView() {
  const view = el("div", { class: "view" },
    el("header", { class: "page-head" },
      el("div", { class: "page-title" },
        el("h1", { text: "Agent 配置" }),
        el("p", { class: "page-sub", text: "密钥只认环境变量；这里只保存密钥的变量名" })),
      el("div", { class: "page-actions" },
        el("button", { class: "button", type: "button", onclick: () => navigate("#/setup"), text: "路径设置" }),
        el("button", { class: "button", type: "button", onclick: () => navigate(state.currentBookId ? `#/books/${state.currentBookId}` : "#/"), text: "返回" }))));
  if (!state.agents) {
    view.append(el("div", { class: "empty" }, el("strong", { text: "配置读取失败" })));
    elements.view.replaceChildren(view);
    return;
  }

  const scopeBar = el("div", { class: "page-actions" },
    el("button", {
      class: "button", type: "button", "aria-pressed": state.agentsScope === "global" ? "true" : "false",
      onclick: () => switchScope("global"), text: "全局默认",
    }),
    el("button", {
      class: "button", type: "button", "aria-pressed": state.agentsScope === "book" ? "true" : "false",
      disabled: state.currentBookId ? null : "disabled",
      onclick: () => switchScope("book"), text: "当前书覆盖",
    }));
  view.append(scopeBar);

  const providerCard = el("section", { class: "card" },
    el("div", { class: "card-head" }, el("h2", { text: "Provider 状态" })),
    el("div", { class: "list" }));
  for (const provider of state.agents.providers ?? []) {
    const status = el("span", {
      class: `chip ${provider.configured ? "chip-ok" : "chip-warn"}`,
      text: provider.optional
        ? "密钥可选（本地）"
        : provider.configured
          ? `已配置${provider.source === "store" ? "（本地存储）" : provider.source === "env" ? "（环境变量）" : ""}`
          : `未配置 ${provider.env ?? ""}`,
    });
    const row = el("div", { class: "list-row" },
      el("span", {}, el("strong", { text: provider.name }), el("span", { class: "mono", text: ` · ${provider.type}` })),
      status);
    if (provider.type === "openai") {
      const input = el("input", {
        type: "password",
        autocomplete: "off",
        placeholder: provider.configured ? "已配置；输入可替换" : "粘贴 API Key",
        "aria-label": `${provider.name} API Key`,
        style: "min-width:180px;flex:1",
      });
      const save = el("button", {
        class: "button button-small", type: "button", text: "保存密钥",
        onclick: async () => {
          if (!input.value.trim()) {
            toast("密钥不能为空", "error");
            return;
          }
          try {
            await api(`/api/providers/${encodeURIComponent(provider.name)}/secret`, {
              method: "PUT",
              body: { api_key: input.value },
            });
            input.value = "";
            toast("密钥已保存");
            await refreshAgentsView();
          } catch (error) {
            toast(error.message, "error");
          }
        },
      });
      const remove = el("button", {
        class: "button button-small", type: "button", text: "删除",
        disabled: provider.source !== "store" ? "disabled" : null,
        onclick: async () => {
          try {
            await api(`/api/providers/${encodeURIComponent(provider.name)}/secret`, { method: "DELETE", body: {} });
            toast("本地密钥已删除");
            await refreshAgentsView();
          } catch (error) {
            toast(error.message, "error");
          }
        },
      });
      row.append(el("div", { class: "field-row", style: "flex:1;min-width:220px" }, input, save, remove));
    }
    providerCard.append(row);
  }
  if (state.agents.secrets && !state.agents.secrets.encrypted) {
    providerCard.append(el("p", { class: "report-warning", text: "当前系统不是 Windows：密钥只能以 0600 权限的明文文件保存，不建议用于共享机器。" }));
  }
  view.append(providerCard);

  if (state.agents.warnings?.length || state.agents.errors?.length) {
    const banner = el("section", { class: "card" },
      el("div", { class: "card-head" }, el("h2", { text: "校验结果" })));
    for (const warning of state.agents.warnings ?? []) {
      banner.append(el("p", { class: "report-warning", text: `警告：${warning}` }));
    }
    for (const error of state.agents.errors ?? []) {
      banner.append(el("p", { class: "report-warning", text: `错误：${error}` }));
    }
    view.append(banner);
  }

  const editor = el("section", { class: "card" },
    el("div", { class: "card-head" },
      el("h2", { text: state.agentsScope === "book" ? "本书配置（与全局合并）" : "全局配置" }),
      el("span", { class: "hint", text: state.agents.override_path ? "已存在本书覆盖文件" : "" })),
    el("textarea", { class: "code-editor", id: "agentConfigEditor", spellcheck: "false" }),
    el("div", { class: "page-actions", style: "margin-top:12px" },
      el("button", { class: "button button-primary", type: "button", onclick: saveAgents, text: "保存" }),
      el("button", { class: "button", type: "button", onclick: formatAgents, text: "格式化" }),
      el("button", { class: "button", type: "button", onclick: resetAgentsDraft, text: "还原" })));
  view.append(editor);
  elements.view.replaceChildren(view);
  const textarea = document.querySelector("#agentConfigEditor");
  textarea.value = state.agentsDraft;
}

async function refreshAgentsView() {
  await loadAgents();
  renderSettingsView();
}

function switchScope(scope) {
  state.agentsScope = scope;
  refreshAgentsView();
}

function formatAgents() {
  const textarea = document.querySelector("#agentConfigEditor");
  try {
    textarea.value = JSON.stringify(JSON.parse(textarea.value), null, 2);
    toast("已格式化");
  } catch {
    toast("JSON 格式有误", "error");
  }
}

function resetAgentsDraft() {
  const textarea = document.querySelector("#agentConfigEditor");
  textarea.value = state.agentsDraft;
  toast("已还原到上次读取的配置");
}

async function saveAgents() {
  const textarea = document.querySelector("#agentConfigEditor");
  let config;
  try {
    config = JSON.parse(textarea.value);
  } catch {
    toast("JSON 格式有误，未保存", "error");
    return;
  }
  try {
    state.agents = await api("/api/agents", {
      method: "PUT",
      body: {
        scope: state.agentsScope,
        book_id: state.agentsScope === "book" ? state.currentBookId : undefined,
        config,
      },
    });
    state.agentsDraft = JSON.stringify(state.agents.config, null, 2);
    toast("配置已保存");
    renderSettingsView();
  } catch (error) {
    const detail = error.details?.errors?.join("；");
    toast(detail ? `${error.message}：${detail}` : error.message, "error");
  }
}

function openNewBookDialog() {
  elements.form.reset();
  elements.form.elements.target_words.value = "20000";
  elements.form.elements.max_chapters.value = "12";
  elements.dialog.showModal();
}

elements.newBookButton.addEventListener("click", openNewBookDialog);
elements.dialogClose.addEventListener("click", () => elements.dialog.close());
elements.dialogCancel.addEventListener("click", () => elements.dialog.close());
elements.refreshBooksButton.addEventListener("click", async () => {
  await loadBooks({ announce: true });
  const route = parseRoute();
  if (route.view === "book") renderBookRoute(route.id);
  else renderRoute();
});
elements.settingsButton.addEventListener("click", () => navigate("#/settings"));
elements.setupButton.addEventListener("click", () => navigate("#/setup"));

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(elements.form).entries());
  elements.dialogSubmit.disabled = true;
  try {
    const result = await api("/api/books", {
      method: "POST",
      body: {
        title: data.title,
        genre: data.genre,
        target_words: Number(data.target_words),
        max_chapters: Number(data.max_chapters),
        folder: data.folder || undefined,
      },
    });
    elements.dialog.close();
    await loadBooks();
    toast("新书已创建");
    navigate(`#/books/${result.book.id}`);
  } catch (error) {
    const output = error.details?.output ? `\n${error.details.output.slice(-300)}` : "";
    toast(`${error.message}${output}`, "error");
  } finally {
    elements.dialogSubmit.disabled = false;
  }
});

async function renderRoute() {
  const route = parseRoute();
  if (route.view !== "book") {
    stopPolling();
  }
  if (route.view === "home") {
    state.currentBookId = null;
    renderHome();
    return;
  }
  if (route.view === "book") {
    await renderBookRoute(route.id);
    return;
  }
  if (route.view === "submissions") {
    await renderSubmissionsRoute(route.id);
    return;
  }
  if (route.view === "settings") {
    await renderSettingsRoute();
  }
  if (route.view === "setup") {
    await renderSetupRoute();
  }
}

window.addEventListener("hashchange", renderRoute);

(async function init() {
  try {
    const data = await api("/api/settings");
    if (data.settings?.needs_setup && !location.hash.startsWith("#/setup")) {
      location.hash = "#/setup";
    }
  } catch {}
  await loadBooks();
  if (!location.hash) location.hash = "#/";
  renderRoute();
})();
