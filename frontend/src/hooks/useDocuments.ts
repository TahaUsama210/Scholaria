import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type UploadInput } from "@/lib/api";
import type { DocumentList, DocumentRead } from "@/types/api";

export const documentKeys = {
  all: ["documents"] as const,
};

const POLL_MS = 1500;

export function isInFlight(doc: DocumentRead): boolean {
  return doc.status === "pending" || doc.status === "processing";
}

export function useDocuments() {
  return useQuery({
    queryKey: documentKeys.all,
    queryFn: api.listDocuments,
    // Poll only while something is mid-ingestion.
    refetchInterval: (query) => (query.state.data?.items.some(isInFlight) ? POLL_MS : false),
  });
}

function upsert(list: DocumentList | undefined, doc: DocumentRead): DocumentList | undefined {
  if (!list) return list;
  const exists = list.items.some((d) => d.id === doc.id);
  return exists
    ? { ...list, items: list.items.map((d) => (d.id === doc.id ? doc : d)) }
    : { items: [doc, ...list.items], total: list.total + 1 };
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: UploadInput) => api.uploadDocument(input),
    onSuccess: ({ document }) => {
      // Show the new row immediately (and start polling), then reconcile with the server.
      qc.setQueryData<DocumentList>(documentKeys.all, (prev) => upsert(prev, document));
      void qc.invalidateQueries({ queryKey: documentKeys.all });
    },
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: (_void, id) => {
      qc.setQueryData<DocumentList>(documentKeys.all, (prev) =>
        prev ? { items: prev.items.filter((d) => d.id !== id), total: Math.max(0, prev.total - 1) } : prev,
      );
      void qc.invalidateQueries({ queryKey: documentKeys.all });
    },
  });
}
