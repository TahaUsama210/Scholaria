from alembic import command

from app.db import session as db_session
from tests.integration.conftest import alembic_config, sql


def test_upgrade_from_empty_schema_creates_the_expected_objects() -> None:
    # The session fixture dropped every table and ran `alembic upgrade head`.
    assert sql("SELECT version_num FROM alembic_version") == [("0001",)]
    tables = {r[0] for r in sql("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {"documents", "chunks", "conversations", "messages"} <= tables

    assert sql(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
    ) == [("vector(256)",)]
    # content_tsv is a STORED generated column.
    assert sql(
        "SELECT attgenerated FROM pg_attribute "
        "WHERE attrelid = 'chunks'::regclass AND attname = 'content_tsv'"
    ) == [("s",)]
    indexes = {r[0]: r[1] for r in sql("SELECT indexname, indexdef FROM pg_indexes")}
    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_chunks_embedding_hnsw"]
    assert "USING gin (content_tsv)" in indexes["ix_chunks_content_tsv"]
    # Deleting a document cascades to its chunks in the database.
    assert sql(
        "SELECT confdeltype FROM pg_constraint "
        "WHERE conrelid = 'chunks'::regclass AND contype = 'f'"
    ) == [("c",)]


async def test_downgrade_and_upgrade_round_trip() -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    tables = {r[0] for r in sql("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert not {"documents", "chunks"} & tables
    command.upgrade(cfg, "head")
    assert sql("SELECT version_num FROM alembic_version") == [("0001",)]
    # Pooled asyncpg connections cached plans for the old tables.
    await db_session.engine.dispose()
