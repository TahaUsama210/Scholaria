"""Embedding provider abstraction.

OpenAI is the default (best quality/$ ratio at the time of writing).
sentence-transformers is the local fallback for offline dev and air-gapped demos.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class Embedder(ABC):
    dim: int

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class OpenAIEmbedder(Embedder):
    """OpenAI embeddings — batched, retried with exponential backoff."""

    def __init__(self, *, model: str, dim: int, api_key: str) -> None:
        self.dim = dim
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key)

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # OpenAI accepts up to 2048 inputs per call; chunk defensively at 256.
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):
            batch = list(texts[i : i + 256])
            resp = await self._client.embeddings.create(model=self.model, input=batch)
            out.extend(d.embedding for d in resp.data)
        log.debug("openai_embed", count=len(texts), model=self.model)
        return out


class LocalEmbedder(Embedder):
    """sentence-transformers fallback. Lazy-imports torch to keep cold start fast."""

    def __init__(self, *, model: str, dim: int) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self.dim = dim
        self._model = SentenceTransformer(model)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vecs]


def make_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    if settings.embedding_provider == "openai":
        if settings.openai_api_key is None:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set."
            )
        return OpenAIEmbedder(
            model=settings.embedding_model,
            dim=settings.embedding_dim,
            api_key=settings.openai_api_key.get_secret_value(),
        )
    return LocalEmbedder(model=settings.embedding_model, dim=settings.embedding_dim)
