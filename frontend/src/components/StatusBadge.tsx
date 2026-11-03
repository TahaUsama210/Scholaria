import { AlertCircle, CheckCircle2, Clock, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import type { DocumentStatus } from "@/types/api";

const STYLES: Record<DocumentStatus, { label: string; className: string; icon: JSX.Element }> = {
  pending: {
    label: "Pending",
    className: "bg-ink-100 text-ink-700 ring-ink-300/60",
    icon: <Clock className="h-3 w-3" aria-hidden="true" />,
  },
  processing: {
    label: "Processing",
    className:
      "bg-blue-50 text-blue-800 ring-blue-600/20 dark:bg-blue-500/10 dark:text-blue-300 dark:ring-blue-400/30",
    icon: <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />,
  },
  ready: {
    label: "Ready",
    className:
      "bg-emerald-50 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-400/30",
    icon: <CheckCircle2 className="h-3 w-3" aria-hidden="true" />,
  },
  failed: {
    label: "Failed",
    className:
      "bg-red-50 text-red-800 ring-red-600/20 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-400/30",
    icon: <AlertCircle className="h-3 w-3" aria-hidden="true" />,
  },
};

export function StatusBadge({ status }: { status: DocumentStatus }) {
  const s = STYLES[status];
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        s.className,
      )}
    >
      {s.icon}
      {s.label}
    </span>
  );
}
