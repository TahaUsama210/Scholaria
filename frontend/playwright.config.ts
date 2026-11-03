import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

import { API_PORT, API_URL, WEB_PORT, WEB_URL } from "./e2e/support/env";

// End-to-end tests run the real backend in its offline profile (fake LLM, hash embeddings,
// no reranker): no API keys, no model downloads. Only Postgres+pgvector is required.
const here = path.dirname(fileURLToPath(import.meta.url));
const backendDir = path.resolve(here, "../backend");
const isWindows = process.platform === "win32";

const python =
  process.env.E2E_PYTHON ??
  (isWindows ? path.join(backendDir, ".venv", "Scripts", "python.exe") : "python");

const backendEnv: Record<string, string> = {
  DATABASE_URL:
    process.env.E2E_DATABASE_URL ??
    "postgresql+asyncpg://scholaria:scholaria@localhost:5432/scholaria_e2e",
  DATABASE_URL_SYNC:
    process.env.E2E_DATABASE_URL_SYNC ??
    "postgresql+psycopg://scholaria:scholaria@localhost:5432/scholaria_e2e",
  LLM_PROVIDER: "fake",
  EMBEDDING_PROVIDER: "hash",
  EMBEDDING_DIM: "256",
  RERANKER_PROVIDER: "none",
  // Specs share one client IP; the 429 path is covered with an injected response instead.
  RATE_LIMIT_ENABLED: "false",
  OTEL_EXPORTER_OTLP_ENDPOINT: "",
  STATIC_DIR: "",
  LOG_LEVEL: "WARNING",
  PYTHONUNBUFFERED: "1",
};

const q = (s: string) => `"${s}"`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  // Specs share one database; run them serially and reset state at the start of each test.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never" }], ["github"]]
    : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /screenshots\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      // `npm run screenshots`: regenerates the README images in docs/screenshots/.
      name: "screenshots",
      testMatch: /screenshots\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: `${q(python)} -m alembic upgrade head && ${q(python)} -m uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: backendDir,
      env: backendEnv,
      url: `${API_URL}/readyz`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      cwd: here,
      env: { VITE_PROXY_TARGET: API_URL },
      url: WEB_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
