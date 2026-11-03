import { describe, expect, it } from "vitest";

import type { ChatMessage } from "@/hooks/useChat";
import { makeCitation } from "@/test/utils";
import { answerForClipboard, citedMarkers, citedSet, splitCitations } from "../citations";

const known = (...markers: number[]) => (n: number) => markers.includes(n);

describe("splitCitations", () => {
  it("splits single and grouped markers", () => {
    expect(splitCitations("Gradients flow back [1]. Both [2, 3] agree.", known(1, 2, 3))).toEqual([
      "Gradients flow back ",
      { markers: [1], raw: "[1]" },
      ". Both ",
      { markers: [2, 3], raw: "[2, 3]" },
      " agree.",
    ]);
  });

  it("handles adjacent markers and markers at the edges", () => {
    expect(splitCitations("[1][2]", known(1, 2))).toEqual([
      { markers: [1], raw: "[1]" },
      { markers: [2], raw: "[2]" },
    ]);
  });

  it("leaves unknown markers, footnotes and non-numeric brackets as text", () => {
    const text = "See [9], [^1], [a] and [1, 9].";
    expect(splitCitations(text, known(1))).toEqual([text]);
  });

  it("returns the text untouched when there are no markers", () => {
    expect(splitCitations("plain", known(1))).toEqual(["plain"]);
  });
});

describe("citedMarkers", () => {
  it("returns distinct markers in order of appearance", () => {
    expect(citedMarkers("a [2] b [1, 2] c [3][1]")).toEqual([2, 1, 3]);
  });
});

function assistant(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: "a1",
    role: "assistant",
    content: "",
    status: "done",
    context: [],
    citations: null,
    ...overrides,
  };
}

describe("citedSet", () => {
  it("prefers the server's citations list", () => {
    const msg = assistant({ content: "x [1] [2]", citations: [makeCitation(2)] });
    expect([...(citedSet(msg) ?? [])]).toEqual([2]);
  });

  it("is unknown (null) while streaming without a citations event", () => {
    expect(citedSet(assistant({ status: "streaming", content: "x [1]" }))).toBeNull();
  });

  it("falls back to parsing a finished answer", () => {
    expect([...(citedSet(assistant({ status: "stopped", content: "x [3]" })) ?? [])]).toEqual([3]);
  });
});

describe("answerForClipboard", () => {
  it("appends a plain-text source list", () => {
    const msg = assistant({
      content: "Answer [1].",
      citations: [makeCitation(1, { document_title: "Notes", page_number: 4 })],
    });
    expect(answerForClipboard(msg)).toBe("Answer [1].\n\nSources:\n[1] Notes, p. 4");
  });

  it("returns the bare answer when nothing was retrieved", () => {
    expect(answerForClipboard(assistant({ content: "No luck." }))).toBe("No luck.");
  });
});
