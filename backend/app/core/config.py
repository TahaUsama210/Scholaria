from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # --- server (used by `python -m app`) ---
    host: str = "0.0.0.0"  # noqa: S104 - container entrypoint binds all interfaces
    port: int = 8000
    # Comma-separated proxy IPs trusted for X-Forwarded-For/-Proto. With "*" uvicorn takes
    # the *leftmost* X-Forwarded-For entry, which the client controls, so never key rate
    # limits on it: set CLIENT_IP_HEADER to the header your edge proxy overwrites instead.
    forwarded_allow_ips: str = "127.0.0.1"
    # Header holding the real client IP, set (not appended) by a trusted edge proxy,
    # e.g. "Fly-Client-IP" on Fly.io. Unset = the socket peer address.
    client_ip_header: str | None = None
    # Built frontend (vite dist) served at / when set.
    static_dir: str | None = None
    # When set, `python -m app` serves Prometheus metrics on this port instead of
    # /metrics on the public app port (e.g. Fly scrapes 9091 over the private network).
    metrics_port: int | None = Field(None, gt=0, lt=65536)
    # Bind address for METRICS_PORT; "::" for dual-stack (Fly's private network is IPv6).
    metrics_host: str = "0.0.0.0"  # noqa: S104
    # Load the embedder/reranker during startup instead of on the first request, which
    # would otherwise block the event loop for seconds on a cold start.
    warm_models_on_startup: bool = True

    # --- database ---
    database_url: str = Field("postgresql+asyncpg://scholaria:scholaria@db:5432/scholaria")
    database_url_sync: str = Field("postgresql+psycopg://scholaria:scholaria@db:5432/scholaria")
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_connect_timeout_seconds: float = 5.0
    readyz_timeout_seconds: float = 3.0

    # --- LLM: "openai" = any OpenAI-compatible endpoint (OpenAI, Groq, Ollama, ...) ---
    llm_provider: Literal["openai", "fake"] = "openai"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 60.0
    # Ask the endpoint for token usage on streamed responses (stream_options.include_usage).
    llm_stream_usage: bool = True

    # --- embeddings ---
    embedding_provider: Literal["openai", "local", "hash"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = Field(1536, gt=0)
    embedding_base_url: str | None = None
    embedding_batch_size: int = Field(256, gt=0)

    # --- reranker ---
    reranker_provider: Literal["local", "cohere", "none"] = "local"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    cohere_api_key: SecretStr | None = None

    # --- retrieval tuning ---
    dense_top_k: int = Field(20, gt=0)
    sparse_top_k: int = Field(20, gt=0)
    rrf_k: int = Field(60, gt=0)
    rerank_top_n: int = Field(5, gt=0)
    # Refuse (no-context answer) when the best reranked chunk scores below this.
    # Units are the reranker's own: raw logits for the local cross-encoder (eval-tuned
    # value: -9), 0..1 for Cohere. Unset = never refuse on score.
    rerank_min_score: float | None = None
    # pgvector >= 0.8: keep scanning the HNSW index until filtered queries (course,
    # status) have enough rows; without it the filter applies after ~ef_search
    # candidates and course-scoped searches can come back nearly empty. "off" for
    # older pgvector.
    hnsw_iterative_scan: Literal["off", "strict_order", "relaxed_order"] = "strict_order"

    # --- ingestion ---
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64
    max_upload_mb: int = Field(50, gt=0)
    # A pending/processing document untouched for this long is treated as abandoned
    # (e.g. the server restarted mid-ingest) and may be re-ingested on re-upload.
    ingest_stale_after_seconds: int = 900

    # --- rate limiting (per client IP) ---
    rate_limit_enabled: bool = True
    rate_limit_storage_uri: str = "memory://"
    rate_limit_chat: str = "20/minute"
    rate_limit_upload: str = "10/minute"

    # --- tracing: exports only when an OTLP/HTTP endpoint is configured ---
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "scholaria-api"

    @field_validator(
        "static_dir",
        "llm_base_url",
        "llm_api_key",
        "openai_api_key",
        "embedding_base_url",
        "cohere_api_key",
        "otel_exporter_otlp_endpoint",
        "rerank_min_score",
        "metrics_port",
        "client_ip_header",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        # `KEY=` lines copied from .env.example mean "unset", not "empty string".
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        level = v.upper()
        if level not in _LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(_LOG_LEVELS)}")
        return level

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def resolved_llm_api_key(self) -> str | None:
        """LLM_API_KEY, falling back to OPENAI_API_KEY."""
        key = self.llm_api_key or self.openai_api_key
        return key.get_secret_value() if key else None

    @property
    def effective_rerank_min_score(self) -> float | None:
        """The relevance floor, or None when there is no reranker to produce comparable scores.

        With RERANKER_PROVIDER=none the "scores" are RRF fusion values (~0.01-0.03), so
        any floor tuned for a reranker would refuse everything or nothing.
        """
        return None if self.reranker_provider == "none" else self.rerank_min_score

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
