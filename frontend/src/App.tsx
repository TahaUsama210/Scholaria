import { useCallback, useEffect, useMemo, useState } from "react";
import { GraduationCap, Library, Moon, Sun } from "lucide-react";

import { ChatWindow } from "./components/ChatWindow";
import { CourseFilter } from "./components/CourseFilter";
import { Drawer } from "./components/Drawer";
import { UPLOAD_BUTTON_ID } from "./components/DocumentUpload";
import { LibraryPanel } from "./components/LibraryPanel";
import { useDocuments } from "./hooks/useDocuments";
import { BREAKPOINTS, useMediaQuery } from "./hooks/useMediaQuery";
import { useTheme } from "./hooks/useTheme";
import { deriveCourses } from "./lib/courses";

/** Stop a PDF dropped outside the drop zone from navigating away from the app. */
function usePreventWindowFileDrop() {
  useEffect(() => {
    const block = (e: DragEvent) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", block);
    window.addEventListener("drop", block);
    return () => {
      window.removeEventListener("dragover", block);
      window.removeEventListener("drop", block);
    };
  }, []);
}

export default function App() {
  const { data } = useDocuments();
  const desktop = useMediaQuery(BREAKPOINTS.md);
  const { theme, toggle: toggleTheme } = useTheme();
  const [selectedCourse, setSelectedCourse] = useState<string | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  usePreventWindowFileDrop();

  const docs = useMemo(() => data?.items ?? [], [data]);
  const courses = useMemo(() => deriveCourses(docs), [docs]);
  const courseCodes = useMemo(() => courses.map((c) => c.code), [courses]);
  // A deleted course silently falls back to "All courses".
  const courseCode =
    selectedCourse && courseCodes.includes(selectedCourse) ? selectedCourse : null;

  const requestUpload = useCallback(() => {
    if (!desktop) setLibraryOpen(true);
    // Wait a frame so the drawer's contents are rendered and focusable.
    requestAnimationFrame(() => document.getElementById(UPLOAD_BUTTON_ID)?.focus());
  }, [desktop]);

  useEffect(() => {
    if (desktop) setLibraryOpen(false);
  }, [desktop]);

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <a
        href="#chat-main"
        className="sr-only z-50 rounded-md bg-surface px-3 py-2 text-sm font-medium focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to chat
      </a>
      <header className="shrink-0 border-b border-ink-200 bg-surface">
        <div className="mx-auto flex max-w-[96rem] items-center gap-2 px-3 py-2.5 sm:gap-3 sm:px-4">
          {!desktop && (
            <button
              type="button"
              onClick={() => setLibraryOpen(true)}
              className="icon-btn -ml-1"
              aria-label={`Open library (${docs.length} documents)`}
              aria-haspopup="dialog"
            >
              <Library className="h-5 w-5" aria-hidden="true" />
            </button>
          )}
          <div className="flex min-w-0 items-center gap-2">
            <GraduationCap className="h-6 w-6 shrink-0 text-accent-text" aria-hidden="true" />
            <h1 className="text-lg font-semibold tracking-tight text-ink-900">Scholaria</h1>
            <span className="hidden rounded-full bg-ink-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-600 sm:inline">
              Course Q&amp;A
            </span>
          </div>
          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            <CourseFilter courses={courses} value={courseCode} onChange={setSelectedCourse} />
            <button
              type="button"
              onClick={toggleTheme}
              className="icon-btn"
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            >
              {theme === "dark" ? (
                <Sun className="h-5 w-5" aria-hidden="true" />
              ) : (
                <Moon className="h-5 w-5" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto flex min-h-0 w-full max-w-[96rem] flex-1 gap-4 p-2 sm:p-4">
        {desktop && (
          <aside aria-label="Library" className="w-72 shrink-0 overflow-y-auto pb-2 lg:w-80">
            <LibraryPanel courseSuggestions={courseCodes} />
          </aside>
        )}
        <main id="chat-main" className="min-w-0 flex-1">
          <ChatWindow
            courseCode={courseCode}
            libraryEmpty={data !== undefined && docs.length === 0}
            onRequestUpload={requestUpload}
          />
        </main>
      </div>

      {!desktop && (
        <Drawer open={libraryOpen} onClose={() => setLibraryOpen(false)} title="Library">
          <LibraryPanel courseSuggestions={courseCodes} />
        </Drawer>
      )}
    </div>
  );
}
