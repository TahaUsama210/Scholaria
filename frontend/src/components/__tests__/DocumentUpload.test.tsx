import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";
import { makeDoc, pdfFile, renderWithClient } from "@/test/utils";
import { DocumentUpload } from "../DocumentUpload";

const fileInput = () => screen.getByLabelText("PDF file") as HTMLInputElement;
const user = () => userEvent.setup({ applyAccept: false });

describe("DocumentUpload", () => {
  it("rejects non-PDF files", async () => {
    renderWithClient(<DocumentUpload courseSuggestions={[]} />);
    await user().upload(fileInput(), pdfFile("essay.docx", 100, "application/msword"));
    expect(screen.getByRole("alert")).toHaveTextContent("“essay.docx” isn't a PDF");
    expect(screen.queryByRole("button", { name: "Upload" })).not.toBeInTheDocument();
  });

  it("rejects files over 50 MB and empty files", async () => {
    renderWithClient(<DocumentUpload courseSuggestions={[]} />);
    await user().upload(fileInput(), pdfFile("huge.pdf", 60 * 1024 * 1024));
    expect(screen.getByRole("alert")).toHaveTextContent("60 MB; the limit is 50 MB");

    await user().upload(fileInput(), pdfFile("empty.pdf", 0));
    expect(screen.getByRole("alert")).toHaveTextContent("“empty.pdf” is empty");
  });

  it("accepts a dropped PDF", () => {
    renderWithClient(<DocumentUpload courseSuggestions={[]} />);
    const zone = screen.getByRole("region", { name: "Add to library" });
    const file = pdfFile("dropped.pdf");
    fireEvent.drop(zone, { dataTransfer: { files: [file], types: ["Files"] } });
    expect(screen.getByText("dropped.pdf")).toBeInTheDocument();
  });

  it("uploads with a derived title and normalised course code", async () => {
    const spy = vi
      .spyOn(api, "uploadDocument")
      .mockResolvedValue({ document: makeDoc({ status: "pending", title: "week 1 notes" }), message: "Ingestion started." });
    renderWithClient(<DocumentUpload courseSuggestions={["CS480"]} />);
    const u = user();

    await u.upload(fileInput(), pdfFile("week_1_notes.pdf", 2048, ""));
    expect(screen.getByLabelText(/title/i)).toHaveAttribute("placeholder", "week 1 notes");
    await u.type(screen.getByLabelText(/course code/i), " cs480 ");
    await u.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const arg = spy.mock.calls[0]?.[0];
    expect(arg).toMatchObject({ title: "week 1 notes", courseCode: "CS480" });
    expect(arg?.file.type).toBe("application/pdf"); // empty MIME re-labelled for the API
    expect(await screen.findByRole("status")).toHaveTextContent("Uploaded “week 1 notes”");
  });

  it("shows the API's error message when the upload is refused", async () => {
    vi.spyOn(api, "uploadDocument").mockRejectedValue(
      new ApiError("PDF exceeds 50 MB limit.", { status: 413 }),
    );
    renderWithClient(<DocumentUpload courseSuggestions={[]} />);
    const u = user();
    await u.upload(fileInput(), pdfFile("a.pdf"));
    await u.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("PDF exceeds 50 MB limit.");
  });
});
