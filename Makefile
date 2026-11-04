# Scholaria developer commands. `make help` lists them.
# Docker targets accept overrides, e.g.:  make obs PROJECT=scholaria-ci API_PORT=18000 DB_PORT=15432
SHELL := /bin/bash
.DEFAULT_GOAL := help

PROJECT ?= scholaria
COMPOSE := docker compose -p $(PROJECT)
DEV_FILES := -f docker-compose.yml
OBS_FILES := -f docker-compose.yml -f docker-compose.obs.yml
PROD_FILES := -f docker-compose.yml -f docker-compose.prod.yml
STACK_FILES := -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.obs.yml
IMAGE ?= scholaria:local
STACK_ENV := SCHOLARIA_ENV_FILE=ops/env/offline.env

# Local Postgres for tests/evals (pgvector). Each suite uses its own database.
PG ?= scholaria:scholaria@localhost:5432
TEST_DB ?= scholaria_test
EVAL_DB ?= scholaria_eval
db_env = DATABASE_URL=postgresql+asyncpg://$(PG)/$(1) DATABASE_URL_SYNC=postgresql+psycopg://$(PG)/$(1)
# Free "real" profile used by the retrieval eval (local models, no API keys).
REAL_PROFILE := EMBEDDING_PROVIDER=local EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2 \
	EMBEDDING_DIM=384 RERANKER_PROVIDER=local RERANK_MIN_SCORE=-9 LOG_LEVEL=WARNING
OFFLINE_PROFILE := LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash EMBEDDING_DIM=256 RERANKER_PROVIDER=none

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------- local stacks ----------
.PHONY: dev down seed obs prod stack logs
dev: ## Dev stack: Postgres + API (hot reload, :8000) + Vite (:5173)
	$(COMPOSE) $(DEV_FILES) up --build --renew-anon-volumes

down: ## Stop every stack of $(PROJECT) and delete its volumes
	$(COMPOSE) $(STACK_FILES) --profile obs --profile loadtest --profile dev down -v --remove-orphans

seed: ## Seed the demo corpus into the running stack
	$(COMPOSE) exec backend python -m scripts.seed_demo_docs

obs: ## Dev stack + Prometheus (:9090), Grafana (:3000), Jaeger (:16686) with tracing wired
	$(COMPOSE) $(OBS_FILES) --profile obs up -d --build

prod: ## Production image (API + SPA on :8000) with Postgres
	$(COMPOSE) $(PROD_FILES) up -d --build --wait

stack: ## Production image + observability on the offline profile (no API keys); used by loadtest
	$(STACK_ENV) $(COMPOSE) $(STACK_FILES) --profile obs up -d --build --wait

logs: ## Follow API logs
	$(COMPOSE) logs -f backend

# ---------- quality ----------
.PHONY: lint lint-backend lint-frontend fmt typecheck test test-backend test-frontend promtool actionlint check
lint: lint-backend lint-frontend ## All linters and type checks

lint-backend: ## ruff check, ruff format --check, mypy --strict
	cd backend && ruff check . && ruff format --check . && mypy app

lint-frontend: ## eslint + tsc
	cd frontend && npm run lint && npm run typecheck

fmt: ## Auto-fix backend lint and formatting
	cd backend && ruff check --fix . && ruff format .

test: test-backend test-frontend ## All unit and integration tests

test-backend: ## pytest with coverage (integration tests need Postgres, see PG/TEST_DB)
	cd backend && $(OFFLINE_PROFILE) $(call db_env,$(TEST_DB)) alembic upgrade head \
		&& $(call db_env,$(TEST_DB)) pytest --cov=app --cov-report=term-missing

test-frontend: ## vitest
	cd frontend && npm test -- --run

promtool: ## Validate Prometheus config and unit-test the SLO alert rules
	docker run --rm --entrypoint promtool -v "$(CURDIR)/ops/prometheus:/etc/prometheus:ro" \
		-w /etc/prometheus prom/prometheus:v3.5.0 check config prometheus.yml
	docker run --rm --entrypoint promtool -v "$(CURDIR)/ops/prometheus:/etc/prometheus:ro" \
		-w /etc/prometheus/tests prom/prometheus:v3.5.0 test rules slo_test.yml

actionlint: ## Lint GitHub Actions workflows
	docker run --rm -v "$(CURDIR):/repo" -w /repo rhysd/actionlint:1.7.7 -color

check: lint test promtool actionlint ## Everything CI runs except evals, e2e and docker

# ---------- evals and e2e ----------
.PHONY: eval eval-retrieval eval-generation e2e
eval: eval-retrieval eval-generation ## Retrieval + generation evals (seeds $(EVAL_DB) first)

eval-retrieval: ## Retrieval eval on the free real profile (downloads local models once)
	cd backend && $(REAL_PROFILE) $(call db_env,$(EVAL_DB)) alembic upgrade head \
		&& $(REAL_PROFILE) $(call db_env,$(EVAL_DB)) python -m scripts.seed_demo_docs \
		&& $(REAL_PROFILE) $(call db_env,$(EVAL_DB)) python -m eval.retrieval_eval

eval-generation: ## Generation eval with the deterministic fake LLM (judge runs if LLM_PROVIDER=openai)
	cd backend && $(REAL_PROFILE) LLM_PROVIDER=$${LLM_PROVIDER:-fake} $(call db_env,$(EVAL_DB)) \
		python -m eval.generation_eval

e2e: ## Playwright end-to-end tests (the config starts its own API and web servers)
	cd frontend && npm run test:e2e -- --project=chromium

# ---------- images and load ----------
.PHONY: image image-size smoke loadtest
image: ## Build the production image as $(IMAGE)
	docker build -t $(IMAGE) .

image-size: ## Print the production image size
	@docker image inspect $(IMAGE) --format '{{.Size}}' | awk '{printf "%s: %.0f MB\n", "$(IMAGE)", $$1/1e6}'

smoke: ## k6 smoke test against `make stack`
	$(STACK_ENV) $(COMPOSE) $(STACK_FILES) --profile loadtest run --rm -e RESULTS_DIR=/results k6 run -q /scripts/smoke.js

loadtest: ## k6 load test against `make stack` (tune with CHAT_VUS, BROWSE_RPS, STEADY_S)
	$(STACK_ENV) $(COMPOSE) $(STACK_FILES) --profile loadtest run --rm -e RESULTS_DIR=/results \
		-e CHAT_VUS=$${CHAT_VUS:-10} -e BROWSE_RPS=$${BROWSE_RPS:-20} -e STEADY_S=$${STEADY_S:-120} \
		k6 run -q /scripts/load.js
