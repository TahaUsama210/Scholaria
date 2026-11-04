"""Shared test configuration.

The suite always runs on the offline provider profile (forced below, so a CI
job env that names real providers cannot leak in):
LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash EMBEDDING_DIM=256 RERANKER_PROVIDER=none.

Unit tests need nothing else. Integration tests (tests/integration, marker
``integration``) need Postgres with pgvector, addressed by:

    DATABASE_URL       postgresql+asyncpg://USER:PASS@HOST:PORT/DB   (app runtime)
    DATABASE_URL_SYNC  postgresql+psycopg://USER:PASS@HOST:PORT/DB   (alembic)

defaulting to the ``scholaria_test`` database on localhost:5432. They skip
cleanly when it is unreachable. They DROP and re-create the schema, so they
refuse to run unless the database name contains "test", or CI=true, or
SCHOLARIA_ALLOW_DB_RESET=1.
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

# Must happen before any `app` import: settings are read once and cached.
os.environ.update(
    {
        "LLM_PROVIDER": "fake",
        "EMBEDDING_PROVIDER": "hash",
        "EMBEDDING_DIM": "256",
        "RERANKER_PROVIDER": "none",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "",
        "STATIC_DIR": "",
    }
)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://scholaria:scholaria@localhost:5432/scholaria_test"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql+psycopg://scholaria:scholaria@localhost:5432/scholaria_test"
)

from app.core.rate_limit import limiter
from app.main import app


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """Rate-limit buckets are process-global; give every test a clean slate."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
