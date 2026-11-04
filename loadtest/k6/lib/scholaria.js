// Shared request helpers, custom metrics and summary output for the Scholaria k6 scripts.
import http from "k6/http";
import { check } from "k6";
import { Rate, Trend } from "k6/metrics";

export const BASE_URL = (__ENV.BASE_URL || "http://localhost:8000").replace(/\/+$/, "");

// Full SSE stream duration (request sent -> stream closed), and time until the stream opens
// (headers are flushed before retrieval runs, so this shows queueing, not time to first token;
// server-side TTFT is scholaria_rag_stage_duration_seconds{stage="generate_ttft"}).
export const chatStreamDuration = new Trend("chat_stream_duration", true);
export const chatTtfb = new Trend("chat_ttfb", true);
// Share of chat streams that ended in an `error` event (HTTP status is 200 either way).
export const chatErrors = new Rate("chat_errors");

// Questions over the demo corpus; the last one filters on a course with no documents and
// exercises the no-context path.
const QUESTIONS = [
  { question: "How does backpropagation compute gradients?", course_code: "CS480" },
  { question: "What are common remedies for overfitting?", course_code: "CS480" },
  { question: "How does k-means clustering assign points?", course_code: null },
  { question: "What does MVCC change about isolation levels?", course_code: "CS348" },
  { question: "Why does write-ahead logging make crash recovery possible?", course_code: null },
  { question: "What conditions are required for deadlock?", course_code: "CS350" },
  { question: "What causes thrashing in virtual memory?", course_code: "CS350" },
  { question: "How does TCP congestion control react to packet loss?", course_code: "CS456" },
  { question: "What does DNS resolution involve?", course_code: null },
  { question: "What is the capital of Atlantis?", course_code: "ZZ999" },
];

export function pickQuestion() {
  return QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
}

export function healthz() {
  const res = http.get(`${BASE_URL}/healthz`, { tags: { endpoint: "healthz" } });
  check(res, { "healthz: 200": (r) => r.status === 200 });
  return res;
}

export function listDocuments() {
  const res = http.get(`${BASE_URL}/api/documents`, { tags: { endpoint: "documents" } });
  check(res, {
    "documents: 200": (r) => r.status === 200,
    "documents: json": (r) => (r.headers["Content-Type"] || "").includes("application/json"),
  });
  return res;
}

// Parses an SSE body into [{event, data}] (sse-starlette separates events with \r\n\r\n).
export function parseSse(body) {
  const events = [];
  for (const block of (body || "").split(/\r?\n\r?\n/)) {
    let event = "message";
    const data = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return events;
}

// POST /api/chat/stream, consumed as one plain HTTP response (k6 buffers the whole stream).
export function chat(payload) {
  const res = http.post(`${BASE_URL}/api/chat/stream`, JSON.stringify(payload), {
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    tags: { endpoint: "chat" },
    timeout: "60s",
  });
  chatStreamDuration.add(res.timings.duration);
  chatTtfb.add(res.timings.waiting);

  const events = res.status === 200 ? parseSse(res.body) : [];
  const names = events.map((e) => e.event);
  const done = events.find((e) => e.event === "done");
  let answer = "";
  try {
    answer = done ? JSON.parse(done.data).answer || "" : "";
  } catch (_) {
    answer = "";
  }
  chatErrors.add(names.includes("error"));
  check(res, {
    "chat: 200": (r) => r.status === 200,
    "chat: event-stream": (r) => (r.headers["Content-Type"] || "").startsWith("text/event-stream"),
    "chat: context first": () => names[0] === "context",
    "chat: done with answer": () => answer.length > 0,
    "chat: no error event": () => !names.includes("error"),
  });
  return res;
}

// Fails fast when the target is not ready, so a misconfigured stack doesn't produce noise.
export function requireReady() {
  const res = http.get(`${BASE_URL}/readyz`, { tags: { endpoint: "readyz" } });
  if (res.status !== 200) {
    throw new Error(`${BASE_URL}/readyz returned ${res.status}; is the stack up?`);
  }
  const docs = http.get(`${BASE_URL}/api/documents`, { tags: { endpoint: "setup" } });
  let total = 0;
  try {
    const body = docs.json();
    total = Array.isArray(body) ? body.length : body.total || (body.items || []).length;
  } catch (_) {
    total = 0;
  }
  if (total === 0) console.warn("No documents ingested: chats will take the no-context path.");
  return { documents: total };
}

const fmtMs = (v) => (v === undefined ? "-" : `${v.toFixed(v < 10 ? 2 : 0)} ms`);

// Markdown summary on stdout (also suitable for $GITHUB_STEP_SUMMARY) plus raw JSON when
// RESULTS_DIR is set.
export function summarize(name, data) {
  const m = data.metrics;
  const lines = [`### k6 ${name}: ${BASE_URL}`, ""];
  const reqs = m.http_reqs ? m.http_reqs.values : { count: 0, rate: 0 };
  const failed = m.http_req_failed ? m.http_req_failed.values.rate : 0;
  const checks = m.checks ? m.checks.values : { passes: 0, fails: 0, rate: 0 };
  const seconds = (data.state.testRunDurationMs / 1000).toFixed(0);
  lines.push(
    `Duration ${seconds}s, ${reqs.count} requests (${reqs.rate.toFixed(1)}/s), ` +
      `failed ${(failed * 100).toFixed(2)}%, checks ${checks.passes}/${checks.passes + checks.fails}` +
      (m.chat_errors ? `, chat error events ${(m.chat_errors.values.rate * 100).toFixed(2)}%` : ""),
    "",
    "| Metric | count | avg | p50 | p95 | p99 | max |",
    "|---|---:|---:|---:|---:|---:|---:|",
  );
  const trends = [
    ["http_req_duration{endpoint:healthz}", "GET /healthz"],
    ["http_req_duration{endpoint:documents}", "GET /api/documents"],
    ["http_req_duration{endpoint:chat}", "POST /api/chat/stream (full stream)"],
    ["chat_ttfb", "chat stream opened (TTFB)"],
  ];
  for (const [key, label] of trends) {
    const t = m[key];
    if (!t) continue;
    const v = t.values;
    lines.push(
      `| ${label} | ${v.count} | ${fmtMs(v.avg)} | ${fmtMs(v.med)} | ${fmtMs(v["p(95)"])} | ` +
        `${fmtMs(v["p(99)"])} | ${fmtMs(v.max)} |`,
    );
  }
  lines.push("", "| Threshold | result |", "|---|---|");
  for (const [metric, def] of Object.entries(m)) {
    for (const [expr, res] of Object.entries(def.thresholds || {})) {
      lines.push(`| \`${metric}\` ${expr} | ${res.ok ? "pass" : "**FAIL**"} |`);
    }
  }
  const out = { stdout: lines.join("\n") + "\n" };
  if (__ENV.RESULTS_DIR) out[`${__ENV.RESULTS_DIR}/${name}-summary.json`] = JSON.stringify(data, null, 2);
  return out;
}

export const TREND_STATS = ["avg", "min", "med", "p(95)", "p(99)", "max", "count"];
