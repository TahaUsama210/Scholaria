"""HTTP behaviour that doesn't need a database (DB access is stubbed or never reached)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.routes import chat as chat_route
from app.core.config import Settings, get_settings
from app.db import session as db_session
from app.main import create_app
from app.rag.generator import StreamEvent
from tests.helpers import make_pdf, parse_sse

PDF = make_pdf(["Hello world"])


# --- upload validation --------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "content_type", "status", "detail"),
    [
        (PDF, "text/plain", 415, "Only PDF"),
        (b"not a pdf at all", "application/pdf", 415, "%PDF-"),
        (b"", "application/pdf", 400, "Empty"),
    ],
)
async def test_upload_rejects_invalid_files(
    client: AsyncClient, content: bytes, content_type: str, status: int, detail: str
) -> None:
    r = await client.post("/api/documents", files={"file": ("x.pdf", content, content_type)})
    assert r.status_code == status
    assert detail in r.json()["detail"]


async def test_upload_rejects_oversized_file_by_size(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024)
    r = await client.post("/api/documents", files={"file": ("big.pdf", big, "application/pdf")})
    assert r.status_code == 413
    assert "1 MB" in r.json()["detail"]


async def test_upload_rejects_huge_content_length_before_reading_the_body(
    client: AsyncClient,
) -> None:
    r = await client.post(
        "/api/documents",
        content=b"tiny",
        headers={"content-length": str(500 * 1024 * 1024), "content-type": "multipart/form-data"},
    )
    assert r.status_code == 413


async def test_upload_validates_form_field_lengths(client: AsyncClient) -> None:
    r = await client.post(
        "/api/documents",
        files={"file": ("x.pdf", PDF, "application/pdf")},
        data={"course_code": "C" * 65},
    )
    assert r.status_code == 422


async def test_list_validates_pagination(client: AsyncClient) -> None:
    assert (await client.get("/api/documents?limit=0")).status_code == 422
    assert (await client.get("/api/documents?limit=1000")).status_code == 422
    assert (await client.get("/api/documents?offset=-1")).status_code == 422


# --- chat -----------------------------------------------------------------------------


class _StubPipeline:
    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.calls: list[dict[str, Any]] = []

    async def stream(self, session: object, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        self.calls.append(kwargs)
        yield StreamEvent(type="context", data=[])
        if self.fail_with:
            raise self.fail_with
        yield StreamEvent(type="token", data="hi")
        yield StreamEvent(type="citations", data=[])
        yield StreamEvent(type="done", data={"answer": "hi"})


@pytest.fixture
def stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> _StubPipeline:
    stub = _StubPipeline()
    monkeypatch.setattr(chat_route, "get_pipeline", lambda: stub)
    return stub


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 2001},
        {"question": "q", "history": [{"role": "user", "content": "x"}] * 11},
        {"question": "q", "history": [{"role": "system", "content": "x"}]},
    ],
)
async def test_chat_validates_requests(client: AsyncClient, body: dict[str, Any]) -> None:
    assert (await client.post("/api/chat/stream", json=body)).status_code == 422


async def test_chat_passes_history_and_normalised_fields(
    client: AsyncClient, stub_pipeline: _StubPipeline
) -> None:
    body = {
        "question": "  Why?  ",
        "course_code": " ",
        "history": [{"role": "user", "content": "What is RRF?"}],
    }
    r = await client.post("/api/chat/stream", json=body)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert [e.event for e in parse_sse(r.text)] == ["context", "token", "citations", "done"]
    call = stub_pipeline.calls[0]
    assert call["question"] == "Why?" and call["course_code"] is None
    assert [(t.role, t.content) for t in call["history"]] == [("user", "What is RRF?")]


async def test_chat_error_event_is_user_safe(
    client: AsyncClient, stub_pipeline: _StubPipeline
) -> None:
    stub_pipeline.fail_with = RuntimeError("password=hunter2 at postgres://db")
    r = await client.post(
        "/api/chat/stream", json={"question": "q"}, headers={"x-request-id": "req-123"}
    )
    events = parse_sse(r.text)
    assert [e.event for e in events] == ["context", "error"]
    error = events[-1].data
    assert isinstance(error, dict)
    assert error["request_id"] == "req-123" == r.headers["x-request-id"]
    assert "hunter2" not in error["message"] and "postgres" not in error["message"]


async def test_chat_reports_misconfiguration_as_an_error_event(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise RuntimeError("LLM_PROVIDER=openai needs LLM_API_KEY")

    monkeypatch.setattr(chat_route, "get_pipeline", broken)
    events = parse_sse((await client.post("/api/chat/stream", json={"question": "q"})).text)
    assert [e.event for e in events] == ["error"]
    assert "LLM_API_KEY" not in events[0].data["message"]  # type: ignore[index]


# --- rate limiting ----------------------------------------------------------------------


async def test_chat_is_rate_limited_per_client(
    client: AsyncClient, stub_pipeline: _StubPipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "3/minute")
    for _ in range(3):
        assert (await client.post("/api/chat/stream", json={"question": "q"})).status_code == 200
    r = await client.post("/api/chat/stream", json={"question": "q"})
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Rate limit exceeded")
    assert r.json()["request_id"] == r.headers["x-request-id"]
    assert r.headers["retry-after"] == "60"

    # Another client IP has its own bucket.
    transport = ASGITransport(app=client._transport.app, client=("10.0.0.9", 1234))  # type: ignore[attr-defined]
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        assert (await other.post("/api/chat/stream", json={"question": "q"})).status_code == 200


async def test_upload_is_rate_limited(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_upload", "2/minute")
    files = {"file": ("x.pdf", b"", "application/pdf")}
    codes = [(await client.post("/api/documents", files=files)).status_code for _ in range(3)]
    assert codes == [400, 400, 429]


async def test_probes_and_metrics_are_not_rate_limited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "1/minute")
    for _ in range(30):
        assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/metrics")).status_code == 200


# --- probes, metrics, errors -------------------------------------------------------------


async def test_readyz_returns_503_when_the_database_is_unreachable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dead = create_async_engine(
        "postgresql+asyncpg://nobody:nothing@127.0.0.1:9/none", connect_args={"timeout": 1}
    )
    monkeypatch.setattr(db_session, "engine", dead)
    r = await client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "checks": {"database": "down"}}
    await dead.dispose()


async def test_metrics_exposition(client: AsyncClient) -> None:
    await client.get("/healthz")
    await client.get("/api/documents/not-a-uuid")  # 422, route template label
    await client.get("/definitely/not/here")
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    text = r.text
    for name in (
        "scholaria_http_requests_total",
        "scholaria_http_request_duration_seconds_bucket",
        "scholaria_rag_stage_duration_seconds_bucket",
        "scholaria_rag_requests_total",
        "scholaria_rag_retrieved_chunks_bucket",
        "scholaria_llm_tokens_total",
        "scholaria_ingest_documents_total",
        "scholaria_ingest_duration_seconds_bucket",
    ):
        assert name in text, name
    assert 'route="/healthz",status="200"' in text
    assert 'route="/api/documents/{document_id}",status="422"' in text
    assert 'route="unmatched",status="404"' in text
    assert 'scholaria_rag_requests_total{outcome="no_context"}' in text
    assert 'stage="generate_ttft"' in text


@pytest.mark.parametrize(
    ("inbound", "echoed"),
    [("abc-123.X_y", True), ("has spaces", False), ("x" * 65, False), ("", False)],
)
async def test_request_id_is_echoed_only_when_sane(
    client: AsyncClient, inbound: str, echoed: bool
) -> None:
    r = await client.get("/healthz", headers={"x-request-id": inbound} if inbound else {})
    rid = r.headers["x-request-id"]
    assert (rid == inbound) is echoed
    assert rid


async def test_unhandled_errors_return_json_with_request_id() -> None:
    app = create_app(Settings())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/boom", headers={"x-request-id": "boom-1"})
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error.", "request_id": "boom-1"}
    assert r.headers["x-request-id"] == "boom-1"


async def test_openapi_lists_the_api_routes(client: AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert {"/api/documents", "/api/documents/{document_id}", "/api/chat/stream"} <= set(paths)
    assert "/healthz" in paths and "/metrics" not in paths


# --- static SPA ---------------------------------------------------------------------------


@pytest.fixture
def spa_app(tmp_path: Path) -> FastAPI:
    (tmp_path / "index.html").write_text("<html>spa</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    return create_app(Settings(static_dir=str(tmp_path)))


async def test_spa_serving(spa_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=spa_app), base_url="http://test") as c:
        root = await c.get("/")
        assert root.status_code == 200 and "spa" in root.text
        assert root.headers["cache-control"] == "no-cache"

        deep = await c.get("/documents/123")  # client-side route
        assert deep.status_code == 200 and "spa" in deep.text

        asset = await c.get("/assets/app-abc123.js")
        assert asset.status_code == 200
        assert "immutable" in asset.headers["cache-control"]
        assert (await c.get("/favicon.svg")).status_code == 200

        assert (await c.get("/assets/missing.js")).status_code == 404
        api_miss = await c.get("/api/nope")
        assert api_miss.status_code == 404 and api_miss.json() == {"detail": "Not Found"}
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        assert (await c.get("/api/documents?limit=0")).status_code == 422  # API still wins


def test_static_dir_without_index_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"index\.html"):
        create_app(Settings(static_dir=str(tmp_path)))


async def test_metrics_port_hides_public_metrics() -> None:
    private = create_app(get_settings().model_copy(update={"metrics_port": 9091}))
    async with AsyncClient(transport=ASGITransport(app=private), base_url="http://test") as ac:
        assert (await ac.get("/metrics")).status_code == 404
        assert (await ac.get("/healthz")).status_code == 200


def test_entrypoint_starts_private_metrics_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.__main__ as entry

    calls: list[tuple[Any, ...]] = []
    settings = get_settings().model_copy(update={"metrics_port": 9091, "port": 8080})
    monkeypatch.setattr(entry, "get_settings", lambda: settings)
    monkeypatch.setattr(entry, "start_http_server", lambda port, addr: calls.append((port, addr)))
    monkeypatch.setattr(
        entry.uvicorn, "run", lambda *a, **kw: calls.append(("uvicorn", kw["port"]))
    )
    entry.main()
    assert calls == [(9091, settings.metrics_host), ("uvicorn", 8080)]


# --- review fixes: client IP, chunked uploads, model warm-up -------------------------


@pytest.mark.parametrize(
    ("header_setting", "headers", "expected"),
    [
        (None, {"fly-client-ip": "203.0.113.7"}, "127.0.0.1"),  # ignored unless configured
        ("Fly-Client-IP", {"fly-client-ip": "203.0.113.7"}, "203.0.113.7"),
        ("Fly-Client-IP", {}, "127.0.0.1"),  # falls back to the socket peer
    ],
)
def test_rate_limit_key_uses_the_configured_client_ip_header(
    monkeypatch: pytest.MonkeyPatch,
    header_setting: str | None,
    headers: dict[str, str],
    expected: str,
) -> None:
    from starlette.requests import Request

    from app.core import rate_limit

    settings = get_settings().model_copy(update={"client_ip_header": header_setting})
    monkeypatch.setattr(rate_limit, "get_settings", lambda: settings)
    raw = [(k.encode(), v.encode()) for k, v in headers.items()]
    raw.append((b"x-forwarded-for", b"6.6.6.6"))  # client-controlled, never used
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat/stream",
        "headers": raw,
        "client": ("127.0.0.1", 50000),
    }
    assert rate_limit.client_ip(Request(scope)) == expected


async def test_chunked_upload_over_the_limit_is_rejected_while_streaming() -> None:
    small = create_app(get_settings().model_copy(update={"max_upload_mb": 1}))
    sent = 0

    async def body() -> AsyncIterator[bytes]:  # no Content-Length -> chunked transfer
        nonlocal sent
        yield b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
        yield b"Content-Type: application/pdf\r\n\r\n%PDF-1.4\n"
        for _ in range(64):
            sent += 64 * 1024
            yield b"x" * (64 * 1024)
        yield b"\r\n--b--\r\n"

    async with AsyncClient(transport=ASGITransport(app=small), base_url="http://test") as ac:
        r = await ac.post(
            "/api/documents",
            content=body(),
            headers={"content-type": "multipart/form-data; boundary=b"},
        )
    assert r.status_code == 413
    assert "1 MB" in r.json()["detail"]
    assert sent < 4 * 1024 * 1024  # aborted mid-stream, not after the whole body


async def test_lifespan_warms_models_and_survives_a_warmup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    calls: list[str] = []

    def ok() -> None:
        calls.append("ok")

    def broken() -> None:
        calls.append("broken")
        raise RuntimeError("missing API key")

    for fn, expected in ((ok, ["ok"]), (broken, ["broken"])):
        calls.clear()
        monkeypatch.setattr(main, "get_pipeline", fn)
        app_ = create_app(get_settings().model_copy(update={"warm_models_on_startup": True}))
        async with app_.router.lifespan_context(app_):
            assert calls == expected
    calls.clear()
    app_ = create_app(get_settings().model_copy(update={"warm_models_on_startup": False}))
    async with app_.router.lifespan_context(app_):
        assert calls == []
