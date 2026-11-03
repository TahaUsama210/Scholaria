import logging
import sys
from contextvars import ContextVar
from uuid import uuid4

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def _add_request_id(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("request_id", request_id_var.get())
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit JSON in prod, pretty output in dev."""
    level = level.upper()
    # Windows consoles default to cp1252; never let a log line crash on a non-ASCII char.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_request_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: list[Processor]
    if sys.stderr.isatty():
        # ConsoleRenderer pretty-prints exc_info itself.
        renderer = [structlog.dev.ConsoleRenderer(colors=True)]
    else:
        renderer = [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]

    structlog.configure(
        processors=[*shared_processors, *renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    for noisy in ("uvicorn.access", "httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def new_request_id() -> str:
    return uuid4().hex[:12]


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
