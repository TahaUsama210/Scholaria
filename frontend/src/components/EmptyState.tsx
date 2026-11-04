import { BookOpenText, Sparkles, Upload } from "lucide-react";

import { EXAMPLE_QUESTIONS } from "@/lib/courses";

interface Props {
  libraryEmpty: boolean;
  onAsk(question: string): void;
  onUpload(): void;
}

export function EmptyState({ libraryEmpty, onAsk, onUpload }: Props) {
  return (
    <div className="mx-auto flex min-h-full max-w-2xl flex-col justify-center gap-6 px-1 py-8">
      <div className="text-center">
        <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-accent/10 text-accent-text">
          <BookOpenText className="h-6 w-6" aria-hidden="true" />
        </div>
        <h2 className="text-xl font-semibold tracking-tight text-ink-900">
          Ask anything about your course materials
        </h2>
        <p className="mx-auto mt-1.5 max-w-md text-sm text-ink-600">
          Answers stream in as they're written, with numbered citations that link to the exact
          passage and page they came from.
        </p>
      </div>

      {libraryEmpty && (
        <div className="card flex flex-col items-start gap-3 border-dashed p-4 sm:flex-row sm:items-center">
          <p className="flex-1 text-sm text-ink-700">
            Your library is empty. Upload lecture notes, slides or a textbook chapter (PDF) to
            start asking questions.
          </p>
          <button type="button" onClick={onUpload} className="btn-primary shrink-0">
            <Upload className="h-4 w-4" aria-hidden="true" />
            Upload a PDF
          </button>
        </div>
      )}

      <section aria-labelledby="examples-heading">
        <h3 id="examples-heading" className="section-title mb-2 flex items-center gap-1.5 px-1">
          <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          Try an example
        </h3>
        <ul className="grid gap-2 sm:grid-cols-2">
          {EXAMPLE_QUESTIONS.map((ex) => (
            <li key={ex.question}>
              <button
                type="button"
                onClick={() => onAsk(ex.question)}
                className="card group flex h-full w-full flex-col items-start gap-1 p-3 text-left transition-colors hover:border-accent/50 hover:bg-accent/5"
              >
                <span className="text-xs font-medium text-ink-500">
                  {ex.course} · {ex.label}
                </span>
                <span className="text-sm text-ink-800 group-hover:text-ink-900">{ex.question}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
