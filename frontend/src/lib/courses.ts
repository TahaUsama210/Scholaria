import type { DocumentRead } from "@/types/api";

export interface CourseOption {
  code: string;
  /** Documents in this course that are ready to search. */
  readyCount: number;
}

/** Distinct course codes in the library, sorted, with ready-document counts. */
export function deriveCourses(documents: readonly DocumentRead[]): CourseOption[] {
  const byCode = new Map<string, number>();
  for (const doc of documents) {
    if (!doc.course_code) continue;
    byCode.set(doc.course_code, (byCode.get(doc.course_code) ?? 0) + (doc.status === "ready" ? 1 : 0));
  }
  return [...byCode.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([code, readyCount]) => ({ code, readyCount }));
}

/** Demo corpus courses (shown as example prompts on an empty chat). */
export const EXAMPLE_QUESTIONS: { course: string; label: string; question: string }[] = [
  {
    course: "CS480",
    label: "Machine Learning",
    question: "How does backpropagation use the chain rule to compute gradients?",
  },
  {
    course: "CS348",
    label: "Database Systems",
    question: "What is the difference between 2NF and 3NF?",
  },
  {
    course: "CS350",
    label: "Operating Systems",
    question: "What happens when a process triggers a page fault?",
  },
  {
    course: "CS456",
    label: "Computer Networks",
    question: "How does TCP congestion control react to packet loss?",
  },
];
