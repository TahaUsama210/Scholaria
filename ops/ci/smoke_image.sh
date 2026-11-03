#!/usr/bin/env bash
# Smoke-test the production image end to end on the offline profile (no API keys):
# migrate a throwaway pgvector database, boot the image on a non-default $PORT, then probe
# health, readiness, metrics, the SPA, the documents API and a full chat SSE stream.
#
#   ops/ci/smoke_image.sh [image]          (default scholaria:local; run from the repo root)
set -euo pipefail

IMAGE=${1:-scholaria:local}
NAME=${SMOKE_NAME:-scholaria-smoke}
HOST_PORT=${SMOKE_PORT:-18080}
NET="$NAME-net"
BASE="http://127.0.0.1:$HOST_PORT"

cleanup() {
  docker rm -f "$NAME-api" "$NAME-db" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
fail() {
  echo "FAIL: $*" >&2
  docker logs --tail 50 "$NAME-api" >&2 2>&1 || true
  exit 1
}
trap cleanup EXIT
cleanup

docker network create "$NET" >/dev/null
docker run -d --name "$NAME-db" --network "$NET" \
  -e POSTGRES_USER=scholaria -e POSTGRES_PASSWORD=scholaria -e POSTGRES_DB=scholaria \
  pgvector/pgvector:pg16 >/dev/null
for _ in $(seq 1 60); do
  docker exec "$NAME-db" pg_isready -U scholaria -d scholaria >/dev/null 2>&1 && break
  sleep 1
done

app_env=(
  --network "$NET"
  --env-file ops/env/offline.env
  -e "DATABASE_URL=postgresql+asyncpg://scholaria:scholaria@$NAME-db:5432/scholaria"
  -e "DATABASE_URL_SYNC=postgresql+psycopg://scholaria:scholaria@$NAME-db:5432/scholaria"
)
docker run --rm "${app_env[@]}" "$IMAGE" alembic upgrade head

# PORT=8080 proves the image honours $PORT (the default is 8000).
start=$(date +%s)
docker run -d --name "$NAME-api" "${app_env[@]}" -e PORT=8080 \
  -p "127.0.0.1:$HOST_PORT:8080" "$IMAGE" >/dev/null
for _ in $(seq 1 90); do
  curl -fsS "$BASE/readyz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$BASE/readyz" >/dev/null || fail "/readyz never became ready"
echo "ready after $(($(date +%s) - start))s"

check() {  # check <description> <command...>
  if "${@:2}"; then echo "ok   $1"; else fail "$1"; fi
}
body() { curl -fsS "$@"; }

check "GET /healthz is ok" sh -c "curl -fsS '$BASE/healthz' | grep -q '\"ok\"'"
check "GET /readyz is 200" curl -fsS -o /dev/null "$BASE/readyz"
check "GET /metrics exposes contract metrics" \
  sh -c "curl -fsS '$BASE/metrics' | grep -q '^scholaria_rag_stage_duration_seconds_bucket'"
check "GET / serves the SPA" sh -c "curl -fsS '$BASE/' | grep -q 'id=\"root\"'"
check "GET /docs serves OpenAPI UI" curl -fsS -o /dev/null "$BASE/docs"
check "GET /api/documents is 200" curl -fsS -o /dev/null "$BASE/api/documents"
check "chat on an empty corpus ends with done" sh -c "curl -fsS -N -X POST '$BASE/api/chat/stream' \
  -H 'Content-Type: application/json' -d '{\"question\": \"What is backpropagation?\"}' \
  | grep -q '^event: done'"

# Seed through the real ingestion path, then expect a cited answer.
docker exec "$NAME-api" python -m scripts.seed_demo_docs >/dev/null
check "seeded documents are listed" sh -c "curl -fsS '$BASE/api/documents' | grep -q '\"status\":\"ready\"'"
chat=$(body -N -X POST "$BASE/api/chat/stream" -H 'Content-Type: application/json' \
  -d '{"question": "How does backpropagation compute gradients?", "course_code": "CS480"}')
check "POST /api/chat/stream ends with a done event" grep -q '^event: done' <<<"$chat"
check "answer carries citations" grep -q '^event: citations' <<<"$chat"
check "chat stream has no error event" sh -c "! grep -q '^event: error' <<'EOF'
$chat
EOF"
check "runs as a non-root user" sh -c "[ \"\$(docker exec '$NAME-api' id -u)\" != 0 ]"

# The Docker HEALTHCHECK must pass with the non-default port as well.
for _ in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$NAME-api")
  [ "$status" = healthy ] && break
  sleep 2
done
check "docker HEALTHCHECK reports healthy" test "$status" = healthy

size=$(docker image inspect "$IMAGE" -f '{{.Size}}')
echo "image $IMAGE: $((size / 1000000)) MB"
echo "smoke test passed"
