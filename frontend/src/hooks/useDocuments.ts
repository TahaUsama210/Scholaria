import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

const KEYS = {
  all: ["documents"] as const,
};

export function useDocuments() {
  return useQuery({
    queryKey: KEYS.all,
    queryFn: api.listDocuments,
    refetchInterval: (query) => {
      // While anything is mid-ingestion, poll for status updates.
      const items = query.state.data?.items ?? [];
      const inFlight = items.some(
        (d) => d.status === "pending" || d.status === "processing",
      );
      return inFlight ? 2000 : false;
    },
  });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.uploadDocument,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.all }),
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteDocument,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.all }),
  });
}
