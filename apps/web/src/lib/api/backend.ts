/**
 * 后端连接信息。
 * - 桌面应用中：由 Tauri 外壳提供本次启动的端口和令牌（invoke("backend_info")，会等待后端就绪）。
 * - 浏览器开发模式：通过 Vite 代理访问 `uv run python -m tk_workspace.desktop --dev`（端口 8765，开发令牌）。
 */
import { invoke, isTauri } from "@tauri-apps/api/core";

export type BackendInfo = { baseUrl: string; token: string };

let pending: Promise<BackendInfo> | null = null;

export function getBackend(): Promise<BackendInfo> {
  if (!pending) {
    pending = (async () => {
      if (isTauri()) {
        const info = await invoke<{ port: number; token: string }>("backend_info");
        return { baseUrl: `http://127.0.0.1:${info.port}`, token: info.token };
      }
      return { baseUrl: "", token: import.meta.env.VITE_DEV_TOKEN ?? "dev-token" };
    })();
    // 失败时允许重试
    pending.catch(() => {
      pending = null;
    });
  }
  return pending;
}
