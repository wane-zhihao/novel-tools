import { api } from "../lib/api.js";
import { el, clear, fmtDate, fmtNumber, fmtShortDate, toast } from "../lib/dom.js";

// B19：SKILL.md「投稿前人工确认 4 条」固定展示，投前逐条勾
const HUMAN_CONFIRM = [
  "只联系一个编辑；这本书只有一次签约评估机会",
  "前 200 字冲突 / 前 600 字升级 / 前 6000 字主线 / 三章内反转",
  "文档不拆成多封邮件，按编辑要求附大纲与设定",
  "回填编辑反馈到投稿记录——那是下一轮校准的唯一真数据",
];
const FALLBACK_PACKAGE_FILES = ["全文.md", "体检报告.md", "投稿邮件.md", "投稿记录.md"];

export async function renderSubmit({ bookId, go, state }) {
  const view = document.querySelector("#view");
  view.classList.remove("wide");
  clear(view).append(el("p", { class: "empty", text: "正在做投前检查…" }));

  let preflight;
  let submissions = [];
  try {
    [preflight, { submissions }] = await Promise.all([
      api(`/api/books/${bookId}/submissions/preflight`, { method: "POST", body: {} }),
      api(`/api/books/${bookId}/submissions`),
    ]);
  } catch (error) {
    clear(view).append(el("div", { class: "empty" }, el("strong", { text: "投稿检查打不开" }), el("p", { text: error.message })));
    return;
  }

  let packaged = null;
  const page = el("div", { class: "submit" },
    el("header", {},
      el("h1", { class: "page-title", text: "投稿" }),
      el("p", { class: "page-sub", text: "每部作品只有一次评估机会。先检查，再投；只联系一位编辑。" })));
  const checklist = el("section", { class: "panel" },
    el("div", { class: "panel-head" },
      el("h2", { text: "投前检查" }),
      el("span", { class: preflight.ready ? "chip chip-ok" : "chip chip-warn", text: preflight.ready ? "可以投" : "还差几步" })),
    el("div", { class: "checklist" },
      ...preflight.checks.map((check) => el("div", { class: `check ${check.ok ? "ok" : "bad"}` },
        el("span", { class: "dot" }),
        el("div", {}, el("strong", { text: check.label }), el("span", { text: check.detail }))))),
    el("div", { class: "inline-fields", style: "margin-top:14px" },
      el("button", {
        class: "button button-primary", type: "button", text: "生成投稿包",
        disabled: preflight.ready ? null : "disabled",
        onclick: async () => {
          const channel = window.prompt("投给哪个平台/渠道？", "七猫") || "七猫";
          try {
            packaged = await api(`/api/books/${bookId}/submissions/package`, {
              method: "POST", body: { channel },
            });
            toast(`投稿包已生成：${packaged.path}`);
            renderPackage(packaged);
          } catch (error) {
            toast(error.message, "error");
          }
        },
      }),
      el("button", {
        class: "button button-quiet", type: "button", text: "重新检查",
        onclick: () => location.reload(),
      })));
  page.append(checklist);

  const confirmPanel = el("section", { class: "panel" },
    el("div", { class: "panel-head" },
      el("h2", { text: "人工确认（投前逐条勾）" }),
      el("span", { class: "chip", text: "来自 SKILL.md" })),
    el("div", { class: "checklist" },
      ...HUMAN_CONFIRM.map((text, index) => {
        const key = `workbench:submit-confirm:${bookId}:${index}`;
        const box = el("input", { type: "checkbox", "aria-label": text });
        try {
          box.checked = localStorage.getItem(key) === "1";
        } catch {}
        box.addEventListener("change", () => {
          try {
            localStorage.setItem(key, box.checked ? "1" : "0");
          } catch {}
        });
        return el("label", { class: "check confirm-item" }, box, el("span", { text }));
      })));
  page.append(confirmPanel);

  const packageBox = el("section", { class: "panel", hidden: "hidden" });
  page.append(packageBox);

  const history = el("section", { class: "panel" },
    el("div", { class: "panel-head" }, el("h2", { text: "投稿记录" })),
    el("div", { class: "card-list" }));
  page.append(history);
  view.replaceChildren(page);

  function renderPackage(result) {
    packageBox.hidden = false;
    clear(packageBox).append(
      el("div", { class: "panel-head" },
        el("h2", { text: "投稿包已生成" }),
        el("span", { class: "chip chip-ok", text: result.manuscript_version })),
      el("p", { class: "muted small", text: "包含全文、体检报告、投稿邮件与大纲；MANIFEST 记录了每个文件的 sha。" }));

    // B19：列出包内文件（以后端 manifest 为准；缺失时按约定四文件展示）
    const files = result.manifest?.files?.length
      ? result.manifest.files
      : FALLBACK_PACKAGE_FILES.map((name) => ({ name }));
    packageBox.append(
      el("h3", { class: "section-title", text: "包内文件" }),
      el("div", { class: "card-list" },
        ...files.map((file) => el("div", { class: "list-row" },
          el("strong", { text: file.name }),
          el("span", { class: "muted small", text: file.bytes != null ? `${fmtNumber(file.bytes)} 字节` : "未找到（按约定应存在）" })))));

    // B19：复制路径（含降级）+ 在资源管理器中打开
    const book = (state?.books ?? []).find((item) => Number(item.id) === Number(bookId));
    const absolutePath = book?.path ? `${book.path.replaceAll("\\", "/")}/${result.path.replaceAll("\\", "/")}` : result.path;
    const pathInput = el("input", { class: "mono", readonly: "readonly", value: absolutePath, "aria-label": "投稿包路径", style: "flex:1;min-width:220px" });
    const copyButton = el("button", {
      class: "button button-small", type: "button", text: "复制路径",
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(absolutePath);
          toast("路径已复制");
        } catch {
          pathInput.select();
          toast("剪贴板不可用，已选中路径，按 Ctrl+C 复制");
        }
      },
    });
    const revealButton = el("button", {
      class: "button button-small", type: "button", text: "在资源管理器中打开",
      onclick: async () => {
        revealButton.disabled = true;
        try {
          await api(`/api/books/${bookId}/submissions/${encodeURIComponent(result.path.split("/").at(-1))}/reveal`, {
            method: "POST", body: { file: "全文.md" },
          });
        } catch (error) {
          toast(error.message, "error");
        } finally {
          revealButton.disabled = false;
        }
      },
    });
    packageBox.append(el("div", { class: "inline-fields", style: "margin-top:12px" }, pathInput, copyButton, revealButton));

    const form = el("form", { class: "form-grid", style: "display:flex;flex-direction:column;gap:12px;margin-top:14px" },
      el("div", { class: "field-row" },
        el("label", { class: "field" }, el("span", { text: "渠道" }), el("input", { name: "channel", value: "七猫", required: "required" })),
        el("label", { class: "field" }, el("span", { text: "编辑" }), el("input", { name: "editor", placeholder: "编辑姓名" })),
        el("label", { class: "field" }, el("span", { text: "预计回复" }), el("input", { name: "expected_reply_at", type: "date" }))),
      el("label", { class: "field" }, el("span", { text: "编辑联系方式或投稿邮箱" }), el("input", { name: "editor_contact" })),
      el("label", { class: "field" }, el("span", { text: "备注" }), el("input", { name: "feedback", placeholder: "例如：按投稿要求附上申请表" })),
      el("button", { class: "button button-primary", type: "submit", text: "记录这次投稿" }));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      try {
        await api(`/api/books/${bookId}/submissions`, {
          method: "POST",
          body: {
            ...values,
            status: "已投稿",
            manuscript_version: result.manuscript_version,
            package_path: result.path,
            submitted_at: new Date().toISOString(),
          },
        });
        toast("已记录投稿。等回复时不要同时投第二个编辑。");
        location.reload();
      } catch (error) {
        toast(error.message, "error");
      }
    });
    packageBox.append(form);
  }

  async function paintHistory() {
    clear(history.querySelector(".card-list"));
    const list = history.querySelector(".card-list");
    if (!submissions.length) {
      list.append(el("p", { class: "muted small", text: "还没有投稿记录。检查通过后，先投一位编辑。" }));
      return;
    }
    for (const item of submissions) {
      const card = el("div", { class: "submission-card" },
        el("div", { class: "panel-head" },
          el("div", {},
            el("strong", { text: `${item.channel ?? "未填渠道"} · ${item.status ?? "未标注"}` }),
            el("p", { class: "muted small", text: `${item.editor ? `编辑 ${item.editor} · ` : ""}${fmtShortDate(item.submitted_at)}` })),
          item.result ? el("span", { class: "chip", text: item.result }) : null),
        item.feedback ? el("p", { class: "small", text: item.feedback }) : null,
        item.manuscript_version ? el("p", { class: "muted small", text: `稿件版本 ${item.manuscript_version}` }) : null,
        item.expected_reply_at ? el("p", { class: "muted small", text: `预计回复 ${fmtShortDate(item.expected_reply_at)}` }) : null);
      const events = el("div", { class: "timeline" });
      const note = el("input", { placeholder: "追加跟进：编辑说了什么 / 下一步" });
      const add = el("button", {
        class: "button button-small", type: "button", text: "记一笔",
        onclick: async () => {
          if (!note.value.trim()) return;
          try {
            await api(`/api/books/${bookId}/submissions/${encodeURIComponent(item.external_id)}/events`, {
              method: "POST", body: { type: "note", note: note.value },
            });
            note.value = "";
            toast("已记录");
            await loadEvents(item.external_id, events);
          } catch (error) {
            toast(error.message, "error");
          }
        },
      });
      card.append(events, el("div", { class: "inline-fields", style: "margin-top:8px" }, note, add));
      list.append(card);
      loadEvents(item.external_id, events);
    }
  }

  async function loadEvents(submissionId, target) {
    try {
      const data = await api(`/api/books/${bookId}/submissions/${encodeURIComponent(submissionId)}/events`);
      clear(target).append(...(data.events ?? []).map((event) => el("div", { class: "event" },
        el("div", {}, el("b", { text: event.type || "记录" }), el("p", { class: "small", text: event.note || "" })),
        el("span", { text: fmtDate(event.at) }))));
    } catch {}
  }

  await paintHistory();
}
