import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // The app calls the API same-origin (/api/...); in dev, Vite forwards to the backend.
  const target = env.VITE_PROXY_TARGET || "http://localhost:8000";
  const proxied = { target, changeOrigin: true };

  return {
    plugins: [react()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/api": proxied,
        "/healthz": proxied,
        "/readyz": proxied,
      },
    },
    preview: {
      proxy: { "/api": proxied, "/healthz": proxied, "/readyz": proxied },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      css: false,
    },
  };
});
