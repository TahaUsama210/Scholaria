// Load test: steady browsing traffic plus a ramp of concurrent chat users.
//   docker compose --profile loadtest run --rm k6 run -q /scripts/load.js
// Tunables (env): CHAT_VUS (10), BROWSE_RPS (20), RAMP_S (30), STEADY_S (120), DOWN_S (15),
// THINK_S (1). Thresholds are targets for the offline profile (fake LLM, hash embedder), so
// they measure the platform (HTTP, retrieval, SSE), not an external LLM's latency.
import { sleep } from "k6";
import {
  TREND_STATS,
  chat,
  healthz,
  listDocuments,
  pickQuestion,
  requireReady,
  summarize,
} from "./lib/scholaria.js";

const num = (name, fallback) => Number(__ENV[name] || fallback);
const CHAT_VUS = num("CHAT_VUS", 10);
const RAMP_S = num("RAMP_S", 30);
const STEADY_S = num("STEADY_S", 120);
const DOWN_S = num("DOWN_S", 15);
const THINK_S = num("THINK_S", 1);

export const options = {
  summaryTrendStats: TREND_STATS,
  scenarios: {
    browse: {
      executor: "constant-arrival-rate",
      exec: "browse",
      rate: num("BROWSE_RPS", 20),
      timeUnit: "1s",
      duration: `${RAMP_S + STEADY_S + DOWN_S}s`,
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
    chat: {
      executor: "ramping-vus",
      exec: "chatter",
      startVUs: 0,
      stages: [
        { duration: `${RAMP_S}s`, target: CHAT_VUS },
        { duration: `${STEADY_S}s`, target: CHAT_VUS },
        { duration: `${DOWN_S}s`, target: 0 },
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    http_req_failed: [{ threshold: "rate<0.01", abortOnFail: true, delayAbortEval: "30s" }],
    checks: ["rate>0.99"],
    chat_errors: ["rate<0.01"],
    "http_req_duration{endpoint:healthz}": ["p(95)<100"],
    "http_req_duration{endpoint:documents}": ["p(95)<300"],
    "http_req_duration{endpoint:chat}": ["p(95)<1500", "p(99)<3000"],
    chat_ttfb: ["p(95)<1000"],
  },
};

export function setup() {
  return requireReady();
}

export function browse() {
  listDocuments();
  healthz();
}

export function chatter() {
  chat(pickQuestion());
  sleep(THINK_S * (0.5 + Math.random()));
}

export function handleSummary(data) {
  return summarize("load", data);
}
