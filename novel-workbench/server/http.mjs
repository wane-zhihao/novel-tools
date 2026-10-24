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

/** 只接受本机回环 Host；写操作还要求 Origin 也是回环，挡住浏览器跨站请求。 */
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
  if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method) && request.headers.origin) {
    const origin = originHostname(request.headers.origin);
    if (!LOOPBACK_HOSTS.has(origin)) {
      throw new HttpError(403, "invalid_origin", "拒绝来自非本机页面的写入请求");
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
