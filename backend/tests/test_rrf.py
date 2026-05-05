from uuid import uuid4

from app.rag.retriever import HybridRetriever, RetrievedChunk


def _chunk(score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_title="t",
        page_number=1,
        content="x",
        score=score,
    )


def test_rrf_promotes_chunks_appearing_in_both_lists() -> None:
    shared = _chunk()
    dense = [_chunk(), shared, _chunk()]
    sparse = [shared, _chunk(), _chunk()]

    fused = HybridRetriever._rrf(dense, sparse, k=60)
    assert fused[0].chunk_id == shared.chunk_id


def test_rrf_handles_empty_lists() -> None:
    assert HybridRetriever._rrf([], [], k=60) == []


def test_rrf_preserves_unique_results() -> None:
    a, b, c = _chunk(), _chunk(), _chunk()
    fused = HybridRetriever._rrf([a], [b, c], k=60)
    assert {x.chunk_id for x in fused} == {a.chunk_id, b.chunk_id, c.chunk_id}
