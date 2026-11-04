import {
  forwardRef,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { ArrowUp, Square } from "lucide-react";

import { MAX_QUESTION_LENGTH } from "@/lib/validation";

interface Props {
  isStreaming: boolean;
  courseCode: string | null;
  onSubmit(question: string): void;
  onStop(): void;
}

const MAX_HEIGHT_PX = 192;

export const Composer = forwardRef<HTMLTextAreaElement, Props>(function Composer(
  { isStreaming, courseCode, onSubmit, onStop },
  forwardedRef,
) {
  const [value, setValue] = useState("");
  const inputId = useId();
  const hintId = useId();
  const localRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(forwardedRef, () => localRef.current as HTMLTextAreaElement);
  const canSend = value.trim().length > 0 && !isStreaming;
  const remaining = MAX_QUESTION_LENGTH - value.length;

  // Grow with content up to MAX_HEIGHT_PX, then scroll.
  useLayoutEffect(() => {
    const el = localRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  const submit = () => {
    if (!canSend) return;
    onSubmit(value.trim());
    setValue("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape" && isStreaming) {
      e.preventDefault();
      onStop();
    }
  };

  return (
    <form
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        submit();
      }}
      className="border-t border-ink-200 bg-surface px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 sm:px-4"
    >
      <div className="mx-auto max-w-3xl">
        <label htmlFor={inputId} className="sr-only">
          Ask a question
        </label>
        <div className="flex items-end gap-2 rounded-xl border border-ink-200 bg-surface py-1.5 pl-3 pr-1.5 shadow-sm transition-shadow focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25">
          <textarea
            id={inputId}
            ref={localRef}
            rows={1}
            value={value}
            maxLength={MAX_QUESTION_LENGTH}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about your course materials…"
            aria-describedby={hintId}
            className="max-h-48 min-h-[2.25rem] flex-1 resize-none bg-transparent py-1.5 text-[0.9375rem] leading-6 text-ink-900 placeholder:text-ink-500 focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0"
          />
          {isStreaming ? (
            <button type="button" onClick={onStop} className="btn-secondary h-9 px-3">
              <Square className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
              <span>Stop</span>
              <span className="sr-only"> generating</span>
            </button>
          ) : (
            <button type="submit" className="btn-primary h-9 w-9 p-0" disabled={!canSend} aria-label="Send">
              <ArrowUp className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>
        <p id={hintId} className="mt-1.5 flex items-center justify-between gap-3 px-1 text-xs text-ink-500">
          <span className="truncate">
            Searching <span className="font-medium text-ink-700">{courseCode ?? "all courses"}</span>
            <span className="hidden sm:inline">
              {" "}
              · Enter to send, Shift+Enter for a new line
            </span>
          </span>
          {remaining <= 200 && (
            <span className={remaining <= 0 ? "text-red-700 dark:text-red-400" : undefined}>
              {remaining} characters left
            </span>
          )}
        </p>
      </div>
    </form>
  );
});
