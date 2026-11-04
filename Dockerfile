# syntax=docker/dockerfile:1.7
#
# Scholaria: one production image serving the FastAPI API and the built React SPA.
# Build context is the repo root.
#
#   web      node build of frontend/ -> dist (served by FastAPI via STATIC_DIR)
#   deps     Python venv: CPU-only torch + runtime deps from backend/pyproject.toml
#   models   bakes the default local embedder/reranker and tiktoken BPE files
#   dev      deps + dev extras; used by docker-compose (source bind-mounted, --reload)
#   runtime  default target: slim, non-root, no compilers, no dev/eval extras
#
#   docker build -t scholaria .
#   docker build -t scholaria --build-arg PRELOAD_EMBEDDING_MODEL= --build-arg PRELOAD_RERANKER_MODEL= .
#
# Model baking trade-off: the two default models add ~180 MB to the image, but a Fly machine
# that scales to zero would otherwise download them from the Hugging Face Hub on the first
# request after every cold start (slow, and a hard runtime dependency on the Hub). The runtime
# sets HF_HUB_OFFLINE=1 so loading never touches the network; to use a different model either
# rebuild with the build args below or set HF_HUB_OFFLINE=0 to allow a download at runtime.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-alpine

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

# ---------- web: build the SPA ----------
FROM ${NODE_IMAGE} AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
# The SPA calls the API same-origin (VITE_API_BASE_URL unset -> "").
RUN npm run build

# ---------- deps: Python virtualenv ----------
FROM ${PYTHON_IMAGE} AS deps
ARG TORCH_VERSION=2.15.0
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
COPY --from=uv /uv /usr/local/bin/uv
RUN uv venv /opt/venv
# CPU-only torch first; otherwise sentence-transformers pulls the multi-GB CUDA build.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}"
# Dependencies only (not the project itself): this layer is reused until pyproject.toml changes.
COPY backend/pyproject.toml /src/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r /src/pyproject.toml \
    && python -c "import torch; assert not torch.cuda.is_available() and '+cpu' in torch.__version__"
# reportlab (a dev extra) renders the demo corpus PDFs, so `python -m scripts.seed_demo_docs`
# also works in the deployed image. It is small and pure Python plus Pillow, already present.
# torch pulls setuptools from the PyTorch index's older mirror; upgrade it (and the wheel and
# jaraco.context copies it vendors) past known HIGH CVEs.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "reportlab>=4.2.0" "setuptools>=84.2"
# Drop payloads inference never loads: torch C++ test binaries, headers, protoc, and
# packages' test suites (~300 MB including their bytecode).
RUN cd /opt/venv/lib/python3.12/site-packages \
    && rm -rf torch/test torch/include torch/share/cmake torch/lib/libtorchbind_test.so \
    && find torch/bin -mindepth 1 -not -name torch_shm_manager -exec rm -rf {} + \
    && find . -depth -type d -name tests -prune -exec rm -rf {} + \
    && python -c "import torch, sentence_transformers, reportlab"

# ---------- models: pre-download default local models ----------
FROM deps AS models
ARG PRELOAD_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
ARG PRELOAD_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ENV HF_HOME=/opt/hf \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken
RUN mkdir -p /opt/hf /opt/tiktoken \
    && python -c "import tiktoken; [tiktoken.get_encoding(e) for e in ('cl100k_base', 'o200k_base')]" \
    && if [ -n "$PRELOAD_EMBEDDING_MODEL" ]; then \
         python -c "import sys; from sentence_transformers import SentenceTransformer as M; M(sys.argv[1])" \
           "$PRELOAD_EMBEDDING_MODEL"; \
       fi \
    && if [ -n "$PRELOAD_RERANKER_MODEL" ]; then \
         python -c "import sys; from sentence_transformers import CrossEncoder as M; M(sys.argv[1])" \
           "$PRELOAD_RERANKER_MODEL"; \
       fi \
    && rm -rf /opt/hf/xet /opt/hf/hub/.locks

# ---------- dev: used by docker-compose ----------
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r /src/pyproject.toml --extra dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
WORKDIR /app
COPY backend/ ./
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --reload"]

# ---------- runtime: production image (default target) ----------
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    STATIC_DIR=/app/static \
    PORT=8000
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin app
COPY --from=deps /opt/venv /opt/venv
COPY --from=models --chown=app:app /opt/hf /opt/hf
COPY --from=models /opt/tiktoken /opt/tiktoken
WORKDIR /app
# Application code stays root-owned (read-only for the app user).
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/scripts ./scripts
COPY backend/app ./app
COPY --from=web /web/dist ./static
# The seed script renders the demo PDFs next to itself; only that directory is writable.
RUN python -m compileall -q app alembic scripts \
    && mkdir -p scripts/demo_docs && chown -R app:app scripts/demo_docs
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/healthz\", timeout=4)"]
# `python -m app` runs uvicorn on $PORT with proxy headers trusted from $FORWARDED_ALLOW_IPS.
# Exec form keeps it PID 1 so it receives SIGTERM/SIGINT and drains SSE streams.
CMD ["python", "-m", "app"]
