import { api } from "./lib/api.js";
import { el, clear, toast } from "./lib/dom.js";
import { renderShelf } from "./views/shelf.js";
import { renderSetup } from "./views/setup.js";
import { renderWrite } from "./views/write.js";
import { renderRevise } from "./views/revise.js";
import { renderStory } from "./views/story.js";
import { renderSubmit } from "./views/submit.js";
import { renderSettings } from "./views/settings.js";
import { createAdvancedDrawer } from "./views/advanced.js";

const state = {
  books: null, // null=加载失败/未加载；[]=真的空书房（B15 空态二分）
  loadError: null,
  health: null,
  currentBookId: null,
  routeToken: 0,
  // 高级抽屉是覆盖层：开着时底页 DOM 必须保活（textarea 里可能有未保存草稿）
  // 页面可注册的「离开守卫」：导航/重渲染前 await，返回 false 取消本次导航。
  // 用于故事页未保存编辑（Phase 2 dirty 守卫）。
  leaveGuard: null,
  // 进入 render 时的 URL 原样：守卫否决 hashchange 导航时用它修回
  lastHash: null,
  // 最近一次完整渲染的底页 URL（含查询段）：抽屉关闭回它，不丢 ?doc=
  baseHash: null,
  drawer: null,
  // 抽屉打开前的底页路由（含章号）。关抽屉按它恢复 hash：章号丢了会命中不了
  // 同路由守卫 → 重渲染 → 丢草稿
  baseRoute: null,
};

const view = document.querySelector("#view");
const railNav = document.querySelector("#railNav");
const railBook = document.querySelector("#railBook");
const bottomNav = document.querySelector("#bottomNav");
const context = document.querySelector("#context");
const advancedEntry = document.querySelector("#advancedEntry");
const dialog = document.querySelector("#newBookDialog");
const form = document.querySelector("#newBookForm");

// B2：记住上次打开的书。单写本机工具，用 localStorage 足够；隐私模式下 setItem
// 会抛错，静默忽略——丢了记忆只是回落到书架，不影响正确性。
const LAST_BOOK_KEY = "workbench:last-book";

function rememberBook(id) {
  try { localStorage.setItem(LAST_BOOK_KEY, String(id)); } catch { /* 忽略 */ }
}

function lastBookId() {
  try { return Number(localStorage.getItem(LAST_BOOK_KEY)) || null; } catch { return null; }
}

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "books" && parts[1]) {
    const id = Number(parts[1]);
    if (parts[2] === "write") return { view: "write", id, chapter: parts[3] ? Number(parts[3]) : null };
    if (parts[2] === "story") {
      // ?doc= 深链（计划第 5 节）：直达某份故事资料。URLSearchParams 负责解码。
      const qs = raw.slice(path.length);
      const doc = qs.startsWith("?") ? (new URLSearchParams(qs.slice(1)).get("doc") ?? null) : null;
      return { view: "story", id, doc };
    }
    if (parts[2] === "revise") return { view: "revise", id };
    if (parts[2] === "submit") return { view: "submit", id };
    if (parts[2] === "advanced") return { view: "advanced", id, tab: parts[3] || "nodes" };
    return { view: "write", id, chapter: null };
  }
  if (parts[0] === "settings") return { view: "settings" };
  if (parts[0] === "setup") return { view: "setup" };
  return { view: "shelf" };
}

function bookOf(id) {
  return (state.books ?? []).find((book) => Number(book.id) === Number(id)) ?? null;
}

function go(hash) {
  // 守卫不在这里问：改 hash 会触发 hashchange → render() 统一问一次。
  // go 先问一遍、render 再问一遍会让用户点两次确认。
  if (location.hash === hash) render(true);
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
  paintBookSwitcher(book);
  if (advancedEntry) {
    if (book) {
      advancedEntry.href = `#/books/${book.id}/advanced`;
      advancedEntry.title = "节点状态、运行日志、Agent 配置与投稿记录";
      advancedEntry.removeAttribute("aria-disabled");
    } else {
      // 还没有当前书：入口保留但点了不跳（href 置空会重新加载页面）
      advancedEntry.href = "#";
      advancedEntry.setAttribute("aria-disabled", "true");
      advancedEntry.title = "先打开一本书";
    }
  }
  context.textContent = book ? `${book.title ?? "未命名"}` : "先写下来，再改好";
}

// B2：多书作者唯一的换书入口。原生 <select>：零框架、键盘可达、移动端可用。
// 选中即跳该书的默认页（app 会用 manuscript.next_chapter 解析到对的章）。
function paintBookSwitcher(book) {
  clear(railBook);
  const books = state.books ?? [];
  if (!books.length) {
    railBook.textContent = "还没有书";
    return;
  }
  const select = el("select", {
    class: "rail-book-select",
    "aria-label": "切换书籍",
    onchange: (event) => {
      const id = Number(event.target.value);
      if (id && id !== Number(book?.id)) go(`#/books/${id}`);
    },
  }, ...books.map((item) => el("option", {
    value: String(item.id),
    text: item.title ?? "未命名",
    selected: Number(item.id) === Number(book?.id) ? "selected" : null,
  })));
  if (book) select.value = String(book.id);
  railBook.append(select);
}

// 平台/题材下拉的唯一填充点：名单全部来自服务端 /api/health，
// 前端不再记任何一份「有哪些平台」。配置里加一个平台，下拉就自己多一项；
// 平台没接进流水线（缺 GENRE_BASE）就标出来并禁选，而不是让人选完撞 400。
function paintPlatforms() {
  const platforms = Array.isArray(state.health?.platforms) ? state.health.platforms : [];
  const select = form.elements.platform;
  select.replaceChildren(...platforms.map((item) => el("option", {
    value: item.key,
    text: item.ready === false ? `${item.label}（未接入）` : item.label,
    disabled: item.ready === false,
  })));
  const usable = platforms.find((item) => item.ready !== false) ?? platforms[0];
  if (usable) select.value = usable.key;
  paintGenres();
}

// 题材必须跟着平台走：七猫是爽文/年代，纵横是都市/玄幻/历史。
// 报一个不在该平台 GENRES 里的题材，prompt 会落到「未取到题材基准」——
// 不报错，但整章的生死线数字全没了。宁可在下拉里就没得选。
function paintGenres() {
  const platforms = Array.isArray(state.health?.platforms) ? state.health.platforms : [];
  const current = platforms.find((item) => item.key === form.elements.platform.value);
  form.elements.genre.replaceChildren(...(current?.genres ?? []).map((item) => el("option", {
    value: item.key,
    text: item.label,
  })));
}
form.elements.platform.addEventListener("change", paintGenres);

async function openNewBook() {
  // B4：书库目录没就绪时，点「开新书」注定 500 books_root_missing。与其让用户
  // 填完整张表再撞墙，不如先把开机向导打开——这是「开书」的真正前置。
  if (state.health?.needs_setup) {
    toast("先设好书库目录才能开书", "warn");
    go("#/setup");
    return;
  }
  // 平台名单拿不到就别开这张表：空名单会开出一本没有题材基准的书
  if (!Array.isArray(state.health?.platforms) || !state.health.platforms.length) {
    try {
      state.health = await api("/api/health");
    } catch (error) {
      toast(`读不到平台名单：${error.message}`, "error");
      return;
    }
  }
  if (!state.health?.platforms?.length) {
    toast("流水线没配置任何平台，先在设置里补上 PLATFORMS", "error");
    return;
  }
  form.reset();
  form.elements.target_words.value = "20000";
  paintPlatforms();
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
  // B2：优先用「上次打开的书」，而不是按标题排序的 books[0]——后者会让多书作者
  // 刷新后静默换书。记忆的书被删时回落书架（不假装还在）。
  if (state.currentBookId == null) state.currentBookId = lastBookId();
  const preferred = bookOf(state.currentBookId) ?? null;
  if (preferred) state.currentBookId = Number(preferred.id);
  paintNav("书房", preferred);
  if (token !== state.routeToken) return;
  await renderShelf({ state, go, openNewBook, retry: render });
}

function baseHashFor(bookId) {
  // 抽屉打开前 URL 的原样（含 ?doc= 等查询段）：优先回它，重建会丢查询参数
  if (state.baseHash && state.baseHash.startsWith(`#/books/${bookId}`)) return state.baseHash;
  const base = state.baseRoute && state.baseRoute.id === Number(bookId)
    ? state.baseRoute
    : { view: "write", id: Number(bookId), chapter: null };
  const suffix = base.view === "write" && base.chapter ? `/${base.chapter}` : "";
  return `#/books/${base.id}/${base.view}${suffix}`;
}

function openDrawer(bookId, tab) {
  // 同一本书已开着：切 tab 而不重建（重建会闪，且重新拉数据）
  if (state.drawer && state.drawer.bookId === Number(bookId)) {
    state.drawer.api.setTab(tab);
    return;
  }
  closeDrawer();
  const drawerApi = createAdvancedDrawer({
    bookId, tab,
    onClose: () => {
      // 用户主动关闭：回到底页。直接改 hash 而不是 history.back()——
      // 深链 /advanced 进来时没有可回退的历史，back() 会离开应用
      location.hash = baseHashFor(bookId);
    },
  });
  state.drawer = { bookId: Number(bookId), api: drawerApi };
  document.body.append(drawerApi.node);
}

function closeDrawer() {
  if (!state.drawer) return;
  const { api: drawerApi } = state.drawer;
  state.drawer = null;
  // destroy 不触发 onClose：路由强拆时用户的目标是别的页，不该被改回底页
  drawerApi.destroy();
}

async function renderBookRoute(route, token) {
  closeDrawer();
  const book = bookOf(route.id);
  if (!book) {
    clear(view).append(el("div", { class: "empty" },
      el("strong", { text: "找不到这本书" }),
      el("p", { text: "可能还没同步，回书房刷新一下。" }),
      el("button", { class: "button", type: "button", onclick: () => go("#/"), text: "回书房" })));
    return;
  }
  state.currentBookId = Number(book.id);
  rememberBook(book.id);
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
    // 并发守卫：换书/切路由会让 routeToken 前进，在途渲染必须作废，否则会用
    // 陈旧 book 覆盖新页面（renderShelfRoute 已有同款守卫）。
    if (token !== state.routeToken) return;
    await renderWrite({ bookId: book.id, chapter, go, state });
    return;
  }
  if (route.view === "revise") {
    if (token !== state.routeToken) return;
    paintNav("改", book);
    await renderRevise({ bookId: book.id, go, state });
    return;
  }
  if (route.view === "story") {
    if (token !== state.routeToken) return;
    paintNav("故事", book);
    await renderStory({ bookId: book.id, go, state, docPath: route.doc ?? null });
    return;
  }
  if (route.view === "submit") {
    if (token !== state.routeToken) return;
    paintNav("投", book);
    await renderSubmit({ bookId: book.id, go, state });
  }
}

async function renderSettingsRoute(token) {
  paintNav("", bookOf(state.currentBookId));
  context.textContent = "设置 · 只保存在本机";
  if (token !== state.routeToken) return;
  await renderSettings({ state, go });
}

async function renderSetupRoute(token) {
  paintNav("", null);
  context.textContent = "首次开机 · 三步";
  if (token !== state.routeToken) return;
  await renderSetup({ state, go, onReady: render });
}

async function render(force = false) {
  const route = parseRoute();
  // 同路由（抽屉开/关回到底页）不重建 DOM——既不该问「离开」守卫（没有离开），
  // 也不该清它（关抽屉回到故事页，它的守卫还得继续管用）。先算出来。
  const sameRoute = !force && route.view !== "advanced" && state.baseRoute
      && state.baseRoute.view === route.view
      && state.baseRoute.id === route.id
      && state.baseRoute.chapter === route.chapter
      && state.baseRoute.doc === route.doc;
  // 真正换页才问守卫；否决时 hash 可能已变（hashchange 路径），
  // replaceState 修回进入本次 render 前保存的原样（含 ?doc= 等查询段），
  // 既不丢用户输入，也不往历史里多压一条记录。go() 路径 hash 未动，无需修。
  if (!sameRoute && state.leaveGuard && route.view !== "advanced"
      && !(await state.leaveGuard())) {
    if (location.hash !== state.lastHash) history.replaceState(null, "", state.lastHash);
    return;
  }
  state.lastHash = location.hash;
  if (!sameRoute) state.leaveGuard = null;
  // 高级抽屉是覆盖层，在 render 顶部处理、完全不进常规渲染路径：
  // 不 ++token（否则在途的底页渲染会被当作过期 token 丢弃后半段）、
  // 不剥 focus-mode/wide（那是底页自己的样式，与抽屉无关）。
  if (route.view === "advanced") {
    if (!bookOf(route.id)) { location.hash = "#/"; return; }
    openDrawer(route.id, route.tab ?? "nodes");
    // 深链直接进 /advanced 还没有底页：垫一页写页，关抽屉才有可回之处
    if (!view.childElementCount) {
      const token = ++state.routeToken;
      await renderBookRoute({ view: "write", id: route.id, chapter: null }, token);
      state.baseRoute = { view: "write", id: Number(route.id), chapter: null };
    }
    return;
  }
  // baseHash：最近一次「完整渲染的底页 URL」原样（含 ?doc= 查询段），
  // 抽屉 onClose 优先回它。必须放在 advanced 早退之后——否则开抽屉那次
  // 渲染会把 #/books/:id/advanced 自己记成 baseHash，关抽屉后 URL 卡在抽屉上。
  if (!sameRoute) state.baseHash = location.hash;
  const token = ++state.routeToken;
  // 关抽屉把 hash 改回底页路由。同一路由不重渲染——稿纸是 textarea，
  // 重建就换 DOM，未自动保存的输入会丢（计划硬口径：抽屉开关不丢稿纸）。
  if (sameRoute) {
    closeDrawer();
    return;
  }
  document.body.classList.remove("focus-mode");
  view.classList.remove("wide");
  try {
    if (route.view === "shelf") {
      const result = await renderShelfRoute(token);
      state.baseRoute = { view: "shelf", id: undefined, chapter: undefined };
      return result;
    }
    if (route.view === "setup") {
      const result = await renderSetupRoute(token);
      state.baseRoute = { view: "setup", id: undefined, chapter: undefined };
      return result;
    }
    if (route.view === "settings") {
      const result = await renderSettingsRoute(token);
      state.baseRoute = { view: "settings", id: undefined, chapter: undefined };
      return result;
    }
    const result = await renderBookRoute(route, token);
    // 只有真正渲染过底页才记 baseRoute；write 分支可能 replaceState 修正过章号，
    // 以当前 URL 为准重新解析，保证关抽屉恢复的 hash 与守卫比对一致
    state.baseRoute = parseRoute();
    return result;
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
  // B2/B4：冷启动路由。服务连上但环境没配好 → 开机向导（此前点「开新书」会直接
  // 500 books_root_missing，路径却藏在设置折叠里）；服务没连上 → 留在书架看错误条；
  // 一切正常 → 回到「上次打开的那本书」的稿纸。记忆失效（书被删/首启动）才落书架。
  if (!location.hash) {
    if (state.health?.needs_setup) location.hash = "#/setup";
    else {
      const remembered = bookOf(lastBookId());
      location.hash = remembered ? `#/books/${remembered.id}` : "#/";
    }
  }
  await render();
}

window.addEventListener("hashchange", () => render());
document.querySelector("#newBookClose").addEventListener("click", () => dialog.close());
document.querySelector("#newBookCancel").addEventListener("click", () => dialog.close());
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = document.querySelector("#newBookSubmit");
  submit.disabled = true;
  const values = Object.fromEntries(new FormData(form).entries());
  // 平台没配 GENRES 时下拉是空的：此时提交只会撞 400 invalid_genre，先在这里说清楚
  if (!values.genre) {
    toast("这个平台还没配题材（GENRES），先在 config.json 里补上", "error");
    submit.disabled = false;
    return;
  }
  try {
    const result = await api("/api/books", {
      method: "POST",
      body: {
        title: values.title,
        genre: values.genre,
        platform: values.platform || "qimao",
        target_words: Number(values.target_words) || 20000,
        vol_size: Number(values.vol_size) || 0,
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
