import { expect, test, type Page } from "@playwright/test";

import type { ChatRequest } from "../src/types/api";
import { resetLibrary, seedDocument } from "./support/api";
import { DB_NOTES, ML_NOTES } from "./support/corpus";

async function ask(page: Page, question: string): Promise<ChatRequest> {
  const request = page.waitForRequest((r) => r.url().endsWith("/api/chat/stream"));
  const box = page.getByRole("textbox", { name: "Ask a question" });
  await box.fill(question);
  await box.press("Enter");
  return (await request).postDataJSON() as ChatRequest;
}

const lastAnswer = (page: Page) => page.locator('[data-role="assistant"]').last();
const rail = (page: Page) => page.getByRole("complementary", { name: "Sources for the selected answer" });

test.beforeEach(async ({ request }) => {
  await resetLibrary(request);
  await seedDocument(request, ML_NOTES);
});

test("streams a cited answer; clicking a chip highlights its source", async ({ page }) => {
  await page.goto("/");
  const response = page.waitForResponse((r) => r.url().endsWith("/api/chat/stream"));
  const body = await ask(page, "How does backpropagation use the chain rule?");
  expect(body).toMatchObject({ course_code: null, history: [] });
  expect((await response).headers()["content-type"]).toContain("text/event-stream");

  const answer = lastAnswer(page);
  await expect(answer).toHaveAttribute("data-status", "done");
  await expect(answer).toContainText("chain rule");

  const chips = answer.getByRole("button", { name: /^Show source \d+: / });
  await expect(chips.first()).toBeVisible();
  const chip = chips.first();
  const marker = await chip.getAttribute("data-marker");
  await chip.click();

  const card = rail(page).locator(`article[data-marker="${marker}"]`);
  await expect(card).toHaveAttribute("aria-current", "true");
  await expect(card).toBeInViewport();
  await expect(card.getByText("Cited", { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "Hide details" })).toHaveAttribute("aria-expanded", "true");
  await expect(chip).toHaveAttribute("data-active", "true");
  await expect(rail(page).locator('article[aria-current="true"]')).toHaveCount(1);
});

test("follow-up questions send the previous turns as history", async ({ page }) => {
  await page.goto("/");
  const first = "What is backpropagation?";
  await ask(page, first);
  await expect(lastAnswer(page)).toHaveAttribute("data-status", "done");
  const firstAnswer = (await lastAnswer(page).locator(".prose-answer").innerText()).trim();

  const body = await ask(page, "How is the learning rate involved?");
  expect(body.history).toHaveLength(2);
  expect(body.history?.[0]).toEqual({ role: "user", content: first });
  expect(body.history?.[1]?.role).toBe("assistant");
  expect(body.history?.[1]?.content).toContain("[1]");
  expect(firstAnswer).toContain("Based on the course materials");
  await expect(lastAnswer(page)).toHaveAttribute("data-status", "done");
  await expect(page.locator('[data-role="assistant"]')).toHaveCount(2);

  // New chat starts over with an empty history.
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page.locator('[data-role="assistant"]')).toHaveCount(0);
  expect((await ask(page, "What is overfitting?")).history).toEqual([]);
});

test("course filter scopes retrieval to the selected course", async ({ page, request }) => {
  await seedDocument(request, DB_NOTES);
  await page.goto("/");

  const filter = page.getByRole("combobox", { name: "Course" });
  await expect(filter.locator("option")).toHaveText(["All courses", "CS348", "CS480"]);
  await filter.selectOption("CS348");
  await expect(page.getByRole("region", { name: "Chat" })).toContainText("Answering from CS348");

  const question = "What does third normal form forbid?";
  expect((await ask(page, question)).course_code).toBe("CS348");
  await expect(lastAnswer(page)).toHaveAttribute("data-status", "done");
  const scoped = rail(page).locator("article h3");
  await expect(scoped.first()).toBeVisible();
  for (const title of await scoped.allInnerTexts()) expect(title).toContain(DB_NOTES.title);

  await filter.selectOption("CS480");
  expect((await ask(page, question)).course_code).toBe("CS480");
  await expect(lastAnswer(page)).toHaveAttribute("data-status", "done");
  for (const title of await rail(page).locator("article h3").allInnerTexts()) {
    expect(title).toContain(ML_NOTES.title);
  }
});
