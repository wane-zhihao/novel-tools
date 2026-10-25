import { api } from "../lib/api.js";
import { el, clear, toast } from "../lib/dom.js";

// 首次运行向导（B4）。此前新用户点「开一本新书」必然 500 books_root_missing，
// 而路径配置藏在「设置 → 高级」折叠里，没有任何指引。
// 这里复用现成端点：PUT /api/settings 存书库目录、PUT /api/providers/:name/secret
// 存密钥、GET /api/health + /api/settings 判就绪。不重写 settings 视图。
//
// 可靠性：每一步都以「服务端回读」为准，而不是点了就假设成功；保存失败保留已填
// 输入（向导状态本地持有，重渲染不清空）。模型服务是可选步骤——不配也能只写正文。
export async function renderSetup({ state, onReady, go }) {
  const view = document.querySelector("#view");
  view.classList.remove("wide");
  clear(view);
  view.append(el("p", { class: "empty", text: "正在检查环境…" }));

  let settings;
  let agents;
  try {
    [settings, agents] = await Promise.all([api("/api/settings"), api("/api/agents")]);
  } catch (error) {
    clear(view).append(el("div", { class: "empty" },
      el("strong", { text: "环境检查没打开" }),
      el("p", { text: error.message }),
      el("button", { class: "button", type: "button", text: "重试", onclick: () => renderSetup({ state, onReady, go }) })));
    return;
  }

  const eff = settings.settings;
  // 本地步骤态：书库目录是否可用、引擎是否绿灯、密钥是否配置。决定按钮可用性，
  // 也让「保存后服务端回读」成为唯一的真相来源。
  const done = {
    root: (eff.exists?.books_roots ?? []).some(Boolean),
    python: Boolean(eff.exists?.python),
    storyctl: Boolean(eff.exists?.storyctl),
  };

  const rootInput = el("input", {
    name: "books_root", type: "text", autocomplete: "off",
    value: eff.books_root ?? "", placeholder: "例如 F:/小说/works",
  });
  const rootStatus = el("span", { class: "chip chip-warn", text: done.root ? "已就绪" : "待设置" });
  const setRoot = el("button", {
    class: "button button-small", type: "button", text: "用这个目录",
    onclick: async () => {
      if (!rootInput.value.trim()) return toast("先填一个目录路径", "error");
      setRoot.disabled = true;
      try {
        const saved = await api("/api/settings", {
          method: "PUT",
          body: { books_root: rootInput.value.trim(), create_books_root: true },
        });
        const ok = (saved.settings.exists?.books_roots ?? []).some(Boolean);
        done.root = ok;
        rootStatus.className = `chip ${ok ? "chip-ok" : "chip-warn"}`;
        rootStatus.textContent = ok ? "已就绪" : "目录没建起来";
        if (ok) toast("书库目录已就绪");
        refresh();
      } catch (error) {
        toast(error.details?.errors?.join("；") || error.message, "error");
      } finally {
        setRoot.disabled = false;
      }
    },
  });

  const engineRow = el("div", { class: "engine-row" },
    el("span", { text: "Python" }), light(done.python),
    el("span", { text: "写作引擎" }), light(done.storyctl));
  const engineNote = el("p", { class: "form-note",
    text: done.python && done.storyctl ? "" : "引擎没齐也能先动笔：正文照常写，AI 功能暂不可用。" });

  const providerSection = el("div", { class: "provider-list" });
  const openai = (agents.providers ?? []).filter((p) => p.type === "openai");
  for (const provider of openai) {
    const keyInput = el("input", { type: "password", autocomplete: "off", placeholder: "粘贴 API Key" });
    const chip = el("span", {
      class: `chip ${provider.configured ? "chip-ok" : "chip-warn"}`,
      text: provider.configured ? "已配置" : provider.optional ? "可免密钥" : "未配置",
    });
    if (provider.configured) done.ai = true;
    providerSection.append(el("div", { class: "provider-row" },
      el("div", {},
        el("strong", { text: provider.name }),
        el("p", { class: "muted small", text: provider.env ? `密钥环境变量 ${provider.env}` : "本地服务" })),
      chip,
      provider.optional && !provider.configured ? null : el("div", { class: "inline-fields", style: "flex-basis:100%" },
        keyInput,
        el("button", {
          class: "button button-small", type: "button", text: "保存密钥",
          onclick: async () => {
            if (!keyInput.value.trim()) return toast("密钥不能为空", "error");
            try {
              await api(`/api/providers/${encodeURIComponent(provider.name)}/secret`, {
                method: "PUT", body: { api_key: keyInput.value },
              });
              keyInput.value = "";
              chip.className = "chip chip-ok";
              chip.textContent = "已配置";
              done.ai = true;
              toast("密钥已保存");
              refresh();
            } catch (error) {
              toast(error.message, "error");
            }
          },
        }))));
  }
  if (!openai.length) {
    providerSection.append(el("p", { class: "muted small", text: "没有检测到可配置的模型服务，可稍后在设置里添加。" }));
  }

  const finish = el("button", {
    class: "button button-primary", type: "button",
    text: done.root ? "开始写第一段" : "先设好书库目录",
    disabled: done.root ? null : "disabled",
    onclick: () => { onReady?.(); go("#/"); },
  });

  function refresh() {
    finish.disabled = done.root ? null : "disabled";
    finish.textContent = done.root ? "开始写第一段" : "先设好书库目录";
  }

  const step = (n, title, body) => el("section", { class: "setup-step" },
    el("div", { class: "step-head" }, el("span", { class: "step-num", text: String(n) }), el("h2", { text: title })),
    body);

  clear(view).append(el("div", { class: "setup" },
    el("header", {},
      el("h1", { class: "page-title", text: "三步开机" }),
      el("p", { class: "page-sub", text: "先备好书库目录就能动笔；模型服务可以稍后再配。" })),
    step(1, "书库目录", el("div", { class: "inline-fields" }, rootInput, setRoot, rootStatus)),
    step(2, "写作引擎", el("div", {}, engineRow, engineNote)),
    step(3, "模型服务（可选）", providerSection),
    el("div", { class: "setup-foot" }, finish)));
}

function light(ok) {
  return el("span", { class: `light ${ok ? "light-ok" : "light-bad"}`, "aria-label": ok ? "可用" : "缺失" });
}
