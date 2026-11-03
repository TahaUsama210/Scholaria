"""Container entrypoint: ``python -m app`` serves the API on $PORT (default 8000).

Runs uvicorn with forwarded-header support so per-IP rate limits see the real
client behind a proxy (trust is limited to FORWARDED_ALLOW_IPS).
"""

import uvicorn
from prometheus_client import start_http_server

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    if settings.metrics_port is not None:
        # Daemon thread over the same default registry; the public app hides /metrics.
        start_http_server(settings.metrics_port, addr=settings.metrics_host)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        # SSE streams can be long-lived; don't let them block shutdown forever.
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
