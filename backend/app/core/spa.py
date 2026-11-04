"""Serve the built frontend (vite dist) with client-side-routing fallback."""

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# Paths that belong to the API: a miss there must stay a 404, never index.html.
_SERVER_PREFIXES = {"api", "healthz", "readyz", "metrics", "docs", "redoc", "openapi.json"}


def _is_client_route(path: str) -> bool:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return True
    # A missing file with an extension (e.g. a stale /assets/x.js) is a real 404.
    return parts[0] not in _SERVER_PREFIXES and "." not in parts[-1]


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not _is_client_route(path):
                raise
            response = await super().get_response("index.html", scope)

        normalised = path.replace("\\", "/")
        if normalised.startswith("assets/") and response.status_code == 200:
            # Vite fingerprints everything under assets/.
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        elif response.media_type == "text/html":
            response.headers["cache-control"] = "no-cache"
        return response
