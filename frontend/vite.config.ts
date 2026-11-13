import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy /api to the FastAPI backend. `changeOrigin` rewrites
      // Host so SSE doesn't 400 on strict hosts. `ws: true` keeps
      // websockets working if we ever add them. SSE is streamed by
      // default (http-proxy passes through chunked responses); the
      // `Cache-Control: no-cache` + `X-Accel-Buffering: no` headers
      // set by the backend tell any upstream proxy not to buffer.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    // Tests and Playwright e2e stay on fixtures. Real-API wiring is
    // only exercised in dev/preview/prod builds.
    env: { VITE_FIXTURES: "1" },
    // Playwright owns tests/e2e/* — vitest would try to import them
    // and crash on `@playwright/test`'s expect clash.
    exclude: ["node_modules/**", "dist/**", "tests/e2e/**"],
  },
});
