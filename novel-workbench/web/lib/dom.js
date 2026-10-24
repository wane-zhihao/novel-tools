export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "html") node.innerHTML = value;
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

export function clear(node) {
  if (node) node.replaceChildren();
  return node;
}

export function fmtNumber(value) {
  return Number(value ?? 0).toLocaleString("zh-CN");
}

export function fmtDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function fmtShortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString("zh-CN");
}

export function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function toast(message, tone = "info") {
  const region = document.querySelector("#toastRegion");
  if (!region) return;
  const node = el("div", { class: "toast", "data-tone": tone, text: message });
  if (tone === "error") {
    // 错误类常驻至手动关闭（P2：不再 4.2 秒自消后线索丢失）
    node.append(el("button", {
      class: "toast-close", type: "button", "aria-label": "关闭", text: "×",
      onclick: () => node.remove(),
    }));
  } else {
    setTimeout(() => node.remove(), 4200);
  }
  region.append(node);
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

export function renderMarkdown(text) {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .split(/\n{2,}/)
    .map((block) => (/^<h[1-3]>/.test(block) ? block : `<p>${block.replaceAll("\n", "<br>")}</p>`))
    .join("");
}
