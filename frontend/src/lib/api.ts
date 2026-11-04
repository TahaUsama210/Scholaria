import type { DocumentList, DocumentRead, DocumentUploadResponse } from "@/types/api";

/** Same-origin by default; set VITE_API_BASE_URL to call an API on another origin. */
export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export function apiUrl(path: string): string {
  return `${API_BASE}/api${path}`;
}

/** An HTTP or network failure, carrying a message that is safe to show users. */
export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;

  constructor(message: string, { status, requestId }: { status: number; requestId?: string | null }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId ?? null;
  }
}

/** status 0 = the request never got a response (offline, DNS, CORS, server down). */
export const NETWORK_ERROR_MESSAGE =
  "Can't reach the server. Check your connection and try again.";
export const RATE_LIMIT_MESSAGE =
  "You're sending requests too quickly. Please wait a moment and try again.";

/** Maps an HTTP status (+ optional server detail) to a user-facing message. */
export function messageForStatus(status: number, detail?: string | null): string {
  if (status === 0) return NETWORK_ERROR_MESSAGE;
  if (status === 429) return RATE_LIMIT_MESSAGE;
  if (status >= 500) return "The server ran into a problem. Please try again.";
  // 4xx details are written for users by the API (e.g. "PDF exceeds 50 MB limit.").
  return detail?.trim() || `Request failed (HTTP ${status}).`;
}

interface ErrorBody {
  detail?: unknown;
  request_id?: unknown;
}

function extractDetail(body: ErrorBody): string | null {
  const { detail } = body;
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{ loc, msg, type }, ...]
  if (Array.isArray(detail) && detail.length > 0) {
    const first: unknown = detail[0];
    if (first && typeof first === "object" && "msg" in first && typeof first.msg === "string") {
      return first.msg;
    }
  }
  return null;
}

/** Builds an ApiError from a non-2xx response without throwing on odd bodies. */
export async function errorFromResponse(res: Response): Promise<ApiError> {
  let body: ErrorBody = {};
  try {
    const parsed: unknown = await res.json();
    if (parsed && typeof parsed === "object") body = parsed as ErrorBody;
  } catch {
    // non-JSON body (proxy error page, empty 502, ...)
  }
  const requestId =
    res.headers.get("x-request-id") ??
    (typeof body.request_id === "string" ? body.request_id : null);
  return new ApiError(messageForStatus(res.status, extractDetail(body)), {
    status: res.status,
    requestId,
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), init);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(NETWORK_ERROR_MESSAGE, { status: 0 });
  }
  if (!res.ok) throw await errorFromResponse(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export interface UploadInput {
  file: File;
  title?: string;
  courseCode?: string;
}

export const api = {
  listDocuments(): Promise<DocumentList> {
    return request<DocumentList>("/documents");
  },

  getDocument(id: string): Promise<DocumentRead> {
    return request<DocumentRead>(`/documents/${encodeURIComponent(id)}`);
  },

  uploadDocument({ file, title, courseCode }: UploadInput): Promise<DocumentUploadResponse> {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    if (courseCode) form.append("course_code", courseCode);
    return request<DocumentUploadResponse>("/documents", { method: "POST", body: form });
  },

  deleteDocument(id: string): Promise<void> {
    return request<void>(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
};
