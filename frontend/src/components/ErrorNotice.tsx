import { AlertTriangle, RotateCcw } from "lucide-react";

import { cn } from "@/lib/utils";

interface Props {
  message: string;
  requestId?: string | null;
  onRetry?(): void;
  className?: string;
}

/** User-safe error with the request id support needs to find the server logs. */
export function ErrorNotice({ message, requestId, onRetry, className }: Props) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800",
        "dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-200",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">{message}</p>
          {requestId && (
            <p className="mt-1 text-xs text-red-700 dark:text-red-300">
              Request ID: <code className="select-all break-all font-mono">{requestId}</code>
            </p>
          )}
        </div>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-red-300 bg-white px-2.5 py-1 text-xs font-medium text-red-800 hover:bg-red-100 dark:border-red-800 dark:bg-transparent dark:text-red-200 dark:hover:bg-red-900/40"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          Try again
        </button>
      )}
    </div>
  );
}
