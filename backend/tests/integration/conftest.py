"""Integration fixtures: a real Postgres (pgvector) migrated from an empty schema.

Env (see tests/conftest.py): DATABASE_URL / DATABASE_URL_SYNC. Every test here
is skipped, not errored, when the database is unreachable or not safe to reset.
"""

import os
from collections.abc import AsyncIterator, Iterator
from functools import lru_cache
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory

BACKEND_DIR = Path(__file__).resolve().parents[2]
_TABLES = "messages, conversations, chunks, documents"


def sync_dsn() -> str:
    url = make_url(os.environ["DATABASE_URL_SYNC"]).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def alembic_config() -> Config:
    # No ini file: alembic's fileConfig() would otherwise disable the app's loggers.
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


@lru_cache(maxsize=1)
def _skip_reason() -> str | None:
    try:
        with psycopg.connect(sync_dsn(), connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        return f"Postgres unreachable at DATABASE_URL_SYNC ({type(exc).__name__})"
    name = make_url(os.environ["DATABASE_URL_SYNC"]).database or ""
    allowed = os.environ.get("CI") == "true" or os.environ.get("SCHOLARIA_ALLOW_DB_RESET") == "1"
    if "test" not in name and not allowed:
        return f"refusing to reset database {name!r} (name lacks 'test'; set CI=true to force)"
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def migrated_db() -> None:
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)
    with psycopg.connect(sync_dsn(), autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {_TABLES}, alembic_version CASCADE")
    command.upgrade(alembic_config(), "head")


@pytest.fixture(autouse=True)
def clean_db(migrated_db: None) -> Iterator[None]:
    with psycopg.connect(sync_dsn(), autocommit=True) as conn:
        conn.execute(f"TRUNCATE {_TABLES} CASCADE")
    yield


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


def sql(query: str, *params: object) -> list[tuple[object, ...]]:
    """Run a query on a separate sync connection (sees only committed data)."""
    with psycopg.connect(sync_dsn(), autocommit=True) as conn:
        cur = conn.execute(query, params)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description else []
