import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Loader2 } from "lucide-react";

import type { ChatMessage } from "@/hooks/useChat";
import { cn } from "@/lib/utils";

interface Props {
  message: ChatMessage;
}

export function MessageBubble({ message }: Props) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-4 py-3 shadow-sm",
          isUser
            ? "bg-accent text-accent-fg"
            : "bg-white border border-ink-200 text-ink-900",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <div className="prose-answer text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content || (message.isStreaming ? "" : "_(empty response)_")}
            </ReactMarkdown>
            {message.isStreaming && (
              <div className="mt-1 flex items-center gap-2 text-xs text-ink-400">
                <Loader2 className="h-3 w-3 animate-spin" />
                <span className="animate-pulse-soft">streaming…</span>
              </div>
            )}
            {message.error && (
              <p className="mt-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700">
                {message.error}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
