import { api } from "../lib/api.js";
import { el, clear, fmtDate, fmtShortDate, toast } from "../lib/dom.js";

export async function renderSubmit({ bookId, go }) {
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
      el("p", { class: "page-sub", text: result.path }),
      el("p", { class: "muted small", text: "包含全文、体检报告、投稿邮件与大纲；MANIFEST 记录了每个文件的 sha。" }));
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
