from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.rag.pipeline import NO_CONTEXT_MESSAGE, get_pipeline
from app.rag.reranker import Reranker
from app.rag.retriever import RetrievedChunk
from tests.helpers import make_pdf, parse_sse
from tests.integration.conftest import sql
from tests.integration.test_documents import upload

CORPUS = {
    "CS480": [
        "Backpropagation applies the chain rule in reverse to compute gradients of the loss.",
        "Overfitting occurs when a model memorises noise; dropout and early stopping help.",
    ],
    "CS348": [
        "A relation is in third normal form when no non-prime attribute depends transitively "
        "on a candidate key.",
        "Two-phase locking guarantees conflict-serialisable schedules.",
    ],
    "CS350": [
        "A deadlock requires the four Coffman conditions: mutual exclusion, hold and wait, "
        "no preemption and circular wait.",
        "Round-robin scheduling gives each process a fixed time quantum.",
    ],
}


@pytest.fixture
async def corpus(client: AsyncClient) -> dict[str, str]:
    ids = {}
    for course, pages in CORPUS.items():
        body = await upload(client, make_pdf(pages), name=f"{course}.pdf", course_code=course)
        ids[course] = body["document"]["id"]
    return ids


# --- retrieval --------------------------------------------------------------------------


async def test_keyword_query_returns_the_right_chunk_first(
    corpus: dict[str, str], db: AsyncSession
) -> None:
    hits = await get_pipeline().retriever.retrieve(db, "Coffman conditions for deadlock")
    assert hits, "expected hybrid retrieval to find something"
    assert str(hits[0].document_id) == corpus["CS350"]
    assert "Coffman" in hits[0].content and hits[0].page_number == 1


async def test_course_filtered_dense_search_uses_iterative_hnsw_scan(
    corpus: dict[str, str], db: AsyncSession
) -> None:
    # Without iterative scans pgvector applies the course filter *after* taking
    # ~ef_search nearest neighbours, so scoped searches can come back nearly empty.
    hits = await get_pipeline().retriever.retrieve(db, "deadlock", course_code="CS350")
    assert hits and all(str(h.document_id) == corpus["CS350"] for h in hits)
    mode = (await db.execute(text("SHOW hnsw.iterative_scan"))).scalar_one()
    assert mode == "strict_order"


async def test_sparse_search_matches_natural_language_questions(
    corpus: dict[str, str], db: AsyncSession
) -> None:
    retriever = get_pipeline().retriever
    query = "which four conditions must all hold before a deadlock can ever happen in an OS?"
    _, sparse = await retriever._dense_and_sparse(
        db, query=query, query_vec=[0.0] * 255 + [1.0], course_code=None
    )
    # Plain AND semantics would need every term in one chunk and return nothing.
    assert sparse and "Coffman" in sparse[0].content


async def test_course_filter_and_status_filter(corpus: dict[str, str], db: AsyncSession) -> None:
    retriever = get_pipeline().retriever
    hits = await retriever.retrieve(db, "deadlock", course_code="CS348")
    assert hits and {str(h.document_id) for h in hits} == {corpus["CS348"]}

    sql("UPDATE documents SET status = 'processing' WHERE id = %s", corpus["CS350"])
    hits = await retriever.retrieve(db, "Coffman conditions for deadlock")
    assert corpus["CS350"] not in {str(h.document_id) for h in hits}


# --- chat end-to-end ----------------------------------------------------------------------


async def chat(client: AsyncClient, **body: Any) -> list[Any]:
    r = await client.post("/api/chat/stream", json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


async def test_chat_streams_context_tokens_citations_done(
    client: AsyncClient, corpus: dict[str, str]
) -> None:
    events = await chat(client, question="What is the third normal form?", course_code="CS348")
    names = [e.event for e in events]
    assert names[0] == "context" and names[-2:] == ["citations", "done"]
    assert set(names[1:-2]) == {"token"}

    context, citations, done = events[0].data, events[-2].data, events[-1].data
    assert [c["marker"] for c in context] == list(range(1, len(context) + 1))
    assert all(c["document_id"] == corpus["CS348"] for c in context)
    assert citations and {c["chunk_id"] for c in citations} <= {c["chunk_id"] for c in context}
    assert "third normal form" in citations[0]["snippet"]
    assert done["answer"] == "".join(e.data for e in events if e.event == "token")
    assert done["outcome"] == "answered"
    assert "[1]" in done["answer"]


async def test_chat_without_matching_material(client: AsyncClient, corpus: dict[str, str]) -> None:
    events = await chat(client, question="What is TCP slow start?", course_code="CS456")
    assert [(e.event, e.data) for e in events] == [
        ("context", []),
        ("token", NO_CONTEXT_MESSAGE),
        ("citations", []),
        ("done", {"answer": NO_CONTEXT_MESSAGE, "outcome": "no_context"}),
    ]


async def test_chat_condenses_follow_ups(client: AsyncClient, corpus: dict[str, str]) -> None:
    history = [
        {"role": "user", "content": "What is overfitting?"},
        {"role": "assistant", "content": "Memorising noise [1]."},
    ]
    events = await chat(client, question="How do I prevent it?", history=history)
    done = events[-1].data
    assert (
        done["standalone_question"] == "How do I prevent it? (follow-up to: What is overfitting?)"
    )
    assert any("Overfitting" in c["snippet"] for c in events[0].data)


class _BrokenReranker(Reranker):
    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]:
        raise RuntimeError("cross-encoder OOM at /models/secret-path")


async def test_chat_error_path_sends_a_safe_error_event(
    client: AsyncClient, corpus: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_pipeline(), "_reranker", _BrokenReranker())
    r = await client.post("/api/chat/stream", json={"question": "deadlock"})
    events = parse_sse(r.text)
    assert [e.event for e in events] == ["error"]
    assert events[0].data["request_id"] == r.headers["x-request-id"]
    assert "secret-path" not in events[0].data["message"]


async def test_chat_rate_limit_returns_429(
    client: AsyncClient, corpus: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "2/minute")
    for _ in range(2):
        await chat(client, question="deadlock")
    r = await client.post("/api/chat/stream", json={"question": "deadlock"})
    assert r.status_code == 429
    assert "Rate limit exceeded" in r.json()["detail"]
