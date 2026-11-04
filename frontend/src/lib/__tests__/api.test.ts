import { describe, expect, it } from "vitest";

import { errorFromResponse, messageForStatus, RATE_LIMIT_MESSAGE } from "../api";
import { normalizeCourseCode, titleFromFilename, validatePdf } from "../validation";
import { pdfFile } from "@/test/utils";

describe("messageForStatus", () => {
  it("uses friendly copy for 429/5xx/network and the API's detail for 4xx", () => {
    expect(messageForStatus(429, "Rate limit exceeded")).toBe(RATE_LIMIT_MESSAGE);
    expect(messageForStatus(503, "db down")).not.toMatch(/db down/);
    expect(messageForStatus(0)).toMatch(/can't reach the server/i);
    expect(messageForStatus(413, "PDF exceeds 50 MB limit.")).toBe("PDF exceeds 50 MB limit.");
  });
});

describe("errorFromResponse", () => {
  it("reads FastAPI validation errors and the request id header", async () => {
    const res = new Response(
      JSON.stringify({ detail: [{ loc: ["body", "question"], msg: "String should have at least 1 character" }] }),
      { status: 422, headers: { "x-request-id": "abc" } },
    );
    const err = await errorFromResponse(res);
    expect(err).toMatchObject({
      status: 422,
      requestId: "abc",
      message: "String should have at least 1 character",
    });
  });

  it("copes with non-JSON bodies", async () => {
    const err = await errorFromResponse(new Response("Bad Gateway", { status: 502 }));
    expect(err.status).toBe(502);
    expect(err.requestId).toBeNull();
  });
});

describe("upload validation", () => {
  it("accepts PDFs by MIME type or extension", () => {
    expect(validatePdf(pdfFile("a.pdf"))).toBeNull();
    expect(validatePdf(pdfFile("a.PDF", 10, ""))).toBeNull();
  });

  it("rejects other types, empty files and files over 50 MB", () => {
    expect(validatePdf(pdfFile("a.docx", 10, "application/msword"))).toMatch(/isn't a PDF/);
    expect(validatePdf(pdfFile("a.pdf", 0))).toMatch(/empty/);
    expect(validatePdf(pdfFile("a.pdf", 50 * 1024 * 1024 + 1))).toMatch(/limit is 50 MB/);
  });

  it("normalises course codes and derives titles", () => {
    expect(normalizeCourseCode("  cs  480 ")).toBe("CS 480");
    expect(titleFromFilename("lecture_03_backprop.pdf")).toBe("lecture 03 backprop");
  });
});
