"""Per-client-IP rate limits (slowapi). Probes and /metrics carry no limit.

Limits are read from settings at request time so they can be tuned via env.
Behind a proxy, set CLIENT_IP_HEADER to the header the proxy *overwrites* with the
caller's address (Fly-Client-IP on Fly). Don't rely on X-Forwarded-For: with
FORWARDED_ALLOW_IPS="*" uvicorn uses its leftmost entry, which any client can forge
to get a fresh bucket per request.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import get_settings


def client_ip(request: Request) -> str:
    header = get_settings().client_ip_header
    if header:
        value = request.headers.get(header, "").strip()
        if value:
            return value
    return get_remote_address(request)


def chat_limit() -> str:
    return get_settings().rate_limit_chat


def upload_limit() -> str:
    return get_settings().rate_limit_upload


def _make_limiter() -> Limiter:
    settings = get_settings()
    return Limiter(
        key_func=client_ip,
        enabled=settings.rate_limit_enabled,
        storage_uri=settings.rate_limit_storage_uri,
        headers_enabled=False,
    )


limiter = _make_limiter()
