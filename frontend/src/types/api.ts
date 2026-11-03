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
  /** 1-based marker matching `[n]` in the answer text. */
  marker: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  page_number: number | null;
  snippet: string;
  score?: number | null;
}

export type ChatRole = "user" | "assistant";

export interface ChatTurn {
  role: ChatRole;
  content: string;
}

export interface ChatRequest {
  question: string;
  course_code?: string | null;
  /** Prior turns (oldest first, max 10); the backend condenses follow-ups. */
  history?: ChatTurn[];
}

export interface DonePayload {
  answer: string;
  [extra: string]: unknown;
}

export interface ErrorPayload {
  message: string;
  request_id?: string;
}

export type StreamEvent =
  | { type: "context"; data: Citation[] }
  | { type: "token"; data: string }
  | { type: "citations"; data: Citation[] }
  | { type: "done"; data: DonePayload }
  | { type: "error"; data: ErrorPayload };
