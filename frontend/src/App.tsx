import { useState } from "react";
import { GraduationCap } from "lucide-react";

import { ChatWindow } from "./components/ChatWindow";
import { DocumentList } from "./components/DocumentList";
import { DocumentUpload } from "./components/DocumentUpload";

export default function App() {
  const [courseFilter, setCourseFilter] = useState<string>("");

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-ink-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-2">
            <GraduationCap className="h-6 w-6 text-accent" />
            <span className="text-lg font-semibold tracking-tight">Scholaria</span>
            <span className="rounded-full bg-ink-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-ink-500">
              RAG demo
            </span>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-ink-500">Course filter:</label>
            <input
              value={courseFilter}
              onChange={(e) => setCourseFilter(e.target.value)}
              placeholder="all"
              className="w-28 rounded-md border border-ink-200 bg-white px-2 py-1 text-xs"
            />
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-7xl flex-1 gap-4 px-4 py-4 overflow-hidden">
        <aside className="hidden w-72 shrink-0 flex-col gap-4 md:flex overflow-y-auto">
          <DocumentUpload />
          <section>
            <h2 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
              Library
            </h2>
            <DocumentList />
          </section>
        </aside>

        <section className="flex-1 min-w-0">
          <ChatWindow courseCode={courseFilter.trim() || null} />
        </section>
      </main>
    </div>
  );
}
