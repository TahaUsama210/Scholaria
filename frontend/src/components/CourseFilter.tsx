import { useId } from "react";
import { ChevronDown } from "lucide-react";

import type { CourseOption } from "@/lib/courses";

interface Props {
  courses: readonly CourseOption[];
  value: string | null;
  onChange(code: string | null): void;
}

const ALL = "";

/** Scopes retrieval to one course; options come from the course codes in the library. */
export function CourseFilter({ courses, value, onChange }: Props) {
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="hidden text-xs font-medium text-ink-600 sm:block">
        Course
      </label>
      <div className="relative">
        <select
          id={id}
          aria-label="Course"
          value={value ?? ALL}
          onChange={(e) => onChange(e.target.value === ALL ? null : e.target.value)}
          className="h-9 max-w-[9.5rem] appearance-none truncate rounded-lg border border-ink-200 bg-surface py-1 pl-2.5 pr-8 text-sm text-ink-900 shadow-sm hover:border-ink-300 sm:max-w-[12rem]"
        >
          <option value={ALL}>All courses</option>
          {courses.map((c) => (
            <option key={c.code} value={c.code}>
              {c.code}
            </option>
          ))}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-500"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
