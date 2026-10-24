import { api } from "./lib/api.js";
import { el, clear, toast } from "./lib/dom.js";
import { renderShelf } from "./views/shelf.js";
import { renderWrite } from "./views/write.js";
import { renderRevise } from "./views/revise.js";
import { renderStory } from "./views/story.js";
import { renderSubmit } from "./views/submit.js";
import { renderSettings } from "./views/settings.js";

const state = {
  books: null, // null=加载失败/未加载；[]=真的空书房（B15 空态二分）
  loadError: null,
  health: null,
  currentBookId: null,
  routeToken: 0,
};

const view = document.querySelector("#view");
const railNav = document.querySelector("#railNav");
const railBook = document.querySelector("#railBook");
const bottomNav = document.querySelector("#bottomNav");
const context = document.querySelector("#context");
const dialog = document.querySelector("#newBookDialog");
const form = document.querySelector("#newBookForm");

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "books" && parts[1]) {
    const id = Number(parts[1]);
    if (parts[2] === "write") return { view: "write", id, chapter: parts[3] ? Number(parts[3]) : null };
    if (parts[2] === "story") return { view: "story", id };
    if (parts[2] === "revise") return { view: "revise", id };
    if (parts[2] === "submit") return { view: "submit", id };
    return { view: "write", id, chapter: null };
  }
  if (parts[0] === "settings") return { view: "settings" };
  return { view: "shelf" };
}

function bookOf(id) {
  return (state.books ?? []).find((book) => Number(book.id) === Number(id)) ?? null;
}

function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

function navItems(book) {
  const id = book?.id;
  return [
    { label: "书房", glyph: "书", hash: "#/" },
    { label: "写", glyph: "写", hash: id ? `#/books/${id}/write` : "#/" },
    { label: "故事", glyph: "人", hash: id ? `#/books/${id}/story` : "#/" },
    { label: "改", glyph: "改", hash: id ? `#/books/${id}/revise` : "#/" },
    { label: "投", glyph: "投", hash: id ? `#/books/${id}/submit` : "#/" },
  ];
}

function paintNav(active, book) {
  const items = navItems(book);
  const make = (item, mobile) => {
    const isActive = item.label === active;
    return el("a", {
      class: mobile ? "bottom-link" : "rail-link",
      href: item.hash,
      "aria-current": isActive ? "page" : null,
    },
      mobile ? null : el("span", { class: "rail-glyph", "aria-hidden": "true", text: item.glyph }),
      el("span", { text: item.label }));
  };
  clear(railNav).append(...items.map((item) => make(item, false)));
  clear(bottomNav).append(...items.map((item) => make(item, true)));
  railBook.textContent = book ? `${book.title ?? "未命名"}` : "还没有书";
  context.textContent = book ? `${book.title ?? "未命名"}` : "先写下来，再改好";
}

function openNewBook() {
  form.reset();
  form.elements.target_words.value = "20000";
  dialog.showModal();
}

async function renderShelfRoute(token) {
  try {
    await loadBooks();
    state.loadError = null;
  } catch (error) {
    // B15：加载失败与真空态二分，绝不再误报"书房还空着"
    state.books = null;
    state.loadError = error.message;
  }
  const preferred = bookOf(state.currentBookId) ?? state.books?.[0] ?? null;
  paintNav("书房", preferred);
  if (token !== state.routeToken) return;
  await renderShelf({ state, go, openNewBook, retry: render });
}

async function renderBookRoute(route, token) {
  const book = bookOf(route.id);
  if (!book) {
    clear(view).append(el("div", { class: "empty" },
      el("strong", { text: "找不到这本书" }),
      el("p", { text: "可能还没同步，回书房刷新一下。" }),
      el("button", { class: "button", type: "button", onclick: () => go("#/"), text: "回书房" })));
    return;
  }
  state.currentBookId = Number(book.id);
  if (route.view === "write") {
    paintNav("写", book);
    let chapter = route.chapter;
    if (!chapter || !Number.isInteger(chapter) || chapter < 1) {
      try {
        const manuscript = await api(`/api/books/${book.id}/manuscript`);
        chapter = manuscript.next_chapter || 1;
      } catch {
        chapter = 1;
      }
      if (token === state.routeToken) {
        history.replaceState(null, "", `#/books/${book.id}/write/${chapter}`);
      }
    } else {
      // P2 小项：URL 章节号越界（如 /write/999）不再直接开空白稿纸，先确认
      try {
        const manuscript = await api(`/api/books/${book.id}/manuscript`);
        const exists = manuscript.chapters.some((item) => item.number === chapter);
        const next = manuscript.next_chapter || 1;
        if (!exists && chapter > next) {
          const ok = window.confirm(`第 ${chapter} 章还不存在。将创建这一章并跳过中间章节，确认创建？`);
          if (!ok) {
            if (token === state.routeToken) go(`#/books/${book.id}/write/${next}`);
            return;
          }
        }
      } catch {
        // 读取失败时交给稿纸视图报错，不阻断打开
      }
    }
    context.textContent = `${book.title ?? "未命名"} · 第 ${chapter} 章`;
    await renderWrite({ bookId: book.id, chapter, go, state });
    return;
  }
  if (route.view === "revise") {
    paintNav("改", book);
    await renderRevise({ bookId: book.id, go, state });
    return;
  }
  if (route.view === "story") {
    paintNav("故事", book);
    await renderStory({ bookId: book.id, go, state });
    return;
  }
  if (route.view === "submit") {
    paintNav("投", book);
    await renderSubmit({ bookId: book.id, go, state });
  }
}

async function renderSettingsRoute() {
  paintNav("", bookOf(state.currentBookId));
  context.textContent = "设置 · 只保存在本机";
  await renderSettings({ state, go });
}

async function render() {
  const token = ++state.routeToken;
  document.body.classList.remove("focus-mode");
  view.classList.remove("wide");
  const route = parseRoute();
  try {
    if (route.view === "shelf") return await renderShelfRoute(token);
    if (route.view === "settings") return await renderSettingsRoute();
    return await renderBookRoute(route, token);
  } catch (error) {
    toast(error.message, "error");
    clear(view).append(el("div", { class: "empty" },
      el("strong", { text: "页面没打开" }),
      el("p", { text: error.message }),
      el("button", { class: "button", type: "button", onclick: render, text: "重试" })));
  }
}

async function loadBooks() {
  const data = await api("/api/books");
  state.books = data.books ?? [];
}

let connBar = null;

function hideConnError() {
  connBar?.remove();
  connBar = null;
}

// B15：连接失败常驻错误条（可手动关闭/重试），不再依赖 4.2 秒自消的 toast
function showConnError(message) {
  hideConnError();
  connBar = el("div", { class: "conn-bar", role: "alert" },
    el("span", { class: "conn-text", text: `连接失败：${message}。先重试；若持续失败，请确认工作台服务正在运行。` }),
    el("div", { class: "conn-actions" },
      el("button", { class: "button button-small", type: "button", text: "重试", onclick: retryConnection }),
      el("button", { class: "button button-small button-quiet", type: "button", text: "关闭", onclick: hideConnError })));
  document.body.prepend(connBar);
}

async function retryConnection() {
  try {
    const [health] = await Promise.all([api("/api/health"), loadBooks()]);
    state.health = health;
    state.loadError = null;
    hideConnError();
    toast("已重新连接");
    await render();
  } catch (error) {
    state.books = null;
    state.loadError = error.message;
    showConnError(error.message);
  }
}

async function boot() {
  try {
    const [health] = await Promise.all([api("/api/health"), loadBooks()]);
    state.health = health;
  } catch (error) {
    state.books = null;
    state.loadError = error.message;
    // 常驻错误条已含完整信息与重试入口，不再叠加会自消/堆积的 toast
    showConnError(error.message);
  }
  if (!location.hash) location.hash = "#/";
  await render();
}

window.addEventListener("hashchange", render);
document.querySelector("#newBookClose").addEventListener("click", () => dialog.close());
document.querySelector("#newBookCancel").addEventListener("click", () => dialog.close());
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = document.querySelector("#newBookSubmit");
  submit.disabled = true;
  const values = Object.fromEntries(new FormData(form).entries());
  try {
    const result = await api("/api/books", {
      method: "POST",
      body: {
        title: values.title,
        genre: values.genre,
        target_words: Number(values.target_words) || 20000,
        one_line: values.one_line || undefined,
      },
    });
    dialog.close();
    await loadBooks();
    state.health = await api("/api/health");
    toast("书已经建好，开始写第一段吧");
    go(`#/books/${result.book.id}/write/1`);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    submit.disabled = false;
  }
});

boot();
