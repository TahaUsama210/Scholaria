"""Token-aware recursive chunker.

Naive char/word splits cut mid-formula on technical PDFs. We split on the
strongest semantic boundary that keeps every chunk under the token budget,
falling back to softer boundaries only when a section is too long.
"""
from __future__ import annotations

from dataclasses import dataclass

import tiktoken

# Boundaries from strongest to weakest. The chunker tries each in order: if a
# split at the current boundary still leaves a chunk over the budget, it
# recurses into that chunk with the next boundary.
SPLIT_BOUNDARIES: tuple[str, ...] = (
    "\n\n\n",   # major section break
    "\n\n",     # paragraph
    "\n",       # line
    ". ",       # sentence
    " ",        # word
    "",         # char (last resort)
)


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    token_count: int
    page_number: int | None = None


class TokenChunker:
    def __init__(
        self,
        chunk_size_tokens: int = 512,
        overlap_tokens: int = 64,
        encoding_name: str = "cl100k_base",
    ) -> None:
        if overlap_tokens >= chunk_size_tokens:
            raise ValueError("overlap_tokens must be < chunk_size_tokens")
        self.chunk_size = chunk_size_tokens
        self.overlap = overlap_tokens
        self._enc = tiktoken.get_encoding(encoding_name)

    def count_tokens(self, text: str) -> int:
        return len(self._enc.encode(text, disallowed_special=()))

    def split(self, text: str, *, page_number: int | None = None) -> list[Chunk]:
        text = text.strip()
        if not text:
            return []

        pieces = self._recursive_split(text, boundary_idx=0)
        return list(self._stitch_with_overlap(pieces, page_number=page_number))

    def _recursive_split(self, text: str, *, boundary_idx: int) -> list[str]:
        if self.count_tokens(text) <= self.chunk_size:
            return [text]
        if boundary_idx >= len(SPLIT_BOUNDARIES):
            return [text]

        sep = SPLIT_BOUNDARIES[boundary_idx]
        parts = text.split(sep) if sep else list(text)

        out: list[str] = []
        for part in parts:
            piece = part if not sep else part + sep
            if self.count_tokens(piece) <= self.chunk_size:
                out.append(piece)
            else:
                out.extend(self._recursive_split(piece, boundary_idx=boundary_idx + 1))
        return out

    def _stitch_with_overlap(
        self, pieces: list[str], *, page_number: int | None
    ) -> list[Chunk]:
        """Greedily pack pieces into chunks ≤ chunk_size, with token overlap."""
        chunks: list[Chunk] = []
        buf: list[str] = []
        buf_tokens = 0

        for piece in pieces:
            piece_tokens = self.count_tokens(piece)
            if buf_tokens + piece_tokens > self.chunk_size and buf:
                chunks.append(self._emit(buf, buf_tokens, page_number))
                buf, buf_tokens = self._tail_overlap(buf)
            buf.append(piece)
            buf_tokens += piece_tokens

        if buf:
            chunks.append(self._emit(buf, buf_tokens, page_number))
        return chunks

    def _tail_overlap(self, buf: list[str]) -> tuple[list[str], int]:
        if self.overlap == 0:
            return [], 0
        # Walk backwards collecting pieces until we hit the overlap budget.
        kept: list[str] = []
        kept_tokens = 0
        for piece in reversed(buf):
            t = self.count_tokens(piece)
            if kept_tokens + t > self.overlap:
                break
            kept.append(piece)
            kept_tokens += t
        kept.reverse()
        return kept, kept_tokens

    @staticmethod
    def _emit(buf: list[str], tokens: int, page: int | None) -> Chunk:
        return Chunk(text="".join(buf).strip(), token_count=tokens, page_number=page)
