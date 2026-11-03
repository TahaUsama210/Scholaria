import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

import type { Citation, DocumentRead } from "@/types/api";

export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) };
}

export function makeDoc(overrides: Partial<DocumentRead> = {}): DocumentRead {
  return {
    id: "doc-1",
    title: "Lecture 3 — Backpropagation",
    source_filename: "lecture3.pdf",
    course_code: "CS480",
    status: "ready",
    page_count: 12,
    chunk_count: 34,
    error_message: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeCitation(marker: number, overrides: Partial<Citation> = {}): Citation {
  return {
    marker,
    chunk_id: `chunk-${marker}`,
    document_id: "doc-1",
    document_title: `Lecture ${marker}`,
    page_number: marker + 1,
    snippet: `Snippet text for source ${marker}.`,
    score: 0.5 + marker / 100,
    ...overrides,
  };
}

export function pdfFile(name = "notes.pdf", size = 1024, type = "application/pdf"): File {
  const file = new File(["%PDF-1.4"], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}
