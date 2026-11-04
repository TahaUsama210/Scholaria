"""Embedding provider abstraction.

- ``openai``: OpenAI (or any OpenAI-compatible ``/v1/embeddings`` endpoint).
- ``local``: sentence-transformers on CPU; free, no API key.
- ``hash``: deterministic feature hashing; no network or model download.
  Used by tests, CI and offline demos.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache

import openai
from openai import AsyncOpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class EmbeddingDimensionError(RuntimeError):
    """The provider returned vectors whose size does not match EMBEDDING_DIM."""


class Embedder(ABC):
    dim: int

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


# --- hash ------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does",
        "for", "from", "how", "in", "into", "is", "it", "its", "of", "on", "or",
        "that", "the", "their", "then", "there", "these", "this", "to", "was", "what",
        "when", "where", "which", "who", "why", "will", "with", "you", "your",
    }
)  # fmt: skip


def _normalise_token(token: str) -> str:
    # Crude plural folding so "gradients" matches "gradient"; deterministic, no stemmer.
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def hash_tokens(text: str) -> list[str]:
    return [_normalise_token(t) for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class HashEmbedder(Embedder):
    """Signed feature hashing over word tokens, sublinear tf, L2-normalised.

    Cosine similarity between two vectors approximates bag-of-words overlap. It
    is a lexical baseline for offline use, not a semantic model.
    """

    def __init__(self, *, dim: int) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def embed_sync(self, text: str) -> list[float]:
        counts = Counter(hash_tokens(text)) or Counter({"<empty>": 1})
        vec = [0.0] * self.dim
        for token, tf in counts.items():
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "little")
            sign = -1.0 if h >> 63 else 1.0
            vec[h % self.dim] += sign * (1.0 + math.log(tf))
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:  # every bucket cancelled out; keep the vector well-defined
            vec[0], norm = 1.0, 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_sync(t) for t in texts]


# --- openai ----------------------------------------------------------------


def is_transient_error(exc: BaseException) -> bool:
    """Retry timeouts, connection errors, 429 and 5xx; never auth or bad-request errors."""
    if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in (408, 429) or exc.status_code >= 500
    return False


class OpenAIEmbedder(Embedder):
    """OpenAI-compatible embeddings, batched, transient errors retried with backoff."""

    def __init__(
        self,
        *,
        model: str,
        dim: int,
        api_key: str | None,
        base_url: str | None = None,
        batch_size: int = 256,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.dim = dim
        self.model = model
        self._batch_size = batch_size
        # max_retries=0: tenacity owns retries so they don't multiply with the SDK's.
        self._client = client or AsyncOpenAI(
            api_key=api_key or "unused", base_url=base_url, max_retries=0
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            out.extend(await self._embed_batch(list(texts[i : i + self._batch_size])))
        log.debug("openai_embed", count=len(texts), model=self.model)
        return out

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(is_transient_error),
        reraise=True,
    )
    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self.model, input=batch)
        vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        if vectors and len(vectors[0]) != self.dim:
            raise EmbeddingDimensionError(
                f"{self.model} returned {len(vectors[0])}-d vectors but EMBEDDING_DIM={self.dim}"
            )
        return vectors


# --- local -----------------------------------------------------------------


class LocalEmbedder(Embedder):
    """sentence-transformers; lazy-imports torch to keep cold start fast."""

    def __init__(self, *, model: str, dim: int) -> None:
        from sentence_transformers import SentenceTransformer

        self.dim = dim
        self._model = SentenceTransformer(model)
        actual = self._model.get_sentence_embedding_dimension()
        if actual is not None and actual != dim:
            raise EmbeddingDimensionError(
                f"{model} produces {actual}-d vectors but EMBEDDING_DIM={dim}"
            )

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [[float(x) for x in v] for v in vecs]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # Model inference is CPU-bound: keep it off the event loop.
        return await asyncio.to_thread(self._encode, list(texts))


# --- factory ---------------------------------------------------------------


def make_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    match settings.embedding_provider:
        case "hash":
            return HashEmbedder(dim=settings.embedding_dim)
        case "local":
            return LocalEmbedder(model=settings.embedding_model, dim=settings.embedding_dim)
        case "openai":
            api_key = settings.openai_api_key
            if api_key is None and settings.embedding_base_url is None:
                raise RuntimeError("EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set.")
            return OpenAIEmbedder(
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                api_key=api_key.get_secret_value() if api_key else None,
                base_url=settings.embedding_base_url,
                batch_size=settings.embedding_batch_size,
            )


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Process-wide embedder: local models are expensive to load, so share one."""
    return make_embedder(get_settings())
