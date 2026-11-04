import { expect, test, type Page } from "@playwright/test";

import { fixturePdf, resetLibrary } from "./support/api";
import { ML_NOTES } from "./support/corpus";

test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });

async function expectNoHorizontalScroll(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

test.beforeEach(async ({ request }) => {
  await resetLibrary(request);
});

test("mobile: library drawer upload, cited answer, no horizontal scroll", async ({ page }) => {
  await page.goto("/");
  await expectNoHorizontalScroll(page);
  await expect(page.getByRole("complementary", { name: "Library" })).toHaveCount(0);

  // The empty-state call to action opens the library drawer.
  await page.getByRole("button", { name: "Upload a PDF" }).click();
  const drawer = page.getByRole("dialog", { name: "Library" });
  await expect(drawer).toBeVisible();
  await drawer.locator('input[type="file"]').setInputFiles({
    name: ML_NOTES.filename,
    mimeType: "application/pdf",
    buffer: fixturePdf(ML_NOTES),
  });
  await drawer.getByLabel(/^Title/).fill(ML_NOTES.title);
  await drawer.getByLabel(/^Course code/).fill("CS480");
  await drawer.getByRole("button", { name: "Upload", exact: true }).click();
  const row = drawer.getByRole("listitem", { name: ML_NOTES.title });
  await expect(row.getByText("Ready", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expectNoHorizontalScroll(page);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();

  await page.getByRole("button", { name: /How does backpropagation use the chain rule/ }).tap();
  const answer = page.locator('[data-role="assistant"]').last();
  await expect(answer).toHaveAttribute("data-status", "done");

  // Sources render inline under the answer on narrow screens.
  const chip = answer.getByRole("button", { name: /^Show source \d+: / }).first();
  const marker = await chip.getAttribute("data-marker");
  await chip.tap();
  const card = answer.locator(`article[data-marker="${marker}"]`);
  await expect(card).toHaveAttribute("aria-current", "true");
  await expect(card).toBeInViewport();
  await expectNoHorizontalScroll(page);

  // Header controls are reachable at 375 px.
  await expect(page.getByRole("combobox", { name: "Course" })).toBeVisible();
  await page.getByRole("button", { name: /Open library/ }).tap();
  await expect(drawer.getByRole("listitem", { name: ML_NOTES.title })).toBeVisible();
});
