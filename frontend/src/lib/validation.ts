import { formatBytes } from "./format";

/** Mirrors the backend's upload limit (documents route, 50 MB). */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const MAX_TITLE_LENGTH = 512;
export const MAX_COURSE_CODE_LENGTH = 64;
export const MAX_QUESTION_LENGTH = 2000;

const PDF_TYPES = new Set(["application/pdf", "application/x-pdf"]);

export function isPdf(file: File): boolean {
  return PDF_TYPES.has(file.type) || file.name.toLowerCase().endsWith(".pdf");
}

/** Returns a user-facing error, or null when the file can be uploaded. */
export function validatePdf(file: File): string | null {
  if (!isPdf(file)) return `“${file.name}” isn't a PDF. Only PDF files are supported.`;
  if (file.size === 0) return `“${file.name}” is empty.`;
  if (file.size > MAX_UPLOAD_BYTES) {
    return `“${file.name}” is ${formatBytes(file.size)}; the limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`;
  }
  return null;
}

/**
 * Some platforms report an empty MIME type for .pdf files; the API checks the declared
 * type, so re-label those as application/pdf.
 */
export function withPdfMimeType(file: File): File {
  return PDF_TYPES.has(file.type) ? file : new File([file], file.name, { type: "application/pdf" });
}

/** Course codes are matched exactly by the API, so normalise "cs 480 " → "CS 480". */
export function normalizeCourseCode(raw: string): string {
  return raw.trim().replace(/\s+/g, " ").toUpperCase();
}

export function titleFromFilename(name: string): string {
  return name.replace(/\.pdf$/i, "").replace(/[_]+/g, " ").trim();
}
