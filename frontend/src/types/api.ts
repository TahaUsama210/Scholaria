// Mirrors backend Pydantic schemas. Keep in sync — or generate from /openapi.json.

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface DocumentRead {
  id: string;
  title: string;
  source_filename: string;
  course_code: string | null;
  status: DocumentStatus;
  page_count: number;
  chunk_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentList {
  items: DocumentRead[];
  total: number;
}

export interface DocumentUploadResponse {
  document: DocumentRead;
  message: string;
}

export interface Citation {
  marker: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  page_number: number | null;
  snippet: string;
  score?: number;
}

export interface ChatRequest {
  question: string;
  course_code?: string | null;
  conversation_id?: string | null;
}

export type StreamEvent =
  | { type: "context"; data: Citation[] }
  | { type: "token"; data: string }
  | { type: "citations"; data: Citation[] }
  | { type: "done"; data: { answer: string } }
  | { type: "error"; data: { message: string } };
