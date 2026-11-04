import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { makeCitation } from "@/test/utils";
import { AnswerMarkdown } from "../answer/AnswerMarkdown";

const sources = [makeCitation(1, { document_title: "Lecture 4" }), makeCitation(2)];

describe("AnswerMarkdown", () => {
  it("turns known markers into chips labelled with their source", () => {
    render(<AnswerMarkdown content="Chain rule [1] and SGD [2]." sources={sources} />);
    const chip = screen.getByRole("button", { name: "Show source 1: Lecture 4, page 2" });
    expect(chip).toHaveTextContent("1");
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("splits grouped markers into one chip per source", () => {
    render(<AnswerMarkdown content="Both agree [1, 2]." sources={sources} />);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["1", "2"]);
  });

  it("keeps unknown markers and markers inside code as plain text", () => {
    render(<AnswerMarkdown content={"Bogus [7]. Index `arr[1]` here."} sources={sources} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText(/Bogus \[7\]/)).toBeInTheDocument();
    expect(screen.getByText("arr[1]").tagName).toBe("CODE");
  });

  it("calls onCite with the marker and marks the active chip", async () => {
    const onCite = vi.fn();
    const { rerender } = render(
      <AnswerMarkdown content="Claim [2]." sources={sources} onCite={onCite} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /source 2/ }));
    expect(onCite).toHaveBeenCalledWith(2);

    rerender(<AnswerMarkdown content="Claim [2]." sources={sources} activeMarker={2} onCite={onCite} />);
    expect(screen.getByRole("button", { name: /source 2/ })).toHaveAttribute("data-active", "true");
  });

  it("renders markdown but never raw HTML", () => {
    const { container } = render(
      <AnswerMarkdown
        content={"**Bold** <script>alert(1)</script><img src=x onerror=alert(1)>"}
        sources={[]}
      />,
    );
    expect(screen.getByText("Bold").tagName).toBe("STRONG");
    expect(container.querySelector("script, img")).toBeNull();
  });

  it("opens links in a new tab without opener and drops images", () => {
    const { container } = render(
      <AnswerMarkdown content={"[docs](https://example.com) ![diagram](https://x/y.png)"} sources={[]} />,
    );
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("diagram")).toBeInTheDocument();
  });

  it("renders LaTeX with KaTeX (loaded lazily) and keeps chips around math", async () => {
    let container = document.body;
    await act(async () => {
      ({ container } = render(
        <AnswerMarkdown content={"Loss is $L = (y - \\hat{y})^2$ [1]."} sources={sources} />,
      ));
      await import("../answer/MathMarkdown"); // let the lazy chunk resolve inside act()
    });
    await screen.findByRole("button", { name: /source 1/ });
    await vi.waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
    const math = container.querySelector(".katex") as HTMLElement;
    // KaTeX emits MathML for assistive tech alongside the visual HTML.
    expect(within(math).getByText((_, el) => el?.tagName.toLowerCase() === "math")).toBeTruthy();
  });
});
