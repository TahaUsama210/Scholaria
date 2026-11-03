import { expect, test, type Page, type Route } from "@playwright/test";

import { resetLibrary } from "./support/api";

const STREAM = "**/api/chat/stream";

async function ask(page: Page, question: string) {
  const box = page.getByRole("textbox", { name: "Ask a question" });
  await box.fill(question);
  await box.press("Enter");
}

const sse = (frames: string) => ({
  status: 200,
  headers: { "content-type": "text/event-stream", "x-request-id": "e2e-req-123" },
  body: frames,
});

test.beforeEach(async ({ request, page }) => {
  await resetLibrary(request);
  await page.goto("/");
});

test("server error events show a safe message, the request id and a retry", async ({ page }) => {
  await page.route(STREAM, (route) =>
    route.fulfill(
      sse(
        'event: error\ndata: {"message":"The answer service is temporarily unavailable.","request_id":"e2e-req-123"}\n\n',
      ),
    ),
  );
  await ask(page, "What is backpropagation?");

  const alert = page.getByRole("alert");
  await expect(alert).toContainText("The answer service is temporarily unavailable.");
  await expect(alert).toContainText("Request ID: e2e-req-123");

  // Retry goes to the real backend (empty library → the "couldn't find" answer).
  await page.unroute(STREAM);
  await alert.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator('[data-role="assistant"]').last()).toHaveAttribute("data-status", "done");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator('[data-role="assistant"]')).toHaveCount(1);
});

test("a 429 shows a friendly rate-limit message", async ({ page }) => {
  await page.route(STREAM, (route) =>
    route.fulfill({
      status: 429,
      contentType: "application/json",
      headers: { "x-request-id": "rl-e2e-1" },
      body: JSON.stringify({ detail: "Rate limit exceeded: 20 per 1 minute" }),
    }),
  );
  await ask(page, "Q");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("You're sending requests too quickly");
  await expect(alert).toContainText("rl-e2e-1");
  await expect(alert).not.toContainText("20 per 1 minute");
});

test("network failure reports the server as unreachable", async ({ page }) => {
  await page.route(STREAM, (route) => route.abort("connectionfailed"));
  await ask(page, "Q");
  await expect(page.getByRole("alert")).toContainText("Can't reach the server");
});

test("a stream that ends early is reported as interrupted, keeping the partial answer", async ({
  page,
}) => {
  await page.route(STREAM, (route) =>
    route.fulfill(sse('event: context\ndata: []\n\nevent: token\ndata: "Partial answer"\n\n')),
  );
  await ask(page, "Anything");
  await expect(page.getByRole("alert")).toContainText("interrupted");
  await expect(page.getByRole("alert")).toContainText("e2e-req-123");
  await expect(page.locator('[data-role="assistant"]').last()).toContainText("Partial answer");
});

test("Stop cancels a pending answer", async ({ page }) => {
  let held: Route | undefined;
  await page.route(STREAM, (route) => {
    held = route; // never answered: the request stays pending until the client aborts
  });
  await ask(page, "Slow question");
  const answer = page.locator('[data-role="assistant"]').last();
  await expect(answer).toContainText("Searching your materials");

  await page.getByRole("button", { name: "Stop generating" }).click();
  await expect(answer).toHaveAttribute("data-status", "stopped");
  await expect(answer).toContainText("Generation stopped.");
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await held?.abort().catch(() => undefined);
});
