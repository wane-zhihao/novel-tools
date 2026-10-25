import { createReadStream, existsSync, statSync } from "node:fs";
import { extname } from "node:path";

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost"]);

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

export class HttpError extends Error {
  constructor(status, code, message, details = null) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function sendJson(response, status, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
  });
  response.end(body);
}

export function sendText(response, status, text, type = "text/plain; charset=utf-8") {
  response.writeHead(status, {
    "Content-Type": type,
    "Content-Length": Buffer.byteLength(text),
    "Cache-Control": "no-store",
  });
  response.end(text);
}

export async function readJsonBody(request, { limit = 1_000_000 } = {}) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) throw new HttpError(413, "payload_too_large", "请求体过大");
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  let parsed;
  try {
    parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new HttpError(400, "invalid_json", "请求体不是合法 JSON");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new HttpError(400, "invalid_body", "请求体必须是 JSON 对象");
  }
  return parsed;
}

export function requestHostname(value) {
  if (!value) return "";
  try {
    return new URL(`http://${value}`).hostname.replace(/^\[|\]$/g, "").toLowerCase();
  } catch {
    return "";
  }
}

export function originHostname(value) {
  if (!value) return "";
  try {
    return new URL(value).hostname.replace(/^\[|\]$/g, "").toLowerCase();
  } catch {
    return "";
  }
}

/** Origin 是否与服务端自身同源。端口必须严格匹配（审查 A5 的攻击向量）；
 *  主机名允许回环别名等价（127.0.0.1 / ::1 / localhost）——同端口只有一个
 *  进程能绑定，等价不增加攻击面，但避免用户换个别名打开就连不上。 */
export function isSameOrigin(originValue, hostValue) {
  if (!originValue || !hostValue) return false;
  let origin;
  let host;
  try {
    origin = new URL(originValue);
    host = new URL(`http://${hostValue}`);
  } catch {
    return false;
  }
  if (origin.protocol !== "http:" && origin.protocol !== "https:") return false;
  if (origin.port !== host.port) return false;
  const oh = origin.hostname.replace(/^\[|\]$/g, "");
  const hh = host.hostname.replace(/^\[|\]$/g, "");
  if (oh === hh) return true;
  return LOOPBACK_HOSTS.has(oh) && LOOPBACK_HOSTS.has(hh);
}

/** 只接受本机回环 Host；写操作还要求 Origin 与服务端同源，挡住浏览器跨站请求。 */
export function assertLocalRequest(request, { allowNetwork = false, networkToken = null } = {}) {
  if (allowNetwork) {
    if (!networkToken) {
      throw new HttpError(403, "network_token_required", "绑定非回环地址必须设置 WORKBENCH_NETWORK_TOKEN");
    }
    if (request.headers["x-workbench-token"] !== networkToken) {
      throw new HttpError(403, "invalid_network_token", "缺少或错误的 x-workbench-token");
    }
    return;
  }
  const host = requestHostname(request.headers.host);
  if (!LOOPBACK_HOSTS.has(host)) {
    throw new HttpError(403, "invalid_host", "工作台只接受本机回环地址请求");
  }
  if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method)) {
    // 审查 A5：只校验 Origin 主机名不校验端口时，本机任意 localhost 页面都能
    // 发起写请求（本地 CSRF → 改 python 路径 → RCE 链）。现在要求同源。
    const origin = request.headers.origin;
    if (origin && !isSameOrigin(origin, request.headers.host)) {
      throw new HttpError(403, "invalid_origin", "拒绝来自非本服务工作台的写入请求");
    }
    const fetchSite = String(request.headers["sec-fetch-site"] ?? "").toLowerCase();
    if (fetchSite && fetchSite !== "same-origin" && fetchSite !== "none") {
      throw new HttpError(403, "invalid_fetch_site", `拒绝跨站写入请求（Sec-Fetch-Site: ${fetchSite}）`);
    }
  }
}

export function isLoopbackHost(host) {
  return LOOPBACK_HOSTS.has(String(host ?? "").toLowerCase());
}

export function serveStaticFile(response, filePath) {
  if (!existsSync(filePath) || !statSync(filePath).isFile()) {
    throw new HttpError(404, "not_found", "页面不存在");
  }
  const type = CONTENT_TYPES[extname(filePath).toLowerCase()] ?? "application/octet-stream";
  response.writeHead(200, {
    "Content-Type": type,
    "Content-Length": statSync(filePath).size,
    "Cache-Control": "no-cache",
    // 审查 C4：本地工作台也是浏览器上下文——提示注入若能在页面里落脚本，
    // CSP 是最后一道闸。style-src 需 'unsafe-inline'：界面用 setAttribute 设
    // 行内样式（lib/dom.js）；script-src 严格（事件监听不走 CSP）。
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  });
  createReadStream(filePath).pipe(response);
}

export function errorPayload(error) {
  if (error instanceof HttpError) {
    return {
      status: error.status,
      body: {
        error: {
          code: error.code,
          message: error.message,
          ...(error.details ? { details: error.details } : {}),
        },
      },
    };
  }
  return {
    status: 500,
    body: { error: { code: "internal_error", message: "工作台处理请求时发生错误" } },
  };
}
