import { expect, test } from "@playwright/test";

import { fixturePdf, resetLibrary, seedDocument } from "./support/api";
import { DB_NOTES, ML_NOTES } from "./support/corpus";
import { API_URL } from "./support/env";

test.beforeEach(async ({ request }) => {
  await resetLibrary(request);
});

test("uploads a PDF with title and course code; it becomes ready", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("No documents yet")).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: ML_NOTES.filename,
    mimeType: "application/pdf",
    buffer: fixturePdf(ML_NOTES),
  });
  await expect(page.getByText(ML_NOTES.filename)).toBeVisible();
  await page.getByLabel(/^Title/).fill("Neural Networks Notes");
  await page.getByLabel(/^Course code/).fill("cs480");
  await page.getByRole("button", { name: "Upload", exact: true }).click();

  await expect(page.getByText("Uploaded “Neural Networks Notes”")).toBeVisible();
  const row = page.getByRole("listitem", { name: "Neural Networks Notes" });
  await expect(row.locator('[data-status="ready"]')).toHaveText("Ready", { timeout: 30_000 });
  await expect(row).toContainText("3 pages");
  await expect(row.getByText("CS480", { exact: true })).toBeVisible();

  // The new course code shows up in the header filter.
  await expect(page.getByRole("combobox", { name: "Course" }).locator("option")).toHaveText([
    "All courses",
    "CS480",
  ]);
});

test("rejects non-PDF files before uploading", async ({ page }) => {
  let uploads = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/api/documents")) uploads += 1;
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("not a pdf"),
  });
  await expect(page.getByRole("alert")).toContainText("“notes.txt” isn't a PDF");
  expect(uploads).toBe(0);
});

test("deletes a document after confirmation", async ({ page, request }) => {
  await seedDocument(request, DB_NOTES);
  await page.goto("/");
  const row = page.getByRole("listitem", { name: DB_NOTES.title });
  await expect(row).toBeVisible();

  await row.getByRole("button", { name: `Delete ${DB_NOTES.title}` }).click();
  const confirm = row.getByRole("group", { name: `Confirm deleting ${DB_NOTES.title}` });
  await expect(confirm).toContainText("This can't be undone.");
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toBeHidden();

  await row.getByRole("button", { name: `Delete ${DB_NOTES.title}` }).click();
  await row.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(row).toBeHidden();
  await expect(page.getByText("No documents yet")).toBeVisible();

  const res = await request.get(`${API_URL}/api/documents`);
  expect((await res.json()).total).toBe(0);
});
