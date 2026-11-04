import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeCitation } from "@/test/utils";
import { CitationCard } from "../CitationCard";

const citation = makeCitation(3, {
  document_title: "Intro to ML — Chapter 4",
  page_number: 42,
  snippet: "Backpropagation applies the chain rule\nin reverse, layer by",
  score: 0.8312,
});

describe("CitationCard", () => {
  it("renders the marker, title, page and snippet", () => {
    render(<CitationCard citation={citation} />);
    expect(screen.getByRole("heading", { name: "Source 3: Intro to ML — Chapter 4" })).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Page 42")).toBeInTheDocument();
    // Hard PDF line wraps collapse; a mid-sentence cut gets an ellipsis.
    expect(screen.getByText("Backpropagation applies the chain rule in reverse, layer by…")).toBeInTheDocument();
  });

  it("omits the page when page_number is null", () => {
    render(<CitationCard citation={{ ...citation, page_number: null }} />);
    expect(screen.queryByText(/^Page /)).not.toBeInTheDocument();
  });

  it("expands to show the full excerpt and relevance score", async () => {
    render(<CitationCard citation={citation} />);
    const toggle = screen.getByRole("button", { name: /show details/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Relevance")).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Relevance")).toBeInTheDocument();
    expect(screen.getByText("0.831")).toBeInTheDocument();
  });

  it("is highlighted and expanded when active", () => {
    render(<CitationCard citation={citation} active />);
    const card = screen.getByRole("article");
    expect(card).toHaveAttribute("aria-current", "true");
    expect(card).toHaveClass("ring-2");
    expect(screen.getByRole("button", { name: /hide details/i })).toHaveAttribute("aria-expanded", "true");
  });

  it("labels cited and uncited sources once known", () => {
    const { rerender } = render(<CitationCard citation={citation} cited />);
    expect(screen.getByText("Cited")).toBeInTheDocument();
    rerender(<CitationCard citation={citation} cited={false} />);
    expect(screen.getByText("Not cited")).toBeInTheDocument();
  });
});
