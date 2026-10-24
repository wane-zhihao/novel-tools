import { api } from "../lib/api.js";
import { el, clear, fmtNumber, fmtDate, toast } from "../lib/dom.js";

export async function renderShelf({ state, go, openNewBook }) {
  const view = document.querySelector("#view");
  view.classList.remove("wide");
  clear(view);
  if (!state.books.length) {
    view.append(el("div", { class: "shelf" },
      el("div", { class: "empty" },
        el("strong", { text: "书房还空着" }),
        el("p", { text: "开一本新书，先写下第一段。AI 是否可用不影响你现在动笔。" }),
        el("button", { class: "button button-primary", type: "button", onclick: openNewBook, text: "开一本新书" }))));
    return;
  }

  const book = state.books.find((item) => Number(item.id) === Number(state.currentBookId)) ?? state.books[0];
  let manuscript = { chapters: [], next_chapter: 1 };
  try {
    manuscript = await api(`/api/books/${book.id}/manuscript`);
  } catch (error) {
    toast(error.message, "error");
  }
  const dirty = manuscript.chapters.filter((chapter) => chapter.dirty).length;
  const withVersions = manuscript.chapters.filter((chapter) => chapter.versions > 0).length;
  const nextChapter = manuscript.next_chapter || 1;
  const lastChapter = manuscript.chapters.at(-1);

  const hero = el("section", { class: "shelf-hero" },
    el("div", {},
      el("p", { class: "eyebrow", text: fmtDate(new Date().toISOString()) }),
      el("h1", { class: "hero-title", text: lastChapter ? `继续写第 ${nextChapter} 章` : "写下第一章" }),
      el("p", { class: "hero-copy", text: lastChapter
        ? `上次写到《${book.title ?? "未命名"}》第 ${lastChapter.number} 章，共 ${fmtNumber(book.words_total)} 字。`
        : `《${book.title ?? "未命名"}》还没有正文。先写 300 字，故事就开始了。` }),
      el("div", { class: "hero-actions" },
        el("button", {
          class: "button button-primary", type: "button",
          onclick: () => go(`#/books/${book.id}/write/${nextChapter}`),
          text: lastChapter ? "继续写" : "开始写第一章",
        }),
        el("button", { class: "button", type: "button", onclick: () => go(`#/books/${book.id}/revise`), text: "看 AI 改动" }),
        el("button", { class: "button", type: "button", onclick: () => go(`#/books/${book.id}/submit`), text: "投稿检查" }))),
    el("div", { class: "hero-stats" },
      el("div", { class: "hero-stat" }, el("span", { text: "已封板" }), el("b", { text: `${book.chapters_committed} 章` })),
      el("div", { class: "hero-stat" }, el("span", { text: "字数" }), el("b", { text: fmtNumber(book.words_total) })),
      el("div", { class: "hero-stat" }, el("span", { text: "目标" }), el("b", { text: fmtNumber(book.target_words) })),
      state.health?.engine?.ai
        ? el("span", { class: "chip chip-ok", text: "AI 已连接" })
        : el("span", { class: "chip", text: "AI 未连接 · 正文照常写" })));

  const todos = el("section", {},
    el("h2", { class: "section-title", text: "今天可以做的三件事" }),
    el("div", { class: "todo-grid" },
      todoCard("待定稿", dirty ? `${dirty} 章修改后还没重新定稿` : "没有待定稿的章节", dirty ? `#/books/${book.id}/revise` : null, go),
      todoCard("看 AI 改动", withVersions ? `${withVersions} 章有历史版本` : "还没有 AI 改动", `#/books/${book.id}/revise`, go),
      todoCard("投稿", book.words_total >= 20000 ? "字数已到 2 万，可以检查投稿包" : `还差 ${fmtNumber(20000 - book.words_total)} 字到 2 万`, `#/books/${book.id}/submit`, go)));

  const bookGrid = el("section", {},
    el("div", { class: "panel-head" },
      el("h2", { class: "section-title", text: "书架" }),
      el("button", { class: "button button-small", type: "button", onclick: openNewBook, text: "开新书" })),
    el("div", { class: "book-grid" },
      ...state.books.map((item) => el("a", {
        class: "book-card", href: `#/books/${item.id}/write/${Math.max(1, Number(item.current_chapter) + 1)}`,
      },
        el("span", { class: "book-cover", text: (item.title ?? "书").replace(/[《》]/g, "").slice(0, 1) }),
        el("span", { class: "book-info" },
          el("strong", { text: item.title ?? "未命名" }),
          el("span", { text: `${item.chapters_committed} 章 · ${fmtNumber(item.words_total)} 字` }),
          item.blocked_reason ? el("span", { class: "danger-text", text: "有需要处理的问题" }) : null)))));

  view.append(el("div", { class: "shelf" }, hero, todos, bookGrid));
}

function todoCard(title, detail, hash, go) {
  const card = el("div", { class: "todo" },
    el("strong", { text: title }),
    el("span", { text: detail }));
  if (hash) {
    card.append(el("button", {
      class: "button button-small button-quiet", type: "button", text: "去看看",
      onclick: () => go(hash),
    }));
  }
  return card;
}
