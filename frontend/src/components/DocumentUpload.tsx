import { useRef, useState } from "react";
import { Upload, Loader2 } from "lucide-react";
import { useUploadDocument } from "@/hooks/useDocuments";

export function DocumentUpload() {
  const upload = useUploadDocument();
  const inputRef = useRef<HTMLInputElement>(null);
  const [courseCode, setCourseCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  const onPick = async (file: File) => {
    setError(null);
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files are supported.");
      return;
    }
    try {
      await upload.mutateAsync({
        file,
        title: file.name.replace(/\.pdf$/i, ""),
        courseCode: courseCode.trim() || undefined,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    }
  };

  return (
    <div className="card p-4">
      <h3 className="text-sm font-semibold text-ink-800">Upload a PDF</h3>
      <p className="mt-1 text-xs text-ink-500">
        Lecture notes or textbook chapters. Up to 50&nbsp;MB.
      </p>

      <div className="mt-3 flex gap-2">
        <input
          value={courseCode}
          onChange={(e) => setCourseCode(e.target.value)}
          placeholder="Course code (optional)"
          className="flex-1 rounded-md border border-ink-200 bg-white px-2 py-1.5 text-xs"
        />
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void onPick(f);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={upload.isPending}
        className="btn-primary mt-3 w-full"
      >
        {upload.isPending ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Uploading…
          </>
        ) : (
          <>
            <Upload className="mr-2 h-4 w-4" />
            Choose PDF
          </>
        )}
      </button>

      {error && (
        <p className="mt-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
