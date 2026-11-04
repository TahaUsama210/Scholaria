import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";
import { makeDoc, renderWithClient } from "@/test/utils";
import { DocumentList } from "../DocumentList";

const list = (...items: ReturnType<typeof makeDoc>[]) => ({ items, total: items.length });

describe("DocumentList", () => {
  it("shows a loading state, then the empty state", async () => {
    let resolve: (v: ReturnType<typeof list>) => void = () => undefined;
    vi.spyOn(api, "listDocuments").mockReturnValue(new Promise((r) => (resolve = r)));
    renderWithClient(<DocumentList />);

    expect(screen.getByText("Loading documents…")).toBeInTheDocument();
    resolve(list());
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
  });

  it("shows an error with a working retry", async () => {
    const spy = vi
      .spyOn(api, "listDocuments")
      .mockRejectedValueOnce(new ApiError("Can't reach the server.", { status: 0 }))
      .mockResolvedValueOnce(list(makeDoc()));
    renderWithClient(<DocumentList />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load your library.");
    expect(alert).toHaveTextContent("Can't reach the server.");
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Lecture 3 — Backpropagation")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("renders a status badge per document and failure reasons", async () => {
    vi.spyOn(api, "listDocuments").mockResolvedValue(
      list(
        makeDoc({ id: "a", title: "Ready doc" }),
        makeDoc({ id: "b", title: "Queued doc", status: "pending", page_count: 0, chunk_count: 0 }),
        makeDoc({ id: "c", title: "Busy doc", status: "processing" }),
        makeDoc({ id: "d", title: "Broken doc", status: "failed", error_message: "PDF has no extractable text." }),
      ),
    );
    renderWithClient(<DocumentList />);

    const ready = await screen.findByRole("listitem", { name: "Ready doc" });
    expect(within(ready).getByText("Ready")).toBeInTheDocument();
    expect(within(ready).getByText("12 pages · 34 chunks")).toBeInTheDocument();
    expect(within(ready).getByText("CS480")).toBeInTheDocument();
    expect(within(screen.getByRole("listitem", { name: "Queued doc" })).getByText("Pending")).toBeInTheDocument();
    expect(within(screen.getByRole("listitem", { name: "Busy doc" })).getByText("Processing")).toBeInTheDocument();
    const failed = screen.getByRole("listitem", { name: "Broken doc" });
    expect(within(failed).getByText("Failed")).toBeInTheDocument();
    expect(within(failed).getByText("PDF has no extractable text.")).toBeInTheDocument();
    expect(screen.getByText("4 documents")).toBeInTheDocument();
  });

  it("deletes only after confirmation", async () => {
    vi.spyOn(api, "listDocuments")
      .mockResolvedValueOnce(list(makeDoc({ id: "x", title: "Old notes" })))
      .mockResolvedValue(list());
    const del = vi.spyOn(api, "deleteDocument").mockResolvedValue(undefined);
    renderWithClient(<DocumentList />);
    const u = userEvent.setup();

    await u.click(await screen.findByRole("button", { name: "Delete Old notes" }));
    const confirm = screen.getByRole("group", { name: "Confirm deleting Old notes" });
    expect(confirm).toHaveTextContent("its 34 indexed passages");
    await u.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(del).not.toHaveBeenCalled();

    await u.click(screen.getByRole("button", { name: "Delete Old notes" }));
    await u.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("x"));
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
  });

  it("shows delete failures inline", async () => {
    vi.spyOn(api, "listDocuments").mockResolvedValue(list(makeDoc({ id: "x", title: "Old notes" })));
    vi.spyOn(api, "deleteDocument").mockRejectedValue(new ApiError("Document not found.", { status: 404 }));
    renderWithClient(<DocumentList />);
    const u = userEvent.setup();

    await u.click(await screen.findByRole("button", { name: "Delete Old notes" }));
    await u.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Document not found.");
  });
});
