import { expect, type APIRequestContext } from "@playwright/test";

import type { DocumentList, DocumentRead, DocumentUploadResponse } from "../../src/types/api";
import type { FixtureDoc } from "./corpus";
import { API_URL } from "./env";
import { makePdf } from "./pdf";

/** Deletes every document so each test starts from an empty library. */
export async function resetLibrary(request: APIRequestContext): Promise<void> {
  for (let guard = 0; guard < 20; guard += 1) {
    const res = await request.get(`${API_URL}/api/documents`);
    expect(res.ok(), await res.text()).toBeTruthy();
    const { items } = (await res.json()) as DocumentList;
    if (items.length === 0) return;
    for (const doc of items) {
      const del = await request.delete(`${API_URL}/api/documents/${doc.id}`);
      expect([204, 404]).toContain(del.status());
    }
  }
  throw new Error("library did not empty");
}

export function fixturePdf(doc: FixtureDoc): Buffer {
  return makePdf(doc.pages, { title: doc.title });
}

/** Uploads through the API and waits until ingestion finishes. */
export async function seedDocument(request: APIRequestContext, doc: FixtureDoc): Promise<DocumentRead> {
  const res = await request.post(`${API_URL}/api/documents`, {
    multipart: {
      file: { name: doc.filename, mimeType: "application/pdf", buffer: fixturePdf(doc) },
      title: doc.title,
      course_code: doc.courseCode,
    },
  });
  expect(res.status(), await res.text()).toBe(202);
  const { document } = (await res.json()) as DocumentUploadResponse;
  return waitForReady(request, document.id);
}

export async function waitForReady(request: APIRequestContext, id: string): Promise<DocumentRead> {
  let latest: DocumentRead | undefined;
  await expect
    .poll(
      async () => {
        const res = await request.get(`${API_URL}/api/documents/${id}`);
        latest = (await res.json()) as DocumentRead;
        return latest.status;
      },
      { timeout: 30_000, intervals: [250, 500, 1000] },
    )
    .toMatch(/ready|failed/);
  expect(latest?.status, latest?.error_message ?? "").toBe("ready");
  return latest as DocumentRead;
}
