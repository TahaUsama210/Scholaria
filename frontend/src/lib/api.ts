import type { DocumentList, DocumentUploadResponse } from "@/types/api";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText);
    throw new Error(body);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  listDocuments(): Promise<DocumentList> {
    return request<DocumentList>("/documents");
  },

  uploadDocument({
    file,
    title,
    courseCode,
  }: {
    file: File;
    title?: string;
    courseCode?: string;
  }): Promise<DocumentUploadResponse> {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    if (courseCode) form.append("course_code", courseCode);
    return request<DocumentUploadResponse>("/documents", {
      method: "POST",
      body: form,
    });
  },

  deleteDocument(id: string): Promise<void> {
    return request<void>(`/documents/${id}`, { method: "DELETE" });
  },
};
