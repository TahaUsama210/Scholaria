from fastapi import APIRouter

from app.api.routes import chat, documents, health

# Business routes live under /api (each router carries its full prefix so route
# templates, used as metric labels, are complete); probes and /metrics stay at the root.
api_router = APIRouter()
api_router.include_router(documents.router)
api_router.include_router(chat.router)

probes_router = APIRouter()
probes_router.include_router(health.router)
