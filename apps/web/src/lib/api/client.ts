import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

const CSRF_COOKIE = "tkws_csrf";

function readCookie(name: string): string | undefined {
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${name}=`))
    ?.slice(name.length + 1);
}

/** 非 GET 请求自动带上 CSRF 令牌（双重提交）。 */
const csrf: Middleware = {
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      const token = readCookie(CSRF_COOKIE);
      if (token) request.headers.set("X-CSRF-Token", decodeURIComponent(token));
    }
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "same-origin" });
api.use(csrf);

export type ApiError = { error?: { code?: string; message?: string } };

export function errorMessage(err: unknown, fallback = "请求失败，请稍后重试"): string {
  const e = err as ApiError | undefined;
  return e?.error?.message ?? fallback;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
