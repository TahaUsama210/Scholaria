// Smoke test: one virtual user walks every endpoint; any failure fails the run.
//   docker compose --profile loadtest run --rm k6 run -q /scripts/smoke.js
import { group, sleep } from "k6";
import {
  TREND_STATS,
  chat,
  healthz,
  listDocuments,
  pickQuestion,
  requireReady,
  summarize,
} from "./lib/scholaria.js";

export const options = {
  vus: 1,
  iterations: Number(__ENV.ITERATIONS || 20),
  summaryTrendStats: TREND_STATS,
  thresholds: {
    checks: ["rate==1"],
    http_req_failed: ["rate==0"],
    chat_errors: ["rate==0"],
    "http_req_duration{endpoint:healthz}": ["p(95)<200"],
    "http_req_duration{endpoint:documents}": ["p(95)<500"],
    "http_req_duration{endpoint:chat}": ["p(95)<3000"],
    chat_ttfb: ["p(95)<2000"],
  },
};

export function setup() {
  return requireReady();
}

export default function () {
  group("probes", () => healthz());
  group("documents", () => listDocuments());
  group("chat", () => chat(pickQuestion()));
  sleep(0.2);
}

export function handleSummary(data) {
  return summarize("smoke", data);
}
