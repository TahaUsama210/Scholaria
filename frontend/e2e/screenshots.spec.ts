// `npm run screenshots` — regenerates the README images from a realistic answered question.
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { resetLibrary, seedDocument } from "./support/api";
import { DB_NOTES, ML_NOTES, NET_NOTES, OS_NOTES } from "./support/corpus";

const outDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../docs/screenshots");
const QUESTION = "How does backpropagation use the chain rule to compute gradients?";

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ request }) => {
  await resetLibrary(request);
  for (const doc of [ML_NOTES, DB_NOTES, OS_NOTES, NET_NOTES]) await seedDocument(request, doc);
});

async function askAndHighlight(page: Page, { inline }: { inline: boolean }) {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Course" }).selectOption("CS480");
  const box = page.getByRole("textbox", { name: "Ask a question" });
  await box.fill(QUESTION);
  await box.press("Enter");
  const answer = page.locator('[data-role="assistant"]').last();
  await expect(answer).toHaveAttribute("data-status", "done");
  const chip = answer.getByRole("button", { name: /^Show source 1: / });
  await (inline ? chip.tap() : chip.click());
  await expect(page.locator('article[aria-current="true"]')).toBeVisible();
  // Reduced motion makes scrolling instant; drop the focus ring from the screenshot.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
}

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

  test("light", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await askAndHighlight(page, { inline: false });
    await page.screenshot({ path: path.join(outDir, "desktop.png") });
  });

  test("dark", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await askAndHighlight(page, { inline: false });
    await page.screenshot({ path: path.join(outDir, "desktop-dark.png") });
  });
});

test.describe("mobile", () => {
  test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });

  test("light", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await askAndHighlight(page, { inline: true });
    await page.screenshot({ path: path.join(outDir, "mobile.png") });
  });
});
