import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 桌面应用中接口地址由 Tauri 外壳提供；浏览器开发模式下 /api 代理到 `tk_workspace.desktop --dev`（8765）。
export default defineConfig({
  // Tauri 要求：固定端口、不清屏；构建产物面向 WebView2（Chromium）
  clearScreen: false,
  build: { target: "chrome120" },
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } },
  },
});
