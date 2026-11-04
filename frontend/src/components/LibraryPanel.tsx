import { DocumentList } from "./DocumentList";
import { DocumentUpload } from "./DocumentUpload";

export function LibraryPanel({ courseSuggestions }: { courseSuggestions: readonly string[] }) {
  return (
    <div className="flex flex-col gap-4">
      <DocumentUpload courseSuggestions={courseSuggestions} />
      <DocumentList />
    </div>
  );
}
