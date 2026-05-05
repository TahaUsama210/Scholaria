from app.rag.chunker import TokenChunker


def test_short_text_is_a_single_chunk() -> None:
    chunker = TokenChunker(chunk_size_tokens=512, overlap_tokens=64)
    chunks = chunker.split("A short paragraph with one idea.")
    assert len(chunks) == 1
    assert chunks[0].text.startswith("A short paragraph")
    assert chunks[0].token_count > 0


def test_long_text_splits_under_budget() -> None:
    chunker = TokenChunker(chunk_size_tokens=64, overlap_tokens=8)
    text = "\n\n".join(f"Paragraph {i}. " + "lorem ipsum dolor sit amet " * 20 for i in range(8))
    chunks = chunker.split(text)
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 64


def test_overlap_is_respected() -> None:
    chunker = TokenChunker(chunk_size_tokens=40, overlap_tokens=10)
    text = " ".join(f"word{i}" for i in range(200))
    chunks = chunker.split(text)
    # Adjacent chunks should share at least one token of overlap.
    assert len(chunks) >= 2
    for a, b in zip(chunks, chunks[1:], strict=False):
        a_tokens = set(a.text.split())
        b_tokens = set(b.text.split())
        assert a_tokens & b_tokens, "expected overlap between adjacent chunks"


def test_overlap_must_be_smaller_than_chunk() -> None:
    import pytest

    with pytest.raises(ValueError):
        TokenChunker(chunk_size_tokens=10, overlap_tokens=10)
