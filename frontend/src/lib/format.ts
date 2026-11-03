export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(/\.0$/, "")} MB`;
}

/**
 * Snippets are raw PDF text cut at a fixed length: collapse hard line wraps and mark a
 * mid-sentence cut with an ellipsis.
 */
export function cleanSnippet(snippet: string): string {
  const text = snippet.replace(/\s+/g, " ").trim();
  return !text || /[.!?)\]"'’”…]$/.test(text) ? text : `${text}…`;
}

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["second", 60],
  ["minute", 60],
  ["hour", 24],
  ["day", 7],
  ["week", 4.35],
  ["month", 12],
  ["year", Number.POSITIVE_INFINITY],
];

/** "just now", "5 minutes ago", "yesterday", ... */
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  let delta = (then - now) / 1000;
  if (Math.abs(delta) < 45) return "just now";
  for (const [unit, size] of STEPS) {
    if (Math.abs(delta) < size) return relative.format(Math.round(delta), unit);
    delta /= size;
  }
  return "";
}

export function formatScore(score: number | null | undefined): string | null {
  if (score == null || !Number.isFinite(score)) return null;
  return Math.abs(score) >= 100 ? score.toFixed(0) : score.toPrecision(3);
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
