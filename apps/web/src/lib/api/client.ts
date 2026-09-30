import createClient, { type Middleware } from "openapi-fetch";

import { getBackend } from "./backend";
import type { paths } from "./schema";

/** 每个请求加上后端地址与本次启动的访问令牌。 */
const backend: Middleware = {
  async onRequest({ request }) {
    const { baseUrl, token } = await getBackend();
    const url = new URL(request.url);
    const target = baseUrl ? `${baseUrl}${url.pathname}${url.search}` : request.url;
    // 不能用 new Request(target, request)：Chromium/WebView2 会把请求体变成流式上传（只支持 HTTP/2），
    // 对本机 HTTP/1.1 后端直接失败（ERR_ALPN_NEGOTIATION_FAILED）。先把请求体读出来再重建请求。
    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const body = hasBody ? await request.arrayBuffer() : undefined;
    const headers = new Headers(request.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return new Request(target, { method: request.method, headers, body, signal: request.signal });
  },
};

// baseUrl 只是占位，真实地址由中间件替换
export const api = createClient<paths>({ baseUrl: window.location.origin });
api.use(backend);

export type ApiError = {
  error?: { code?: string; message?: string; blockers?: { code: string; message: string }[] };
};

export function errorMessage(err: unknown, fallback = "请求失败，请稍后重试"): string {
  const e = err as ApiError | undefined;
  return e?.error?.message ?? fallback;
}

/** openapi-fetch 的结果：有 error 时抛出（交给 react-query 的 error），否则返回 data。 */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await p;
  if (error !== undefined) throw error;
  return data as T;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
