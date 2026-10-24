export class ApiError extends Error {
  constructor(message, { status = 0, code = null, details = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function api(path, { method = "GET", body = null } = {}) {
  const init = { method };
  if (body !== null) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    throw new ApiError(data?.error?.message ?? `请求失败（HTTP ${response.status}）`, {
      status: response.status,
      code: data?.error?.code ?? null,
      details: data?.error?.details ?? null,
    });
  }
  return data;
}

export function openStudio(bookId) {
  const suffix = bookId ? `#/books/${bookId}` : "";
  window.location.href = `/studio.html${suffix}`;
}
