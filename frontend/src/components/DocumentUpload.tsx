import { useId, useRef, useState, type DragEvent, type FormEvent } from "react";
import { FileText, Loader2, UploadCloud, X } from "lucide-react";

import { useUploadDocument } from "@/hooks/useDocuments";
import { ApiError } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  MAX_COURSE_CODE_LENGTH,
  MAX_TITLE_LENGTH,
  MAX_UPLOAD_BYTES,
  normalizeCourseCode,
  titleFromFilename,
  validatePdf,
  withPdfMimeType,
} from "@/lib/validation";

/** Focus target for "Upload a PDF" calls to action elsewhere in the app. */
export const UPLOAD_BUTTON_ID = "upload-choose-file";

interface Props {
  /** Existing course codes, offered as suggestions. */
  courseSuggestions: readonly string[];
}

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files");

export function DocumentUpload({ courseSuggestions }: Props) {
  const upload = useUploadDocument();
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [courseCode, setCourseCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const ids = { heading: useId(), title: useId(), course: useId(), list: useId(), hint: useId() };

  const choose = (files: FileList | null) => {
    const picked = files ? Array.from(files) : [];
    const first = picked[0];
    if (!first) return;
    setNotice(picked.length > 1 ? "One file at a time — using the first one." : null);
    const problem = validatePdf(first);
    setError(problem);
    setFile(problem ? null : first);
  };

  const clear = () => {
    setFile(null);
    setTitle("");
    setError(null);
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file || upload.isPending) return;
    setError(null);
    setNotice(null);
    const code = normalizeCourseCode(courseCode);
    try {
      const { document } = await upload.mutateAsync({
        file: withPdfMimeType(file),
        title: title.trim() || titleFromFilename(file.name),
        courseCode: code || undefined,
      });
      setNotice(
        document.status === "ready"
          ? `“${document.title}” is already in your library.`
          : `Uploaded “${document.title}”. Indexing usually takes a few seconds.`,
      );
      setCourseCode(code);
      setFile(null);
      setTitle("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed. Please try again.");
    }
  };

  const dragHandlers = {
    onDragEnter: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current += 1;
      setDragging(true);
    },
    onDragOver: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: () => {
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    },
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      choose(e.dataTransfer.files);
    },
  };

  return (
    <section aria-labelledby={ids.heading} className="card p-4" {...dragHandlers}>
      <h2 id={ids.heading} className="text-sm font-semibold text-ink-900">
        Add to library
      </h2>

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        aria-label="PDF file"
        className="hidden"
        onChange={(e) => {
          choose(e.target.files);
          e.target.value = ""; // allow re-selecting the same file
        }}
      />

      {!file ? (
        <div
          className={cn(
            "mt-3 flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-3 py-5 text-center transition-colors",
            dragging ? "border-accent bg-accent/5" : "border-ink-200",
          )}
        >
          <UploadCloud
            className={cn("h-7 w-7", dragging ? "text-accent-text" : "text-ink-400")}
            aria-hidden="true"
          />
          <p className="text-sm text-ink-700">
            {dragging ? "Drop to add this PDF" : "Drag a PDF here, or"}
          </p>
          <button
            id={UPLOAD_BUTTON_ID}
            type="button"
            onClick={() => inputRef.current?.click()}
            className="btn-secondary px-3 py-1.5"
            aria-describedby={ids.hint}
          >
            Choose PDF
          </button>
          <p id={ids.hint} className="text-xs text-ink-500">
            Lecture notes, slides or textbook chapters · up to {formatBytes(MAX_UPLOAD_BYTES)}
          </p>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="mt-3 space-y-3" aria-label="Upload details">
          <div className="flex items-center gap-2 rounded-lg border border-ink-200 bg-ink-50 p-2">
            <FileText className="h-5 w-5 shrink-0 text-accent-text" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink-900" title={file.name}>
                {file.name}
              </p>
              <p className="text-xs text-ink-500">{formatBytes(file.size)}</p>
            </div>
            <button
              type="button"
              onClick={clear}
              disabled={upload.isPending}
              className="icon-btn h-8 w-8"
              aria-label={`Remove ${file.name}`}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          <div>
            <label htmlFor={ids.title} className="label">
              Title <span className="font-normal text-ink-500">(optional)</span>
            </label>
            <input
              id={ids.title}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={titleFromFilename(file.name)}
              maxLength={MAX_TITLE_LENGTH}
              className="field"
            />
          </div>

          <div>
            <label htmlFor={ids.course} className="label">
              Course code <span className="font-normal text-ink-500">(optional)</span>
            </label>
            <input
              id={ids.course}
              value={courseCode}
              onChange={(e) => setCourseCode(e.target.value)}
              onBlur={() => setCourseCode((c) => normalizeCourseCode(c))}
              placeholder="e.g. CS480"
              maxLength={MAX_COURSE_CODE_LENGTH}
              list={ids.list}
              autoComplete="off"
              className="field uppercase placeholder:normal-case"
            />
            <datalist id={ids.list}>
              {courseSuggestions.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </div>

          <button type="submit" disabled={upload.isPending} className="btn-primary w-full">
            {upload.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Uploading…
              </>
            ) : (
              <>
                <UploadCloud className="h-4 w-4" aria-hidden="true" />
                Upload
              </>
            )}
          </button>
        </form>
      )}

      {error && (
        <p
          role="alert"
          className="mt-3 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-200"
        >
          {error}
        </p>
      )}
      <p role="status" className={cn("text-xs text-ink-600", notice && "mt-3")}>
        {notice}
      </p>
    </section>
  );
}
