import createClient, { type Middleware } from "openapi-fetch";

import { getBackend } from "./backend";
import type { paths } from "./schema";

/** 每个请求加上后端地址与本次启动的访问令牌。 */
const backend: Middleware = {
  async onRequest({ request }) {
    const { baseUrl, token } = await getBackend();
    const url = new URL(request.url);
    const target = baseUrl ? `${baseUrl}${url.pathname}${url.search}` : request.url;
    const next = new Request(target, request);
    next.headers.set("Authorization", `Bearer ${token}`);
    return next;
  },
};

// baseUrl 只是占位，真实地址由中间件替换
export const api = createClient<paths>({ baseUrl: window.location.origin });
api.use(backend);

export type ApiError = { error?: { code?: string; message?: string } };

export function errorMessage(err: unknown, fallback = "请求失败，请稍后重试"): string {
  const e = err as ApiError | undefined;
  return e?.error?.message ?? fallback;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
