import { api } from "../lib/api.js";
import { el, clear, toast } from "../lib/dom.js";

export async function renderSettings({ state }) {
  const view = document.querySelector("#view");
  view.classList.remove("wide");
  clear(view).append(el("p", { class: "empty", text: "正在读取设置…" }));

  let settings;
  let agents;
  try {
    [settings, agents] = await Promise.all([api("/api/settings"), api("/api/agents")]);
  } catch (error) {
    clear(view).append(el("div", { class: "empty" }, el("strong", { text: "设置打不开" }), el("p", { text: error.message })));
    return;
  }

  const effective = settings.settings;
  const page = el("div", { class: "settings" },
    el("header", {},
      el("h1", { class: "page-title", text: "设置" }),
      el("p", { class: "page-sub", text: "常用项在前；路径与完整 Agent 配置收进高级。" })));

  const aiPanel = el("section", { class: "panel" },
    el("div", { class: "panel-head" },
      el("h2", { text: "模型服务" }),
      el("span", { class: "chip", text: state.health?.engine?.ai ? "AI 已连接" : "AI 未连接" })));
  for (const provider of agents.providers ?? []) {
    const row = el("div", { class: "provider-row" },
      el("div", {},
        el("strong", { text: provider.name }),
        el("p", { class: "muted small", text: `${provider.type}${provider.env ? ` · ${provider.env}` : ""}` })),
      el("span", {
        class: `chip ${provider.configured ? "chip-ok" : "chip-warn"}`,
        text: provider.optional ? "密钥可选" : provider.configured
          ? `已配置${provider.source === "store" ? "（本地）" : provider.source === "env" ? "（环境变量）" : ""}`
          : "未配置",
      }));
    if (provider.type === "openai") {
      const input = el("input", { type: "password", autocomplete: "off", placeholder: provider.configured ? "输入可替换" : "粘贴 API Key" });
      row.append(
        el("div", { class: "inline-fields", style: "flex-basis:100%" },
          input,
          el("button", {
            class: "button button-small", type: "button", text: "保存密钥",
            onclick: async () => {
              if (!input.value.trim()) return toast("密钥不能为空", "error");
              try {
                await api(`/api/providers/${encodeURIComponent(provider.name)}/secret`, {
                  method: "PUT", body: { api_key: input.value },
                });
                input.value = "";
                toast("密钥已保存");
                await renderSettings({ state });
              } catch (error) {
                toast(error.message, "error");
              }
            },
          }),
          el("button", {
            class: "button button-small button-quiet", type: "button", text: "删除本地密钥",
            disabled: provider.source !== "store" ? "disabled" : null,
            onclick: async () => {
              try {
                await api(`/api/providers/${encodeURIComponent(provider.name)}/secret`, { method: "DELETE", body: {} });
                toast("本地密钥已删除");
                await renderSettings({ state });
              } catch (error) {
                toast(error.message, "error");
              }
            },
          })));
    }
    aiPanel.append(row);
  }
  aiPanel.append(el("p", { class: "form-note", text: "默认自动改稿；每次 AI 写入都会留下版本，可在「改」里看 diff 并回滚。" }));

  const writePanel = el("section", { class: "panel" },
    el("div", { class: "panel-head" }, el("h2", { text: "写作偏好" })),
    el("div", { class: "inline-fields" },
      el("button", {
        class: "button", type: "button", text: document.body.classList.contains("theme-night") ? "切换日间" : "切换夜间",
        onclick: () => { document.body.classList.toggle("theme-night"); renderSettings({ state }); },
      }),
      el("button", {
        class: "button", type: "button", text: "回到书房",
        onclick: () => { location.hash = "#/"; },
      })));

  const advanced = el("details", { class: "advanced" },
    el("summary", { text: "高级：路径与完整 Agent 配置" }),
    el("p", { class: "form-note", text: "作者通常不需要改这里。路径错误会导致 AI 不可用，但正文仍可手写。" }));
  const pathForm = el("form", { class: "form-grid", style: "display:flex;flex-direction:column;gap:12px;margin-top:12px" },
    el("label", { class: "field" }, el("span", { text: "书库目录" }), el("input", { name: "books_root", value: effective.books_root ?? "" })),
    el("label", { class: "field" }, el("span", { text: "Python" }), el("input", { name: "python", value: effective.python ?? "" })),
    el("label", { class: "field" }, el("span", { text: "story-long-write 技能目录" }), el("input", { name: "story_long_write", value: effective.story_long_write ?? "" })),
    el("div", { class: "inline-fields" },
      el("button", { class: "button button-primary", type: "submit", text: "保存路径" }),
      el("a", { class: "button", href: "/studio.html", text: "打开制作台" })));
  pathForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(pathForm).entries());
    try {
      await api("/api/settings", {
        method: "PUT",
        body: {
          books_root: values.books_root,
          python: values.python,
          story_long_write: values.story_long_write,
          create_books_root: true,
        },
      });
      toast("路径已保存");
      state.health = await api("/api/health");
    } catch (error) {
      toast(error.details?.errors?.join("；") || error.message, "error");
    }
  });
  advanced.append(pathForm);

  page.append(aiPanel, writePanel, advanced);
  view.replaceChildren(page);
}
