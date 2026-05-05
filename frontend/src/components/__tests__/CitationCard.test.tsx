import { render, screen } from "@testing-library/react";
import { CitationCard } from "../CitationCard";

const baseCitation = {
  marker: 3,
  chunk_id: "c1",
  document_id: "d1",
  document_title: "Intro to ML — Chapter 4",
  page_number: 42,
  snippet: "Backpropagation applies the chain rule in reverse...",
};

describe("CitationCard", () => {
  it("renders the marker, title, page and snippet", () => {
    render(<CitationCard citation={baseCitation} />);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/Intro to ML/)).toBeInTheDocument();
    expect(screen.getByText("p.42")).toBeInTheDocument();
    expect(screen.getByText(/Backpropagation/)).toBeInTheDocument();
  });

  it("omits page label when page_number is null", () => {
    render(<CitationCard citation={{ ...baseCitation, page_number: null }} />);
    expect(screen.queryByText(/^p\./)).not.toBeInTheDocument();
  });
});
