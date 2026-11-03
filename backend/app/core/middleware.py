"""Pure-ASGI middleware (no BaseHTTPMiddleware: it buffers poorly with SSE).

RequestContextMiddleware gives every request an id (honouring a sane inbound
X-Request-ID), echoes it back, and records HTTP metrics labelled by route
template so cardinality stays bounded. For SSE responses the duration covers
the whole stream.
"""

import re
import time

from fastapi import HTTPException
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import new_request_id, request_id_var
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _route_label(scope: Scope, status: int) -> str:
    route = scope.get("route")
    if isinstance(route, Mount):  # the SPA mount at / catches every unrouted path
        return "unmatched" if status == 404 else "static"
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get("x-request-id", "")
        rid = inbound if _REQUEST_ID_RE.match(inbound) else new_request_id()
        # Also stash it on request.state: the 500 handler runs outside this context.
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)
        status = 500  # if the app raises before responding
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["x-request-id"] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)
            method = scope["method"]
            route = _route_label(scope, status)
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
            HTTP_DURATION.labels(method=method, route=route).observe(time.perf_counter() - start)


class UploadSizeLimitMiddleware:
    """Cap upload bodies before the multipart parser spools them to disk.

    The parser writes the whole body before a route runs, so the route's own size
    check can't stop a multi-GB upload. A declared Content-Length over the limit is
    rejected up front; a chunked body (no Content-Length) is counted as it streams
    and aborted with 413 as soon as it crosses the limit.
    """

    _MULTIPART_OVERHEAD = 1024 * 1024

    def __init__(self, app: ASGIApp, *, path: str, max_bytes: int) -> None:
        self.app = app
        self.path = path
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == self.path
        ):
            await self.app(scope, receive, send)
            return

        budget = self.max_bytes + self._MULTIPART_OVERHEAD
        detail = f"PDF exceeds the {self.max_bytes // (1024 * 1024)} MB limit."
        length = Headers(scope=scope).get("content-length", "")
        if length.isdigit() and int(length) > budget:
            await JSONResponse({"detail": detail}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > budget:
                    # HTTPException passes through FastAPI's body parsing untouched and is
                    # rendered by the exception middleware as a 413.
                    raise HTTPException(status_code=413, detail=detail)
            return message

        await self.app(scope, counting_receive, send)
